from __future__ import annotations

import importlib
import inspect
import re
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import numpy as np

from verification_signal_study.common import geometric_mean


HYPOTHESIS_PATTERN = re.compile(r"<hypothesis>(.*?)</hypothesis>", re.DOTALL)
FUNCTION_PATTERN = re.compile(r"(def\s+discovered_law\s*\(.*?(?=\ndef|\Z))", re.DOTALL)
SIGNATURE_PATTERN = re.compile(r"def\s+discovered_law\s*\((.*?)\)\s*:")


def _sanitize_function_block(content: str) -> str:
    lines = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith('<') and stripped.endswith('>'):
            break
        lines.append(line)
    return '\n'.join(lines).strip()


def extract_hypothesis(response_text: str) -> str | None:
    tagged = HYPOTHESIS_PATTERN.findall(response_text)
    if tagged:
        content = tagged[-1].strip()
        function_match = FUNCTION_PATTERN.findall(content)
        return _sanitize_function_block(function_match[-1]) if function_match else _sanitize_function_block(content)
    function_match = FUNCTION_PATTERN.findall(response_text)
    if function_match:
        return _sanitize_function_block(function_match[-1])
    return None


def extract_function_arguments(function_str: str) -> list[str]:
    match = SIGNATURE_PATTERN.search(function_str)
    if not match:
        return []
    arguments = [argument.strip() for argument in match.group(1).split(",") if argument.strip()]
    return arguments


def _load_module(benchmark_root: str | Path, module_name: str):
    root = str(Path(benchmark_root).resolve())
    if root not in sys.path:
        sys.path.insert(0, root)
    return importlib.import_module(f"modules.{module_name}")


def _sample_like(values: np.ndarray, low: float, high: float, count: int) -> np.ndarray:
    if count <= 0:
        return values
    if np.isclose(low, high):
        return np.full(count, low, dtype=float)
    if low > 0 and high > 0 and (high / low) >= 10:
        return np.exp(np.random.uniform(np.log(low), np.log(high), count))
    return np.random.uniform(low, high, count)


def _override_test_data(
    original_test_data: dict[str, np.ndarray],
    parameter_mapping: dict[str, str],
    explored_ranges: dict[str, tuple[float, float]] | None,
) -> dict[str, np.ndarray]:
    if not explored_ranges:
        return original_test_data
    first_key = list(parameter_mapping.values())[0]
    count = len(original_test_data[first_key])
    overridden: dict[str, np.ndarray] = dict(original_test_data)
    for parameter_name, test_key in parameter_mapping.items():
        if parameter_name not in explored_ranges:
            continue
        low, high = explored_ranges[parameter_name]
        overridden[test_key] = _sample_like(np.asarray(original_test_data[test_key]), low, high, count)
    return overridden


def _safe_r2(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    ss_res = float(np.sum((y_true - y_pred) ** 2))
    ss_tot = float(np.sum((y_true - np.mean(y_true)) ** 2))
    if ss_tot <= 0:
        return 1.0 if np.allclose(y_true, y_pred) else 0.0
    return 1.0 - (ss_res / ss_tot)


def _direction_from_error(mean_signed_relative_error: float) -> str:
    return "overestimated" if mean_signed_relative_error >= 0 else "underestimated"


@contextmanager
def patched_numeric_feedback(
    module_package,
    details: dict[str, Any],
    *,
    explored_ranges: dict[str, tuple[float, float]] | None = None,
):
    core_module = importlib.import_module(f"{module_package.__name__}.core")
    original = core_module.shared_evaluate_law

    def capture_shared_evaluate(
        llm_function_str,
        gt_law,
        test_data,
        parameter_mapping,
        param_description,
        judge_model_name="ignored",
        trial_info=None,
        symbolic_check=True,
    ):
        from modules.common.evaluation import add_necessary_imports, calculate_rmsle

        local_scope: dict[str, Any] = {}
        exec(add_necessary_imports(llm_function_str), {}, local_scope)
        llm_function = local_scope["discovered_law"]

        effective_test_data = _override_test_data(test_data, parameter_mapping, explored_ranges)
        first_key = list(parameter_mapping.values())[0]
        num_points = len(effective_test_data[first_key])
        y_true = np.array(
            [gt_law(*[effective_test_data[param_key][i] for param_key in parameter_mapping.values()]) for i in range(num_points)]
        )
        y_pred = np.array(
            [llm_function(*[effective_test_data[param_key][i] for param_key in parameter_mapping.values()]) for i in range(num_points)]
        )

        finite_mask = np.isfinite(y_true) & np.isfinite(y_pred)
        y_true = y_true[finite_mask]
        y_pred = y_pred[finite_mask]
        if len(y_true) == 0:
            raise ValueError("No finite comparison points available for hypothesis evaluation")

        eps = 1e-12
        relative_error = (y_pred - y_true) / np.maximum(np.abs(y_true), eps)
        abs_relative_error = np.abs(relative_error)
        log_error = np.abs(np.log1p(np.abs(y_pred)) - np.log1p(np.abs(y_true)))
        rmsle = calculate_rmsle(y_true, y_pred)
        r2 = _safe_r2(y_true, y_pred)

        slices: list[dict[str, Any]] = []
        parameter_summaries: dict[str, dict[str, Any]] = {}
        for parameter_name, test_key in parameter_mapping.items():
            values = np.asarray(effective_test_data[test_key])[finite_mask]
            q25 = float(np.quantile(values, 0.25))
            q75 = float(np.quantile(values, 0.75))
            buckets = {
                "low": values <= q25,
                "mid": (values > q25) & (values < q75),
                "high": values >= q75,
            }
            bucket_rows: list[dict[str, Any]] = []
            for label, mask in buckets.items():
                if not np.any(mask):
                    continue
                row = {
                    "parameter": parameter_name,
                    "region": label,
                    "lower_bound": float(values[mask].min()),
                    "upper_bound": float(values[mask].max()),
                    "mean_signed_relative_error": float(np.mean(relative_error[mask])),
                    "mean_abs_relative_error": float(np.mean(abs_relative_error[mask])),
                    "mean_abs_log_error": float(np.mean(log_error[mask])),
                    "bias_direction": _direction_from_error(float(np.mean(relative_error[mask]))),
                    "bias_percent": float(abs(np.mean(relative_error[mask])) * 100.0),
                }
                slices.append(row)
                bucket_rows.append(row)
            if bucket_rows:
                parameter_summaries[parameter_name] = {
                    "parameter": parameter_name,
                    "mean_abs_relative_error": float(np.mean([row["mean_abs_relative_error"] for row in bucket_rows])),
                    "mean_abs_log_error": float(np.mean([row["mean_abs_log_error"] for row in bucket_rows])),
                    "max_bias_percent": float(max(row["bias_percent"] for row in bucket_rows)),
                }
        worst_slice = max(slices, key=lambda item: item["mean_abs_relative_error"]) if slices else None
        worst_parameter = None
        if parameter_summaries:
            worst_parameter = max(parameter_summaries.values(), key=lambda item: item["mean_abs_relative_error"])
        details.update(
            {
                "rmsle": float(rmsle),
                "r2": float(r2),
                "sample_count": int(len(y_true)),
                "worst_slice": worst_slice,
                "worst_parameter": worst_parameter,
                "parameter_summaries": parameter_summaries,
                "geometric_mean_abs_log_error": float(geometric_mean(log_error + 1e-12)),
                "mean_abs_relative_error": float(np.mean(abs_relative_error)),
                "mean_signed_relative_error": float(np.mean(relative_error)),
            }
        )
        return {
            "rmsle": float(rmsle),
            "exact_accuracy": 0.0,
            "symbolic_equivalent": False,
            "symbolic_msg": None,
            "error": None,
        }

    core_module.shared_evaluate_law = capture_shared_evaluate
    try:
        yield
    finally:
        core_module.shared_evaluate_law = original


def evaluate_hypothesis(
    benchmark_root: str | Path,
    module_name: str,
    hypothesis: str,
    difficulty: str,
    law_version: str,
    *,
    explored_ranges: dict[str, tuple[float, float]] | None = None,
) -> dict[str, Any]:
    module_package = _load_module(benchmark_root, module_name)
    details: dict[str, Any] = {"hypothesis": hypothesis, "explored_ranges": explored_ranges or {}}
    with patched_numeric_feedback(module_package, details, explored_ranges=explored_ranges):
        result = module_package.evaluate_law(
            hypothesis,
            module_package.PARAM_DESCRIPTION,
            difficulty=difficulty,
            law_version=law_version,
            judge_model_name="disabled",
            trial_info={"trial_id": "validation_feedback"},
        )
    details.update(result)
    return details


def render_feedback(validation_mode: str, feedback: dict[str, Any] | None, previous_best_r2: float | None = None) -> str:
    if not feedback:
        return "<validation_feedback>Current hypothesis unavailable. Provide a <hypothesis> block to unlock feedback.</validation_feedback>"
    if validation_mode == "C_quantitative_feedback":
        improvement = ""
        if previous_best_r2 is not None:
            delta = feedback["r2"] - previous_best_r2
            improvement = f" Delta vs previous best: {delta:+.3f}."
        return (
            "<validation_feedback>"
            f"Current best-fit hypothesis R² = {feedback['r2']:.3f} on hidden validation points."
            f" Samples used: {feedback.get('sample_count', 0)}."
            f" RMSLE = {feedback['rmsle']:.4f}."
            f"{improvement}"
            "</validation_feedback>"
        )
    if validation_mode == "D_directional_feedback":
        worst = feedback.get("worst_slice")
        if not worst:
            return "<validation_feedback>No directional slice available yet.</validation_feedback>"
        return (
            "<validation_feedback>"
            f"Your equation deviates most on variable {worst['parameter']} in the {worst['region']} range. "
            f"It {worst['bias_direction']} the target by about {worst['bias_percent']:.1f}% there. "
            f"Current hidden-set R² = {feedback['r2']:.3f}."
            "</validation_feedback>"
        )
    if validation_mode == "E_verifier_agent":
        verdict = feedback.get("verdict", "unknown")
        confidence = feedback.get("confidence", "unknown")
        recommendation = feedback.get("recommendation", "No recommendation provided.")
        return (
            "<validation_feedback>"
            f"Verifier verdict: {verdict}. Confidence: {confidence}. Recommendation: {recommendation}"
            "</validation_feedback>"
        )
    raise KeyError(f"Unsupported validation mode: {validation_mode}")


def render_hypothesis_instructions() -> str:
    return inspect.cleandoc(
        """
        You may optionally include a <hypothesis> ... </hypothesis> block in any turn.
        The block should contain your current best `def discovered_law(...)` function.
        This does not end the trial. It only lets the environment compute validation feedback.
        The wrapper uses this block to compute hidden-set R² or directional error after experiments.
        """
    )
