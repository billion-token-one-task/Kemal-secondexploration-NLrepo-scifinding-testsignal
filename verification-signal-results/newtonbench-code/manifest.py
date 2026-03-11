from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from verification_signal_study.common import StudyArm, round_robin_sample
from verification_signal_study.newtonbench.discovery import discover_tasks


NEWTONBENCH_ARMS = [
    StudyArm(
        arm_id="A_batched_dataset",
        label="Batched Observations",
        description="Spend the full experiment budget up front, then analyze only.",
        benchmark="NewtonBench",
        budget={"experiment_budget": 10, "turn_budget": 10},
    ),
    StudyArm(
        arm_id="B_interactive_observation",
        label="Interactive Observation",
        description="Standard benchmark behavior: experiment, observe, revise.",
        benchmark="NewtonBench",
        budget={"experiment_budget": 10, "turn_budget": 10},
    ),
    StudyArm(
        arm_id="C_quantitative_feedback",
        label="Quantitative Feedback",
        description="After each experiment, reveal current hypothesis error if provided.",
        benchmark="NewtonBench",
        budget={"experiment_budget": 10, "turn_budget": 10},
    ),
    StudyArm(
        arm_id="D_directional_feedback",
        label="Directional Feedback",
        description="After each experiment, reveal where the current hypothesis errs most.",
        benchmark="NewtonBench",
        budget={"experiment_budget": 10, "turn_budget": 10},
    ),
]


def build_manifest(benchmark_root: str | Path, pilot_count: int = 20) -> dict[str, object]:
    tasks = discover_tasks(benchmark_root)
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for task in tasks:
        grouped[task.module].append(task.to_dict())
    sampled = round_robin_sample([grouped[module] for module in sorted(grouped)], pilot_count)

    runs: list[dict[str, object]] = []
    for task in sampled:
        for arm in NEWTONBENCH_ARMS:
            run_id = "__".join([task["module"], task["difficulty"], task["system"], task["law_version"], arm.arm_id])
            runs.append({"run_id": run_id, **task, "arm_id": arm.arm_id})

    return {
        "benchmark": "NewtonBench",
        "benchmark_root": str(Path(benchmark_root).resolve()),
        "task_count_total": len(tasks),
        "pilot_task_count": len(sampled),
        "arms": [arm.to_dict() for arm in NEWTONBENCH_ARMS],
        "tasks": sampled,
        "runs": runs,
    }
