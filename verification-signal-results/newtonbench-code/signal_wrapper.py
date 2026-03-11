from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from verification_signal_study.newtonbench.feedback import (
    evaluate_hypothesis,
    extract_function_arguments,
    render_feedback,
)
from verification_signal_study.newtonbench.verifier import render_verifier_feedback, run_verifier_feedback


VALIDATION_MODES = {
    "A_batched_dataset",
    "B_interactive_observation",
    "C_quantitative_feedback",
    "D_directional_feedback",
    "E_verifier_agent",
}


def build_explored_ranges(experiment_history: list[dict[str, Any]], hypothesis: str | None) -> dict[str, tuple[float, float]]:
    if not hypothesis:
        return {}
    tracked_arguments = set(extract_function_arguments(hypothesis))
    ranges: dict[str, tuple[float, float]] = {}
    for experiment in experiment_history:
        for key, value in experiment.items():
            if key not in tracked_arguments:
                continue
            if not isinstance(value, (int, float)):
                continue
            current = ranges.get(key)
            numeric_value = float(value)
            if current is None:
                ranges[key] = (numeric_value, numeric_value)
            else:
                ranges[key] = (min(current[0], numeric_value), max(current[1], numeric_value))
    return ranges


def wrap_experiment_output(
    *,
    benchmark_root: str | Path,
    module_name: str,
    validation_mode: str,
    experiment_results: list[Any],
    current_hypothesis: str | None,
    difficulty: str,
    law_version: str,
    experiment_history: list[dict[str, Any]],
    previous_best_r2: float | None,
    llm_callable=None,
    verifier_model_name: str | None = None,
    trajectory: list[dict[str, Any]] | None = None,
    verifier_trial_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if validation_mode not in VALIDATION_MODES:
        raise KeyError(f"Unsupported validation mode: {validation_mode}")

    payload = f"<experiment_output>\n{json.dumps(experiment_results)}\n</experiment_output>"
    explored_ranges = build_explored_ranges(experiment_history, current_hypothesis)
    evaluation: dict[str, Any] | None = None
    verifier_feedback: dict[str, Any] | None = None
    visible_feedback = ""

    if validation_mode in {"C_quantitative_feedback", "D_directional_feedback"} and current_hypothesis:
        evaluation = evaluate_hypothesis(
            benchmark_root,
            module_name,
            current_hypothesis,
            difficulty,
            law_version,
            explored_ranges=explored_ranges,
        )
        visible_feedback = render_feedback(validation_mode, evaluation, previous_best_r2)
    elif validation_mode == "E_verifier_agent":
        if current_hypothesis and llm_callable and verifier_model_name:
            verifier_feedback = run_verifier_feedback(
                llm_callable,
                verifier_model_name,
                trajectory=trajectory or [],
                current_hypothesis=current_hypothesis,
                trial_info=verifier_trial_info,
            )
            visible_feedback = render_verifier_feedback(verifier_feedback)
        else:
            visible_feedback = render_feedback(
                validation_mode,
                {
                    "verdict": "unavailable",
                    "confidence": "low",
                    "recommendation": "Provide a hypothesis and verifier model configuration to enable verifier feedback.",
                },
            )

    if visible_feedback:
        payload += "\n\n" + visible_feedback
    return {
        "payload": payload,
        "evaluation": evaluation,
        "verifier_feedback": verifier_feedback,
        "explored_ranges": explored_ranges,
    }
