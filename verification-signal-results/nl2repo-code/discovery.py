from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class NL2RepoTask:
    name: str
    task_dir: str
    test_case_count: int
    test_commands: list[str]
    test_targets: list[str]
    image: str
    stage_groups: list[dict[str, object]]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


PACKAGE_FILES = {
    "setup.py",
    "pyproject.toml",
    "setup.cfg",
    "requirements.txt",
    "requirements-dev.txt",
    "requirements-test.txt",
    "tox.ini",
    "pytest.ini",
    "poetry.lock",
    "Pipfile",
    "Pipfile.lock",
    "environment.yml",
    "conda-env.yaml",
    "manifest.in",
    "MANIFEST.in",
}


def _read_json(path: Path) -> list[str]:
    return json.loads(path.read_text(encoding="utf-8"))


def group_test_targets(test_targets: list[str]) -> list[dict[str, object]]:
    grouped: dict[str, list[str]] = {}
    for target in test_targets:
        parts = Path(target).parts
        if len(parts) >= 2:
            group_key = "/".join(parts[:2]) if parts[1].endswith(".py") else parts[0]
        else:
            group_key = parts[0]
        grouped.setdefault(group_key, []).append(target)
    groups: list[dict[str, object]] = []
    for index, (group_key, targets) in enumerate(sorted(grouped.items())):
        groups.append(
            {
                "group_id": f"g{index + 1}",
                "label": group_key,
                "targets": targets,
            }
        )
    return groups


def discover_tasks(repo_root: str | Path) -> list[NL2RepoTask]:
    root = Path(repo_root)
    test_root = root / "test_files"
    tasks: list[NL2RepoTask] = []
    for task_dir in sorted(path for path in test_root.iterdir() if path.is_dir()):
        count = int((task_dir / "test_case_count.txt").read_text(encoding="utf-8").strip())
        commands = _read_json(task_dir / "test_commands.json")
        targets = _read_json(task_dir / "test_files.json")
        image = f"ghcr.io/multimodal-art-projection/nl2repobench/{task_dir.name}:1.0"
        tasks.append(
            NL2RepoTask(
                name=task_dir.name,
                task_dir=str(task_dir),
                test_case_count=count,
                test_commands=commands,
                test_targets=targets,
                image=image,
                stage_groups=group_test_targets(targets),
            )
        )
    return tasks


def load_task(repo_root: str | Path, task_name: str) -> NL2RepoTask:
    for task in discover_tasks(repo_root):
        if task.name == task_name:
            return task
    raise KeyError(f"Unknown NL2Repo task: {task_name}")
