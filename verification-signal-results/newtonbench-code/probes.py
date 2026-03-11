from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from verification_signal_study.common import split_into_thirds
from verification_signal_study.metrics import carnot_efficiency, divergence_rate, eta_step, eta_token


def _normalized_experiment_key(experiment: dict[str, Any], decimals: int = 6) -> tuple[tuple[str, float], ...]:
    normalized = []
    for key, value in sorted(experiment.items()):
        if isinstance(value, (int, float)):
            normalized.append((key, round(float(value), decimals)))
    return tuple(normalized)


def _jump_out_rate(steps: list[dict[str, Any]], window: int = 3) -> float:
    opportunities = 0
    escapes = 0
    for index in range(window - 1, len(steps) - 1):
        window_slice = steps[index - window + 1 : index + 1]
        if any(step.get("hypothesis_updated", False) for step in window_slice):
            continue
        opportunities += 1
        if steps[index + 1].get("hypothesis_updated", False):
            escapes += 1
    if opportunities == 0:
        return 0.0
    return escapes / opportunities


def _coverage_ratio(experiment_history: list[dict[str, Any]], signature_variables: list[str], bins: int = 5) -> float:
    if not experiment_history or not signature_variables:
        return 0.0
    per_var_values: dict[str, list[float]] = defaultdict(list)
    for experiment in experiment_history:
        for variable in signature_variables:
            value = experiment.get(variable)
            if isinstance(value, (int, float)):
                per_var_values[variable].append(float(value))
    if not per_var_values:
        return 0.0
    visited = set()
    for experiment in experiment_history:
        cell = []
        for variable in signature_variables:
            values = per_var_values.get(variable, [])
            value = experiment.get(variable)
            if not values or not isinstance(value, (int, float)):
                break
            low = min(values)
            high = max(values)
            if np.isclose(low, high):
                bucket = 0
            else:
                bucket = min(bins - 1, int(((float(value) - low) / (high - low)) * bins))
            cell.append(bucket)
        if len(cell) == len(signature_variables):
            visited.add(tuple(cell))
    return len(visited) / (bins ** len(signature_variables))


def compute_newtonbench_probes(
    trial_result: dict[str, Any],
    *,
    temperature_threshold: float = 0.77,
    stagnation_window: int = 3,
    correct_direction_r2: float = 0.5,
    premature_r2: float = 0.7,
) -> dict[str, Any]:
    trajectory = list(trial_result.get("trajectory", []))
    total_tokens = int(trial_result.get("total_tokens", 0) or 0)
    total_steps = len(trajectory)
    if total_steps == 0:
        return {
            "eta_token": 0.0,
            "eta_step": 0.0,
            "r2_curve": [],
            "temperature_curve": [],
        }

    best_r2_curve: list[float] = []
    current_r2_curve: list[float] = []
    improvements: list[bool] = []
    positive_deltas: list[float] = []
    negative_deltas: list[float] = []
    effective_tokens = 0
    effective_steps = 0
    repeated_experiments = 0
    all_experiments = 0
    seen_experiment_keys: set[tuple[tuple[str, float], ...]] = set()
    experiment_history: list[dict[str, Any]] = list(trial_result.get("experiment_history", []))

    previous_best = 0.0
    for step in trajectory:
        best_r2 = float(step.get("best_r2") or 0.0)
        current_r2 = float(step.get("current_r2") or 0.0)
        best_r2_curve.append(best_r2)
        current_r2_curve.append(current_r2)
        delta = best_r2 - previous_best
        improved = delta > 1e-9
        improvements.append(improved)
        if improved:
            positive_deltas.append(delta)
            effective_tokens += int(step.get("token_count") or 0)
            effective_steps += 1
        elif delta < -1e-9:
            negative_deltas.append(abs(delta))
        previous_best = max(previous_best, best_r2)

        for experiment in step.get("experiments_requested", []) or []:
            all_experiments += 1
            key = _normalized_experiment_key(experiment)
            if key in seen_experiment_keys:
                repeated_experiments += 1
            seen_experiment_keys.add(key)

    update_deltas = []
    update_frictions = []
    previous_step_best = 0.0
    for step in trajectory:
        step_best = float(step.get("best_r2") or 0.0)
        if step.get("hypothesis_updated", False):
            delta = step_best - previous_step_best
            if delta >= 0:
                update_deltas.append(delta)
            else:
                update_frictions.append(abs(delta))
        previous_step_best = max(previous_step_best, step_best)

    avg_context_pressure = float(np.mean([step.get("context_pressure", 1.0) for step in trajectory]))
    hypothesis_updates = sum(1 for step in trajectory if step.get("hypothesis_updated", False))
    temperature_curve = [float(step.get("temperature", 0.0) or 0.0) for step in trajectory]
    divergence_steps = sum(1 for value in temperature_curve if value > temperature_threshold)
    first_correct_direction = next((index + 1 for index, value in enumerate(best_r2_curve) if value > correct_direction_r2), None)

    premature_convergence = False
    for index, value in enumerate(best_r2_curve):
        if value >= premature_r2:
            later_best = max(best_r2_curve[index:], default=value)
            if (later_best - value) < 0.05 and index < (len(best_r2_curve) - 1):
                premature_convergence = True
                break

    experiment_efficiency = []
    previous_best = 0.0
    for step in trajectory:
        step_best = float(step.get("best_r2") or 0.0)
        num_experiments = len(step.get("experiments_requested", []) or [])
        if num_experiments > 0:
            experiment_efficiency.append((step_best - previous_best) / num_experiments)
        previous_best = max(previous_best, step_best)

    thirds = split_into_thirds(trajectory)
    jump_out_by_segment = {segment.segment_id: _jump_out_rate(segment.items, window=stagnation_window) for segment in thirds}

    effective_quality = best_r2_curve[-1]
    probe_summary = {
        "eta_token": eta_token(effective_quality, total_tokens),
        "effective_eta_token": eta_token(effective_quality, max(effective_tokens, 1)) if effective_tokens else 0.0,
        "eta_step": eta_step(effective_steps, total_steps),
        "carnot_efficiency": carnot_efficiency(sum(update_deltas), sum(update_deltas) + sum(update_frictions)) if (update_deltas or update_frictions) else 0.0,
        "context_pressure_index": avg_context_pressure,
        "redundancy_rate": (repeated_experiments / all_experiments) if all_experiments else 0.0,
        "r2_curve": best_r2_curve,
        "current_r2_curve": current_r2_curve,
        "experiment_efficiency_curve": experiment_efficiency,
        "exploration_coverage": _coverage_ratio(experiment_history, trial_result.get("signature_variables", [])),
        "hypothesis_iteration_frequency": hypothesis_updates / total_steps,
        "lock_in_temperature_fraction": divergence_steps / total_steps,
        "temperature_curve": temperature_curve,
        "lock_in_threshold": temperature_threshold,
        "jump_out_rate_by_segment": jump_out_by_segment,
        "first_correct_direction_step": first_correct_direction,
        "premature_convergence": premature_convergence,
        "divergence_rate": divergence_rate(improvements, patience=stagnation_window),
        "stagnation_window": stagnation_window,
    }
    return probe_summary
