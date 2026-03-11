from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

from verification_signal_study.newtonbench.feedback import (
    evaluate_hypothesis,
    extract_function_arguments,
    extract_hypothesis,
    render_hypothesis_instructions,
)
from verification_signal_study.newtonbench.probes import compute_newtonbench_probes
from verification_signal_study.newtonbench.signal_wrapper import build_explored_ranges, wrap_experiment_output


def _ensure_root(benchmark_root: str | Path) -> None:
    root = str(Path(benchmark_root).resolve())
    if root not in sys.path:
        sys.path.insert(0, root)


def _base_prompt(max_turns: int, validation_mode: str) -> str:
    extra = ""
    if validation_mode in {"C_quantitative_feedback", "D_directional_feedback", "E_verifier_agent"}:
        extra = "\n\n" + render_hypothesis_instructions()
    return (
        "You are an AI research assistant tasked with discovering scientific laws in a simulated universe. "
        "Use experiments carefully, analyze observations, and submit your final law with <final_law>. "
        f"You can use up to {max_turns} rounds."
        + extra
    )


def _task_prompt(module, system: str, noise_level: float, validation_mode: str, experiment_budget: int) -> str:
    prompt = module.get_task_prompt(system, noise_level=noise_level)
    if validation_mode == "A_batched_dataset":
        prompt += (
            f"\n\nSpecial rule for this run: you must spend your full experiment budget up front. "
            f"In your first experimental turn, request up to {experiment_budget} experiments. After that, no further experiments are allowed."
        )
    elif validation_mode in {"C_quantitative_feedback", "D_directional_feedback", "E_verifier_agent"}:
        prompt += "\n\nThe environment may append <validation_feedback> after each experiment if you provide a <hypothesis> block."
    return prompt


def _extract_final_law(response_text: str, function_signature: str) -> tuple[bool, str]:
    start = response_text.rfind("<final_law>")
    if start == -1:
        return False, f"{function_signature} return float('nan')"
    end = response_text.find("</final_law>", start)
    if end == -1:
        return False, f"{function_signature} return float('nan')"
    content = response_text[start + len("<final_law>") : end].strip()
    hypothesis = extract_hypothesis(content) or content
    return True, hypothesis


def _parse_experiments(response_text: str) -> list[dict[str, Any]]:
    start = response_text.rfind("<run_experiment>")
    if start == -1:
        return []
    end = response_text.find("</run_experiment>", start)
    if end == -1:
        return []
    content = response_text[start + len("<run_experiment>") : end].strip()
    parsed = json.loads(content)
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        return [parsed]
    return []


def _context_size(messages: list[dict[str, str]]) -> int:
    return sum(len(message.get("content", "")) for message in messages)


def _step_record(turn: int, max_turns: int, token_count: int, messages: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "turn": turn,
        "remaining_steps": max(0, max_turns - turn),
        "token_count": token_count,
        "prompt_chars": _context_size(messages),
    }


def run_vanilla_trial(
    benchmark_root: str | Path,
    module_name: str,
    model_name: str,
    *,
    noise_level: float = 0.0,
    difficulty: str = "easy",
    system: str = "vanilla_equation",
    law_version: str = "v0",
    validation_mode: str = "B_interactive_observation",
    max_turns: int = 10,
    experiment_budget: int | None = None,
    trial_info: dict[str, Any] | None = None,
    verifier_model_name: str | None = None,
    verifier_every_n_steps: int = 2,
) -> dict[str, Any]:
    _ensure_root(benchmark_root)
    module = importlib.import_module(f"modules.{module_name}")
    call_llm_api = importlib.import_module("utils.call_llm_api").call_llm_api

    if experiment_budget is None:
        experiment_budget = max_turns

    messages = [{"role": "system", "content": _base_prompt(max_turns, validation_mode)}]
    task_prompt = _task_prompt(module, system, noise_level, validation_mode, experiment_budget)
    messages.append({"role": "user", "content": task_prompt})

    total_tokens = 0
    experiments_used = 0
    current_hypothesis: str | None = None
    best_hypothesis: str | None = None
    best_feedback: dict[str, Any] | None = None
    best_r2 = 0.0
    batched_dataset_released = False
    steps_since_equation_update = 0
    trajectory: list[dict[str, Any]] = []
    experiment_history: list[dict[str, Any]] = []
    initial_prompt_chars = _context_size(messages)
    signature_variables = extract_function_arguments(module.FUNCTION_SIGNATURE)

    for turn in range(1, max_turns + 1):
        response_text, reasoning_text, tokens = call_llm_api(messages, model_name=model_name, trial_info=trial_info)
        total_tokens += tokens or 0
        response_text = response_text or ""
        combined = response_text if not reasoning_text else f"**Reasoning Process:**\n{reasoning_text}\n\n**Main Response:**\n{response_text}"
        messages.append({"role": "assistant", "content": combined})

        previous_hypothesis = current_hypothesis
        current_hypothesis = extract_hypothesis(response_text) or current_hypothesis
        hypothesis_updated = bool(current_hypothesis and current_hypothesis != previous_hypothesis)

        step = _step_record(turn, max_turns, int(tokens or 0), messages)
        step.update(
            {
                "action_type": "invalid",
                "raw_response": response_text,
                "reasoning": reasoning_text,
                "current_hypothesis": current_hypothesis,
                "hypothesis_updated": hypothesis_updated,
                "num_experiments": 0,
                "experiments_requested": [],
                "experiment_results": [],
                "current_r2": best_feedback.get("r2", 0.0) if best_feedback else 0.0,
                "best_r2": best_r2,
                "validation_feedback": None,
                "verifier_feedback": None,
            }
        )

        explored_ranges = build_explored_ranges(experiment_history, current_hypothesis)
        if hypothesis_updated and current_hypothesis:
            hidden_eval = evaluate_hypothesis(
                benchmark_root,
                module_name,
                current_hypothesis,
                difficulty,
                law_version,
                explored_ranges=explored_ranges,
            )
            step["current_r2"] = float(hidden_eval.get("r2") or 0.0)
            step["current_hidden_metrics"] = hidden_eval
            if step["current_r2"] >= best_r2:
                best_r2 = step["current_r2"]
                best_feedback = hidden_eval
                best_hypothesis = current_hypothesis
        step["best_r2"] = best_r2

        is_final, submitted_law = _extract_final_law(response_text, module.FUNCTION_SIGNATURE)
        if is_final:
            step["action_type"] = "final_law"
            step["submitted_law"] = submitted_law
            steps_since_equation_update = 0 if hypothesis_updated else steps_since_equation_update + 1
            step["temperature"] = steps_since_equation_update / max(1, step["remaining_steps"])
            step["context_pressure"] = step["prompt_chars"] / max(1, initial_prompt_chars)
            trajectory.append(step)
            result = {
                "status": "completed",
                "submitted_law": submitted_law,
                "current_hypothesis": current_hypothesis,
                "best_hypothesis": best_hypothesis or current_hypothesis,
                "rounds": turn,
                "total_tokens": total_tokens,
                "num_experiments": experiments_used,
                "chat_history": messages,
                "trajectory": trajectory,
                "experiment_history": experiment_history,
                "signature_variables": signature_variables,
            }
            result["probe_summary"] = compute_newtonbench_probes(result)
            return result

        requested = _parse_experiments(response_text)
        if validation_mode == "A_batched_dataset" and batched_dataset_released and requested:
            step["action_type"] = "analysis_only"
            messages.append({"role": "user", "content": "No more experiments are available in this batched mode. Analyze the released dataset and submit your law."})
            steps_since_equation_update = 0 if hypothesis_updated else steps_since_equation_update + 1
            step["temperature"] = steps_since_equation_update / max(1, step["remaining_steps"])
            step["context_pressure"] = step["prompt_chars"] / max(1, initial_prompt_chars)
            trajectory.append(step)
            continue

        if requested:
            remaining_budget = max(0, experiment_budget - experiments_used)
            if remaining_budget <= 0:
                step["action_type"] = "budget_exhausted"
                messages.append({"role": "user", "content": "Experiment budget exhausted. Submit your final law with <final_law>."})
                steps_since_equation_update = 0 if hypothesis_updated else steps_since_equation_update + 1
                step["temperature"] = steps_since_equation_update / max(1, step["remaining_steps"])
                step["context_pressure"] = step["prompt_chars"] / max(1, initial_prompt_chars)
                trajectory.append(step)
                continue
            if len(requested) > remaining_budget:
                requested = requested[:remaining_budget]

            results = []
            for experiment in requested:
                result = module.run_experiment_for_module(
                    **experiment,
                    noise_level=noise_level,
                    difficulty=difficulty,
                    system=system,
                    law_version=law_version,
                )
                if system == "vanilla_equation":
                    result = "{:.15e}".format(result)
                results.append(result)
                experiment_history.append(experiment)
            experiments_used += len(requested)
            batched_dataset_released = batched_dataset_released or validation_mode == "A_batched_dataset"

            wrapper_output = wrap_experiment_output(
                benchmark_root=benchmark_root,
                module_name=module_name,
                validation_mode=validation_mode,
                experiment_results=results,
                current_hypothesis=current_hypothesis,
                difficulty=difficulty,
                law_version=law_version,
                experiment_history=experiment_history,
                previous_best_r2=best_r2,
                llm_callable=call_llm_api if validation_mode == "E_verifier_agent" else None,
                verifier_model_name=verifier_model_name,
                trajectory=trajectory,
                verifier_trial_info={"trial_id": (trial_info or {}).get("trial_id", "verifier"), "role": "verifier"},
            )
            evaluation = wrapper_output.get("evaluation")
            verifier_feedback = wrapper_output.get("verifier_feedback")
            if evaluation:
                step["current_r2"] = float(evaluation.get("r2") or 0.0)
                step["current_hidden_metrics"] = evaluation
                if step["current_r2"] >= best_r2:
                    best_r2 = step["current_r2"]
                    best_feedback = evaluation
                    best_hypothesis = current_hypothesis
            step.update(
                {
                    "action_type": "experiment",
                    "num_experiments": len(requested),
                    "experiments_requested": requested,
                    "experiment_results": results,
                    "validation_feedback": evaluation,
                    "verifier_feedback": verifier_feedback,
                    "explored_ranges": wrapper_output.get("explored_ranges", {}),
                    "best_r2": best_r2,
                }
            )
            if validation_mode == "E_verifier_agent" and verifier_every_n_steps > 1 and (turn % verifier_every_n_steps != 0):
                payload = f"<experiment_output>\n{json.dumps(results)}\n</experiment_output>"
            else:
                payload = wrapper_output["payload"]
            messages.append({"role": "user", "content": payload})
        else:
            messages.append({"role": "user", "content": "Invalid response. Use <run_experiment> or <final_law>."})

        steps_since_equation_update = 0 if hypothesis_updated else steps_since_equation_update + 1
        step["temperature"] = steps_since_equation_update / max(1, step["remaining_steps"])
        step["context_pressure"] = step["prompt_chars"] / max(1, initial_prompt_chars)
        step["best_r2"] = best_r2
        trajectory.append(step)

    messages.append({"role": "user", "content": f"You have used all turns. Submit your final law now using signature {module.FUNCTION_SIGNATURE}."})
    response_text, _, tokens = call_llm_api(messages, model_name=model_name, trial_info=trial_info)
    total_tokens += tokens or 0
    submitted_law = extract_hypothesis(response_text or "") or f"{module.FUNCTION_SIGNATURE} return float('nan')"
    messages.append({"role": "assistant", "content": response_text or ""})
    result = {
        "status": "max_turns_reached",
        "submitted_law": submitted_law,
        "current_hypothesis": current_hypothesis,
        "best_hypothesis": best_hypothesis or current_hypothesis,
        "rounds": max_turns,
        "total_tokens": total_tokens,
        "num_experiments": experiments_used,
        "chat_history": messages,
        "trajectory": trajectory,
        "experiment_history": experiment_history,
        "signature_variables": signature_variables,
    }
    result["probe_summary"] = compute_newtonbench_probes(result)
    return result
