from __future__ import annotations

from pathlib import Path

from verification_signal_study.common import StudyArm, evenly_spaced_sample
from verification_signal_study.nl2repo.discovery import discover_tasks


NL2REPO_ARMS = [
    StudyArm(
        arm_id="A_sparse_final_only",
        label="Sparse Validation",
        description="No hidden-test feedback until final submission.",
        benchmark="NL2RepoBench",
        budget={"experiment_budget": "fixed", "hidden_test_calls": 1},
        notes=["Matches end-only validation.", "Closest to official benchmark behavior."],
    ),
    StudyArm(
        arm_id="B_staged_modules",
        label="Staged Validation",
        description="Expose grouped hidden tests only at stage checkpoints.",
        benchmark="NL2RepoBench",
        budget={"experiment_budget": "fixed", "hidden_test_calls": "bounded_by_stage_count"},
    ),
    StudyArm(
        arm_id="C_dense_any_test",
        label="Dense Validation",
        description="Allow validation against any hidden-test group at any time.",
        benchmark="NL2RepoBench",
        budget={"experiment_budget": "fixed", "hidden_test_calls": "agent_controlled"},
    ),
    StudyArm(
        arm_id="D_progressive_release",
        label="Progressive Validation",
        description="Gradually unlock more hidden-test groups over time.",
        benchmark="NL2RepoBench",
        budget={"experiment_budget": "fixed", "hidden_test_calls": "progressive"},
    ),
]


def build_manifest(repo_root: str | Path, pilot_count: int = 20) -> dict[str, object]:
    tasks = discover_tasks(repo_root)
    sampled_tasks = evenly_spaced_sample(tasks, pilot_count)

    runs: list[dict[str, object]] = []
    for task in sampled_tasks:
        for arm in NL2REPO_ARMS:
            runs.append(
                {
                    "run_id": f"{task.name}__{arm.arm_id}",
                    "task_name": task.name,
                    "arm_id": arm.arm_id,
                    "test_case_count": task.test_case_count,
                    "stage_groups": task.stage_groups,
                    "image": task.image,
                }
            )

    return {
        "benchmark": "NL2RepoBench",
        "benchmark_root": str(Path(repo_root).resolve()),
        "task_count_total": len(tasks),
        "pilot_task_count": len(sampled_tasks),
        "arms": [arm.to_dict() for arm in NL2REPO_ARMS],
        "tasks": [task.to_dict() for task in sampled_tasks],
        "runs": runs,
    }
