from __future__ import annotations

import difflib
from collections import defaultdict
from pathlib import Path
from typing import Any

from verification_signal_study.common import split_into_thirds


DOC_CLEANUP_HINTS = {"readme", "docs", "changelog", "license", "pyproject.toml", "setup.py"}


def _load_workspace_tree(root: str | Path) -> set[str]:
    root = Path(root)
    if not root.exists():
        return set()
    return {
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.parts and ".vsignal" not in path.parts
    }


def _top_level_module(path: str | None) -> str | None:
    if not path:
        return None
    parts = Path(path).parts
    if not parts:
        return None
    return parts[0]


def _jump_out_rate(states: list[dict[str, Any]], window: int = 3) -> float:
    opportunities = 0
    escapes = 0
    for index in range(window - 1, len(states) - 1):
        window_slice = states[index - window + 1 : index + 1]
        if any(step.get("code_edit", False) for step in window_slice):
            continue
        opportunities += 1
        if states[index + 1].get("code_edit", False):
            escapes += 1
    if opportunities == 0:
        return 0.0
    return escapes / opportunities


def _rewrite_ratio(old_text: str | None, new_text: str | None) -> float:
    if not old_text or not new_text:
        return 0.0
    matcher = difflib.SequenceMatcher(a=old_text.splitlines(), b=new_text.splitlines())
    return 1.0 - matcher.ratio()


def compute_nl2repo_probes(
    *,
    states: list[dict[str, Any]],
    raw_events: list[dict[str, Any]],
    workspace_root: str | Path | None = None,
    monitor_snapshots: list[dict[str, Any]] | None = None,
    total_tokens: int | None = None,
    avg_tokens_per_line: float = 6.0,
    temperature_threshold: float = 0.77,
) -> dict[str, Any]:
    total_steps = len(states)
    if total_steps == 0:
        return {
            "eta_step": 0.0,
            "pass_rate_curve": [],
            "temperature_curve": [],
        }

    final_files = _load_workspace_tree(workspace_root) if workspace_root else set()
    retained_edit_steps = 0
    code_edit_steps = 0
    repeated_files = 0
    rewrite_heavy = 0
    seen_file_edits: set[str] = set()
    module_order: list[str] = []
    seen_modules: set[str] = set()

    for state, event in zip(states, raw_events):
        file_path = event.get("file_path")
        if state.get("code_edit", False):
            code_edit_steps += 1
            if file_path in seen_file_edits:
                repeated_files += 1
            seen_file_edits.add(file_path or "")
            if file_path and file_path in final_files:
                retained_edit_steps += 1
            module_name = _top_level_module(file_path)
            if module_name and module_name not in seen_modules:
                module_order.append(module_name)
                seen_modules.add(module_name)
            rewrite = _rewrite_ratio(event.get("raw_event", {}).get("old_content"), event.get("raw_event", {}).get("new_content"))
            if rewrite > 0.5:
                rewrite_heavy += 1

    final_python_lines = 0
    if workspace_root:
        for path in Path(workspace_root).rglob("*.py"):
            if ".vsignal" in path.parts:
                continue
            final_python_lines += len(path.read_text(encoding="utf-8", errors="ignore").splitlines())

    eta_token_value = 0.0
    if total_tokens and total_tokens > 0:
        eta_token_value = (final_python_lines * avg_tokens_per_line) / total_tokens

    pass_rate_curve = []
    visible_pass_rate_curve = []
    heldout_pass_rate_curve = []
    architecture_tree_30 = set()
    if monitor_snapshots:
        one_third_index = max(0, len(monitor_snapshots) // 3 - 1)
        if workspace_root:
            architecture_tree_30 = _load_workspace_tree(workspace_root)
        for snapshot in monitor_snapshots:
            full_validation = snapshot.get("full_validation", {})
            visible_validation = snapshot.get("visible_validation", {})
            heldout_validation = snapshot.get("heldout_validation", {})
            full_total = max(1, full_validation.get("total_seen", 0) or 1)
            visible_total = max(1, visible_validation.get("total_seen", 0) or 1)
            heldout_total = max(1, heldout_validation.get("total_seen", 0) or 1)
            pass_rate_curve.append((full_validation.get("passed", 0) / full_total) if full_validation else 0.0)
            visible_pass_rate_curve.append((visible_validation.get("passed", 0) / visible_total) if visible_validation else 0.0)
            heldout_pass_rate_curve.append((heldout_validation.get("passed", 0) / heldout_total) if heldout_validation else 0.0)

    first_full_import_pass = None
    for snapshot in monitor_snapshots or []:
        visible = snapshot.get("visible_validation", {})
        total_seen = int(visible.get("total_seen", 0) or 0)
        failed = int(visible.get("failed", 0) or 0)
        errors = int(visible.get("errors", 0) or 0)
        if total_seen > 0 and failed == 0 and errors == 0:
            first_full_import_pass = snapshot.get("current_step")
            break

    cleanup_after_midpoint = 0
    if monitor_snapshots:
        for index, event in enumerate(raw_events):
            file_path = (event.get("file_path") or "").lower()
            if index < len(raw_events) // 2:
                continue
            if any(hint in file_path for hint in DOC_CLEANUP_HINTS):
                cleanup_after_midpoint += 1

    temperature_curve = [float(state.get("temperature", 0.0) or 0.0) for state in states]
    divergence_fraction = sum(1 for value in temperature_curve if value > temperature_threshold) / total_steps
    thirds = split_into_thirds(states)
    jump_out_by_segment = {segment.segment_id: _jump_out_rate(segment.items) for segment in thirds}
    final_full_pass_rate = pass_rate_curve[-1] if pass_rate_curve else 0.0
    final_visible_pass_rate = visible_pass_rate_curve[-1] if visible_pass_rate_curve else 0.0
    final_heldout_pass_rate = heldout_pass_rate_curve[-1] if heldout_pass_rate_curve else 0.0
    premature_convergence = bool(
        visible_pass_rate_curve
        and any(rate >= 0.8 for rate in visible_pass_rate_curve)
        and cleanup_after_midpoint > 0
        and final_full_pass_rate < 1.0
    )

    return {
        "eta_token": eta_token_value,
        "eta_step": (retained_edit_steps / total_steps),
        "context_pressure_index": sum(state.get("context_pressure", 1.0) for state in states) / total_steps,
        "redundancy_rate": (repeated_files / code_edit_steps) if code_edit_steps else 0.0,
        "temperature_curve": temperature_curve,
        "pass_rate_curve": pass_rate_curve,
        "visible_pass_rate_curve": visible_pass_rate_curve,
        "heldout_pass_rate_curve": heldout_pass_rate_curve,
        "module_completion_order": module_order,
        "code_rewrite_rate": (rewrite_heavy / code_edit_steps) if code_edit_steps else 0.0,
        "architecture_stability": 1.0 if not final_files else len(architecture_tree_30 & final_files) / len(final_files) if architecture_tree_30 else 0.0,
        "first_full_import_pass_step": first_full_import_pass,
        "jump_out_rate_by_segment": jump_out_by_segment,
        "divergence_rate": divergence_fraction,
        "premature_convergence": premature_convergence,
        "final_pass_rate": final_full_pass_rate,
        "final_visible_pass_rate": final_visible_pass_rate,
        "final_heldout_pass_rate": final_heldout_pass_rate,
    }
