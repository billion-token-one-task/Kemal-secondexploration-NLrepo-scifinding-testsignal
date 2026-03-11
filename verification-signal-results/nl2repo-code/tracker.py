from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from verification_signal_study.common import ensure_dir, read_jsonl, write_jsonl
from verification_signal_study.nl2repo.controller import apply_visibility, load_split_manifest
from verification_signal_study.nl2repo.validator import run_validation


FILE_EDIT_MARKERS = {"FileEditAction", "FileWriteAction", "WriteFileAction", "EditFileAction"}


def _load_any_event_log(path: str | Path) -> list[dict[str, Any]]:
    path = Path(path)
    if path.suffix == ".jsonl":
        return read_jsonl(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        if isinstance(payload.get("events"), list):
            return payload["events"]
        if isinstance(payload.get("history"), list):
            return payload["history"]
    return []


def _normalize_event(index: int, event: dict[str, Any]) -> dict[str, Any]:
    action_type = event.get("action_type") or event.get("type") or event.get("action") or event.get("event_type") or "unknown"
    file_path = event.get("file_path") or event.get("path") or event.get("filename")
    command = event.get("command") or event.get("cmd") or event.get("bash_command")
    prompt = event.get("prompt") or event.get("content") or event.get("message") or ""
    return {
        "step_index": index + 1,
        "action_type": action_type,
        "file_path": file_path,
        "command": command,
        "prompt_chars": len(prompt) if isinstance(prompt, str) else 0,
        "raw_event": event,
    }


def parse_openhands_events(path: str | Path) -> list[dict[str, Any]]:
    return [_normalize_event(index, event) for index, event in enumerate(_load_any_event_log(path))]


def is_code_edit(event: dict[str, Any]) -> bool:
    action_type = str(event.get("action_type", ""))
    file_path = str(event.get("file_path") or "")
    return (action_type in FILE_EDIT_MARKERS or "edit" in action_type.lower()) and file_path.endswith(".py")


def compute_step_state(events: list[dict[str, Any]], total_budget: int | None = None) -> list[dict[str, Any]]:
    states: list[dict[str, Any]] = []
    steps_since_code_edit = 0
    first_prompt = max(1, events[0]["prompt_chars"] if events else 1)
    for index, event in enumerate(events):
        if is_code_edit(event):
            steps_since_code_edit = 0
        else:
            steps_since_code_edit += 1
        remaining_steps = max(1, (total_budget or len(events)) - (index + 1))
        state = {
            "step_index": index + 1,
            "action_type": event.get("action_type"),
            "file_path": event.get("file_path"),
            "prompt_chars": event.get("prompt_chars", 0),
            "context_pressure": event.get("prompt_chars", 0) / first_prompt,
            "code_edit": is_code_edit(event),
            "steps_since_code_edit": steps_since_code_edit,
            "temperature": steps_since_code_edit / remaining_steps,
        }
        states.append(state)
    return states


def monitor_once(
    *,
    event_log_path: str | Path,
    workspace_root: str | Path,
    split_manifest_path: str | Path,
    regime: str,
    total_budget: int,
    repo_root: str | Path | None = None,
    task_name: str | None = None,
) -> dict[str, Any]:
    split_manifest = load_split_manifest(split_manifest_path)
    events = parse_openhands_events(event_log_path)
    states = compute_step_state(events, total_budget=total_budget)
    current_step = len(states)
    visibility = apply_visibility(workspace_root, split_manifest, regime, current_step=current_step, total_steps=total_budget)
    snapshot = {
        "current_step": current_step,
        "total_budget": total_budget,
        "visibility": visibility,
        "temperature": states[-1]["temperature"] if states else 0.0,
        "context_pressure": states[-1]["context_pressure"] if states else 1.0,
        "states": states,
    }
    if repo_root and task_name:
        visible_validation = run_validation(
            repo_root=repo_root,
            task_name=task_name,
            workspace_path=workspace_root,
            regime="C_dense_any_test",
            selected_targets=visibility["visible_files"],
            session_id="tracker-visible",
            is_final=False,
        )
        full_validation = run_validation(
            repo_root=repo_root,
            task_name=task_name,
            workspace_path=workspace_root,
            regime="C_dense_any_test",
            selected_targets=split_manifest["release_plan"]["C_visible_nonheldout"] + split_manifest["release_plan"]["heldout"],
            session_id="tracker-full",
            is_final=True,
        )
        heldout_files = split_manifest["release_plan"].get("heldout", [])
        heldout_validation = None
        if heldout_files:
            heldout_validation = run_validation(
                repo_root=repo_root,
                task_name=task_name,
                workspace_path=workspace_root,
                regime="C_dense_any_test",
                selected_targets=heldout_files,
                session_id="tracker-heldout",
                is_final=True,
            )
        snapshot["visible_validation"] = visible_validation.get("pytest", {})
        snapshot["full_validation"] = full_validation.get("pytest", {})
        snapshot["heldout_validation"] = heldout_validation.get("pytest", {}) if heldout_validation else {}
    return snapshot


def watch_trajectory(
    *,
    event_log_path: str | Path,
    workspace_root: str | Path,
    split_manifest_path: str | Path,
    regime: str,
    total_budget: int,
    output_jsonl: str | Path,
    repo_root: str | Path | None = None,
    task_name: str | None = None,
    poll_interval_sec: float = 2.0,
    max_polls: int | None = None,
) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    last_step = -1
    polls = 0
    output_jsonl = Path(output_jsonl)
    ensure_dir(output_jsonl.parent)
    while True:
        snapshot = monitor_once(
            event_log_path=event_log_path,
            workspace_root=workspace_root,
            split_manifest_path=split_manifest_path,
            regime=regime,
            total_budget=total_budget,
            repo_root=repo_root,
            task_name=task_name,
        )
        current_step = snapshot["current_step"]
        if current_step != last_step:
            snapshots.append(snapshot)
            write_jsonl(output_jsonl, snapshots)
            last_step = current_step
        polls += 1
        if current_step >= total_budget:
            break
        if max_polls is not None and polls >= max_polls:
            break
        time.sleep(poll_interval_sec)
    return snapshots
