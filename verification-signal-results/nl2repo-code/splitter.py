from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from verification_signal_study.common import ensure_dir, safe_slug, write_json
from verification_signal_study.nl2repo.discovery import discover_tasks, load_task


BUCKETS = ["import", "interface", "functional", "integration", "heldout"]


def bucket_for_test_path(path: str) -> str:
    lowered = path.lower()
    name = Path(path).name.lower()
    if any(token in lowered for token in ["import", "smoke", "load", "sanity", "module", "version"]):
        return "import"
    if any(token in lowered for token in ["interface", "signature", "api", "schema", "contract", "types", "protocol"]):
        return "interface"
    if any(token in lowered for token in ["integration", "e2e", "end_to_end", "workflow", "cli", "benchmark", "system", "performance"]):
        return "integration"
    if any(token in name for token in ["test_all", "full", "functional"]):
        return "functional"
    return "functional"


def ensure_task_image(image: str) -> None:
    inspected = subprocess.run(["sudo", "-n", "docker", "image", "inspect", image], capture_output=True, text=True, check=False)
    if inspected.returncode == 0:
        return
    subprocess.run(["sudo", "-n", "docker", "pull", image], check=True, capture_output=True, text=True)


def discover_hidden_test_files(image: str, targets: list[str]) -> list[str]:
    ensure_task_image(image)
    lines: list[str] = []
    for target in targets:
        command = (
            f"if [ -d /workspace/{target} ]; then find /workspace/{target} -type f | sort; "
            f"elif [ -f /workspace/{target} ]; then echo /workspace/{target}; fi"
        )
        result = subprocess.run(
            ["sudo", "-n", "docker", "run", "--rm", image, "bash", "-lc", command],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            continue
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("/workspace/"):
                line = line[len("/workspace/") :]
            lines.append(line)
    deduped = sorted(set(lines))
    return deduped


def assign_buckets(test_files: list[str], heldout_ratio: float = 0.2) -> dict[str, list[str]]:
    buckets: dict[str, list[str]] = {bucket: [] for bucket in BUCKETS}
    for path in test_files:
        bucket = bucket_for_test_path(path)
        buckets[bucket].append(path)

    non_import_interface = buckets["functional"] + buckets["integration"]
    heldout_count = int(len(non_import_interface) * heldout_ratio)
    if heldout_count > 0:
        heldout = sorted(non_import_interface)[-heldout_count:]
        buckets["heldout"] = heldout
        buckets["functional"] = [path for path in buckets["functional"] if path not in heldout]
        buckets["integration"] = [path for path in buckets["integration"] if path not in heldout]
    return buckets


def build_split_manifest(repo_root: str | Path, task_name: str, *, heldout_ratio: float = 0.2) -> dict[str, Any]:
    task = load_task(repo_root, task_name)
    hidden_test_files = discover_hidden_test_files(task.image, task.test_targets)
    buckets = assign_buckets(hidden_test_files, heldout_ratio=heldout_ratio)
    return {
        "task_name": task.name,
        "image": task.image,
        "test_targets": task.test_targets,
        "hidden_test_files": hidden_test_files,
        "buckets": buckets,
        "release_plan": {
            "A_hidden": [],
            "B_import_interface": buckets["import"] + buckets["interface"],
            "C_visible_nonheldout": buckets["import"] + buckets["interface"] + buckets["functional"] + buckets["integration"],
            "D_progressive": {
                "0.0": buckets["import"],
                "0.3": buckets["import"] + buckets["interface"],
                "0.6": buckets["import"] + buckets["interface"] + buckets["functional"],
                "0.8": buckets["import"] + buckets["interface"] + buckets["functional"] + buckets["integration"],
            },
            "heldout": buckets["heldout"],
        },
    }


def export_hidden_tests(image: str, targets: list[str], output_dir: str | Path) -> Path:
    ensure_task_image(image)
    output_dir = ensure_dir(output_dir)
    create = subprocess.run(["sudo", "-n", "docker", "create", image], capture_output=True, text=True, check=True)
    container_id = create.stdout.strip()
    try:
        for target in targets:
            destination = output_dir / target
            destination.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["sudo", "-n", "docker", "cp", f"{container_id}:/workspace/{target}", str(destination)],
                check=False,
                capture_output=True,
                text=True,
            )
    finally:
        subprocess.run(["sudo", "-n", "docker", "rm", "-f", container_id], capture_output=True, text=True, check=False)
    return output_dir


def build_all_split_manifests(
    repo_root: str | Path,
    output_dir: str | Path,
    *,
    task_names: list[str] | None = None,
    heldout_ratio: float = 0.2,
) -> list[Path]:
    ensure_dir(output_dir)
    tasks = discover_tasks(repo_root)
    selected = {task.name for task in tasks} if task_names is None else set(task_names)
    written: list[Path] = []
    for task in tasks:
        if task.name not in selected:
            continue
        manifest = build_split_manifest(repo_root, task.name, heldout_ratio=heldout_ratio)
        target = Path(output_dir) / f"{safe_slug(task.name)}.split.json"
        write_json(target, manifest)
        written.append(target)
    return written


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--task")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--heldout-ratio", type=float, default=0.2)
    args = parser.parse_args()

    if args.task:
        manifest = build_split_manifest(args.repo_root, args.task, heldout_ratio=args.heldout_ratio)
        target = Path(args.output_dir) / f"{safe_slug(args.task)}.split.json"
        write_json(target, manifest)
        print(target)
        return

    written = build_all_split_manifests(args.repo_root, args.output_dir, heldout_ratio=args.heldout_ratio)
    print(json.dumps([str(path) for path in written], indent=2))


if __name__ == "__main__":
    main()
