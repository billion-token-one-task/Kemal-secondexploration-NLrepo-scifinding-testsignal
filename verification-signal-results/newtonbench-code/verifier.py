from __future__ import annotations

import json
import re
from typing import Any, Callable


VERIFIER_PROMPT = """You are an external verifier observing a scientific-discovery agent.
Assess whether the worker's current hypothesis is self-consistent with its experiment history.
Return compact JSON with keys:
- verdict: one of [promising, inconsistent, underdetermined]
- confidence: one of [low, medium, high]
- recommendation: one short sentence about the next best action
- concerns: list of short strings
"""


JSON_BLOCK_PATTERN = re.compile(r"\{.*\}", re.DOTALL)


def build_trajectory_summary(trajectory: list[dict[str, Any]], max_steps: int = 6) -> str:
    lines: list[str] = []
    for step in trajectory[-max_steps:]:
        line = {
            "turn": step.get("turn"),
            "action_type": step.get("action_type"),
            "num_experiments": step.get("num_experiments", 0),
            "hypothesis_updated": step.get("hypothesis_updated", False),
            "current_r2": step.get("current_r2"),
            "best_r2": step.get("best_r2"),
            "temperature": step.get("temperature"),
        }
        lines.append(json.dumps(line, ensure_ascii=False))
    return "\n".join(lines)


def _parse_verifier_json(response_text: str) -> dict[str, Any]:
    match = JSON_BLOCK_PATTERN.search(response_text or "")
    if not match:
        return {
            "verdict": "underdetermined",
            "confidence": "low",
            "recommendation": "Verifier could not parse a structured judgment.",
            "concerns": ["unparseable response"],
        }
    try:
        payload = json.loads(match.group(0))
        return {
            "verdict": payload.get("verdict", "underdetermined"),
            "confidence": payload.get("confidence", "low"),
            "recommendation": payload.get("recommendation", "No recommendation provided."),
            "concerns": payload.get("concerns", []),
        }
    except json.JSONDecodeError:
        return {
            "verdict": "underdetermined",
            "confidence": "low",
            "recommendation": "Verifier returned malformed JSON.",
            "concerns": ["malformed json"],
        }


def run_verifier_feedback(
    llm_callable: Callable[..., tuple[str | None, str | None, int | None]],
    model_name: str,
    *,
    trajectory: list[dict[str, Any]],
    current_hypothesis: str,
    trial_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    user_prompt = (
        "Trajectory summary:\n"
        f"{build_trajectory_summary(trajectory)}\n\n"
        "Current hypothesis:\n"
        f"{current_hypothesis}\n\n"
        "Judge whether the worker is on a coherent path or trapped in a self-consistent but weak local explanation."
    )
    response_text, reasoning_text, tokens = llm_callable(
        [{"role": "system", "content": VERIFIER_PROMPT}, {"role": "user", "content": user_prompt}],
        model_name=model_name,
        trial_info=trial_info,
    )
    parsed = _parse_verifier_json(response_text or "")
    parsed.update(
        {
            "raw_response": response_text,
            "raw_reasoning": reasoning_text,
            "token_count": tokens or 0,
        }
    )
    return parsed


def render_verifier_feedback(result: dict[str, Any]) -> str:
    concerns = result.get("concerns") or []
    suffix = f" Concerns: {', '.join(concerns[:3])}." if concerns else ""
    return (
        "<validation_feedback>"
        f"Verifier verdict: {result.get('verdict', 'underdetermined')}. "
        f"Confidence: {result.get('confidence', 'low')}. "
        f"Recommendation: {result.get('recommendation', 'No recommendation provided.')}"
        f"{suffix}"
        "</validation_feedback>"
    )
