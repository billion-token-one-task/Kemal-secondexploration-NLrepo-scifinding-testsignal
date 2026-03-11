from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from verification_signal_study.nl2repo.discovery import PACKAGE_FILES, NL2RepoTask, load_task


def _prepare_submission_copy(workspace_path: str | Path, task: NL2RepoTask, scratch_root: str | Path | None = None) -> Path:
    workspace = Path(workspace_path).resolve()
    root = Path(scratch_root) if scratch_root else Path(tempfile.mkdtemp(prefix="nl2repo-validate-"))
    submission = root / "submission"
    shutil.copytree(workspace, submission, dirs_exist_ok=True)

    for path in list(submission.rglob("*")):
        if path.is_file() and path.name in PACKAGE_FILES:
            path.unlink()

    for relative_target in task.test_targets:
        target = submission / relative_target
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()

    return submission


def _rewrite_pytest_command(command: str, task: NL2RepoTask, selected_targets: list[str] | None) -> str:
    if not selected_targets:
        return command
    parts = shlex.split(command)
    if not parts:
        return command
    pytest_index = None
    if parts[0] == "pytest":
        pytest_index = 0
    elif len(parts) >= 3 and parts[0].startswith("python") and parts[1] == "-m" and parts[2] == "pytest":
        pytest_index = 2
    if pytest_index is None:
        return command
    filtered: list[str] = []
    target_set = set(task.test_targets)
    prefix = parts[: pytest_index + 1]
    for token in parts[pytest_index + 1 :]:
        if token in target_set:
            continue
        filtered.append(token)
    rebuilt = [*prefix, *filtered, *selected_targets]
    return shlex.join(rebuilt)


def _build_commands(task: NL2RepoTask, selected_targets: list[str] | None) -> list[str]:
    commands: list[str] = []
    for command in task.test_commands:
        if "pytest" in command:
            commands.append(_rewrite_pytest_command(command, task, selected_targets))
        else:
            commands.append(command)
    return commands


def _parse_pytest_output(output: str) -> dict[str, Any]:
    passed = sum(int(match) for match in re.findall(r"(\d+) passed", output))
    failed = sum(int(match) for match in re.findall(r"(\d+) failed", output))
    errors = sum(int(match) for match in re.findall(r"(\d+) error", output))
    skipped = sum(int(match) for match in re.findall(r"(\d+) skipped", output))
    total_seen = passed + failed + errors + skipped
    return {
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "skipped": skipped,
        "total_seen": total_seen,
    }


def _load_state(state_dir: Path, session_id: str) -> dict[str, Any]:
    state_file = state_dir / f"{session_id}.json"
    if not state_file.exists():
        return {"released_groups": [], "history": []}
    return json.loads(state_file.read_text(encoding="utf-8"))


def _save_state(state_dir: Path, session_id: str, payload: dict[str, Any]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / f"{session_id}.json"
    state_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def resolve_targets(task: NL2RepoTask, regime: str, session_id: str, stage_id: str | None, selected_targets: list[str] | None, state_dir: str | Path) -> tuple[list[str] | None, dict[str, Any]]:
    state_root = Path(state_dir)
    if regime == "A_sparse_final_only":
        return None, {"mode": "final_only"}

    if regime == "B_staged_modules":
        if not stage_id:
            raise ValueError("stage_id is required for B_staged_modules")
        group = next((group for group in task.stage_groups if group["group_id"] == stage_id), None)
        if not group:
            raise KeyError(f"Unknown stage_id {stage_id} for task {task.name}")
        return list(group["targets"]), {"mode": "staged", "group": group}

    if regime == "C_dense_any_test":
        if selected_targets:
            return selected_targets, {"mode": "dense", "selection": "custom"}
        return task.test_targets, {"mode": "dense", "selection": "all"}

    if regime == "D_progressive_release":
        state = _load_state(state_root, session_id)
        released = list(state.get("released_groups", []))
        remaining = [group for group in task.stage_groups if group["group_id"] not in released]
        if remaining:
            next_group = remaining[0]
            released.append(next_group["group_id"])
            state["released_groups"] = released
            _save_state(state_root, session_id, state)
        allowed_groups = [group for group in task.stage_groups if group["group_id"] in released]
        flattened: list[str] = []
        for group in allowed_groups:
            flattened.extend(group["targets"])
        return flattened, {"mode": "progressive", "released_groups": released}

    raise KeyError(f"Unsupported regime: {regime}")


def run_validation(
    repo_root: str | Path,
    task_name: str,
    workspace_path: str | Path,
    regime: str,
    *,
    session_id: str = "default",
    stage_id: str | None = None,
    selected_targets: list[str] | None = None,
    is_final: bool = False,
    scratch_root: str | Path | None = None,
    state_dir: str | Path = "/tmp/nl2repo-validation-state",
) -> dict[str, Any]:
    task = load_task(repo_root, task_name)

    if regime == "A_sparse_final_only" and not is_final:
        return {
            "status": "unavailable",
            "task": asdict(task),
            "message": "Validation is disabled until final submission for this regime.",
        }

    resolved_targets, release_info = resolve_targets(task, regime, session_id, stage_id, selected_targets, state_dir)
    commands = _build_commands(task, resolved_targets)
    submission = _prepare_submission_copy(workspace_path, task, scratch_root=scratch_root)

    copy_command = "shopt -s dotglob nullglob; cp -a /submission/. /workspace/ 2>/dev/null || true"
    joined_command = " && ".join([copy_command, *commands])
    docker_command = [
        "sudo",
        "-n",
        "docker",
        "run",
        "--rm",
        "-v",
        f"{submission}:/submission:ro",
        task.image,
        "bash",
        "-lc",
        joined_command,
    ]

    result = subprocess.run(docker_command, capture_output=True, text=True, check=False)
    combined_output = "\n".join(part for part in [result.stdout, result.stderr] if part)
    parsed = _parse_pytest_output(combined_output)

    return {
        "status": "ok" if result.returncode == 0 else "failed",
        "task": asdict(task),
        "regime": regime,
        "release_info": release_info,
        "selected_targets": resolved_targets,
        "commands": commands,
        "docker_command": docker_command,
        "returncode": result.returncode,
        "pytest": parsed,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "output": combined_output,
    }
