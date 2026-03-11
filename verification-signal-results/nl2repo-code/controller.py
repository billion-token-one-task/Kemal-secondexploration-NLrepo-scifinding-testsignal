from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from verification_signal_study.common import ensure_dir, read_json


NL2REPO_VISIBILITY_MODES = {
    "A_sparse_final_only",
    "B_staged_modules",
    "C_dense_any_test",
    "D_progressive_release",
}


def load_split_manifest(path: str | Path) -> dict[str, Any]:
    return read_json(path)


def install_hidden_tests(source_root: str | Path, workspace_root: str | Path, split_manifest: dict[str, Any]) -> Path:
    source_root = Path(source_root)
    workspace_root = Path(workspace_root)
    hidden_root = ensure_dir(workspace_root / ".vsignal" / "hidden_tests")
    for relative_path in split_manifest.get("hidden_test_files", []):
        source = source_root / relative_path
        if not source.exists():
            continue
        destination = hidden_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return hidden_root


def determine_visible_files(split_manifest: dict[str, Any], regime: str, *, current_step: int | None = None, total_steps: int | None = None) -> list[str]:
    if regime not in NL2REPO_VISIBILITY_MODES:
        raise KeyError(f"Unsupported regime: {regime}")

    buckets = split_manifest["buckets"]
    if regime == "A_sparse_final_only":
        return []
    if regime == "B_staged_modules":
        return list(buckets["import"] + buckets["interface"])
    if regime == "C_dense_any_test":
        return list(buckets["import"] + buckets["interface"] + buckets["functional"] + buckets["integration"])

    progress = 0.0
    if current_step is not None and total_steps:
        progress = max(0.0, min(1.0, current_step / total_steps))
    visible = list(buckets["import"])
    if progress >= 0.3:
        visible.extend(buckets["interface"])
    if progress >= 0.6:
        visible.extend(buckets["functional"])
    if progress >= 0.8:
        visible.extend(buckets["integration"])
    return sorted(set(visible))


def _remove_hidden_targets(workspace_root: Path, all_hidden_files: list[str]) -> None:
    for relative_path in all_hidden_files:
        target = workspace_root / relative_path
        if target.is_file() or target.is_symlink():
            target.unlink()
    candidate_dirs = sorted({str(Path(path).parent) for path in all_hidden_files}, key=len, reverse=True)
    for relative_dir in candidate_dirs:
        directory = workspace_root / relative_dir
        if directory.exists() and directory.is_dir():
            try:
                if not any(directory.iterdir()):
                    directory.rmdir()
            except OSError:
                pass


def apply_visibility(
    workspace_root: str | Path,
    split_manifest: dict[str, Any],
    regime: str,
    *,
    current_step: int | None = None,
    total_steps: int | None = None,
) -> dict[str, Any]:
    workspace_root = Path(workspace_root)
    hidden_root = workspace_root / ".vsignal" / "hidden_tests"
    visible_files = determine_visible_files(split_manifest, regime, current_step=current_step, total_steps=total_steps)
    hidden_files = split_manifest.get("hidden_test_files", [])

    _remove_hidden_targets(workspace_root, hidden_files)
    for relative_path in visible_files:
        source = hidden_root / relative_path
        if not source.exists():
            continue
        target = workspace_root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    visible_buckets = []
    buckets = split_manifest["buckets"]
    for bucket_name, paths in buckets.items():
        if bucket_name == "heldout":
            continue
        if any(path in visible_files for path in paths):
            visible_buckets.append(bucket_name)

    return {
        "regime": regime,
        "current_step": current_step,
        "total_steps": total_steps,
        "visible_files": visible_files,
        "visible_buckets": visible_buckets,
        "heldout_files": split_manifest["buckets"].get("heldout", []),
    }


def write_workspace_bundle(output_dir: str | Path, split_manifest: dict[str, Any], regime: str) -> Path:
    output_dir = ensure_dir(output_dir)
    vsignal_dir = ensure_dir(output_dir / ".vsignal")
    (vsignal_dir / "split_manifest.json").write_text(json.dumps(split_manifest, indent=2), encoding="utf-8")
    script_path = output_dir / "tools" / "apply_test_visibility.py"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(
        """#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
from verification_signal_study.nl2repo.controller import apply_visibility


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace-root', default='.')
    parser.add_argument('--regime', required=True)
    parser.add_argument('--current-step', type=int)
    parser.add_argument('--total-steps', type=int)
    args = parser.parse_args()
    manifest = json.loads((Path(args.workspace_root) / '.vsignal' / 'split_manifest.json').read_text(encoding='utf-8'))
    result = apply_visibility(args.workspace_root, manifest, args.regime, current_step=args.current_step, total_steps=args.total_steps)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
""",
        encoding="utf-8",
    )
    script_path.chmod(0o755)
    guide = output_dir / "TEST_VISIBILITY_PLAN.md"
    guide.write_text(
        "\n".join(
            [
                f"# Test Visibility Plan ({regime})",
                "",
                "This bundle expects hidden tests under `.vsignal/hidden_tests/`.",
                "Run `tools/apply_test_visibility.py` whenever the OpenHands step count changes.",
            ]
        ),
        encoding="utf-8",
    )
    return output_dir
