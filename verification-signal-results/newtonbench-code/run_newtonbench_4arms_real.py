#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import litellm

DEFAULT_BENCH_ROOT = Path('/home/ec2-user/benchmarks/NewtonBench')
DEFAULT_MODEL_NAME = 'gpt-5.3-codex'
DEFAULT_ARMS = [
    'A_batched_dataset',
    'B_interactive_observation',
    'C_quantitative_feedback',
    'D_directional_feedback',
]

BENCH_ROOT = DEFAULT_BENCH_ROOT
if str(BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCH_ROOT))
call_module = importlib.import_module('utils.call_llm_api')

litellm.drop_params = True


def litellm_stream_call(messages, model_name, keys=None, temperature=0.4, trial_info=None):
    stream = litellm.completion(
        model=model_name,
        messages=messages,
        max_tokens=1024,
        timeout=180,
        api_base='https://api.tabcode.cc/openai',
        api_key=os.environ['OPENAI_API_KEY'],
        stream=True,
    )
    text_parts = []
    total_tokens = 0
    for chunk in stream:
        try:
            delta = chunk.choices[0].delta.content
        except Exception:
            delta = None
        if delta:
            text_parts.append(delta)
        try:
            usage = getattr(chunk, 'usage', None)
            if usage and getattr(usage, 'total_tokens', None):
                total_tokens = int(usage.total_tokens)
        except Exception:
            pass
    text = ''.join(text_parts)
    return text, None, total_tokens or max(1, len(text.split()))


call_module.call_llm_api = litellm_stream_call
from verification_signal_study.newtonbench.runner import run_vanilla_trial


def task_id(module: str, difficulty: str, system: str, law_version: str) -> str:
    return '__'.join([module, difficulty, system, law_version])


def default_out_dir(module: str, difficulty: str, system: str, law_version: str) -> Path:
    if (module, difficulty, system, law_version) == ('m0_gravity', 'easy', 'vanilla_equation', 'v0'):
        return ROOT / 'run_outputs' / 'newtonbench'
    return ROOT / 'run_outputs' / 'newtonbench_multitask' / task_id(module, difficulty, system, law_version)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument('--benchmark-root', default=str(DEFAULT_BENCH_ROOT))
    parser.add_argument('--module', default='m0_gravity')
    parser.add_argument('--difficulty', default='easy')
    parser.add_argument('--system', default='vanilla_equation')
    parser.add_argument('--law-version', default='v0')
    parser.add_argument('--model-name', default=DEFAULT_MODEL_NAME)
    parser.add_argument('--max-turns', type=int, default=6)
    parser.add_argument('--experiment-budget', type=int, default=6)
    parser.add_argument('--arms', nargs='+', default=list(DEFAULT_ARMS), choices=DEFAULT_ARMS)
    parser.add_argument('--out-dir')
    parser.add_argument('--trial-prefix', default='newton')
    return parser.parse_args()


def run_task(args: argparse.Namespace) -> list[dict[str, object]]:
    benchmark_root = Path(args.benchmark_root)
    out_dir = Path(args.out_dir) if args.out_dir else default_out_dir(args.module, args.difficulty, args.system, args.law_version)
    out_dir.mkdir(parents=True, exist_ok=True)

    summary: list[dict[str, object]] = []
    for arm in args.arms:
        result = run_vanilla_trial(
            benchmark_root=benchmark_root,
            module_name=args.module,
            model_name=args.model_name,
            difficulty=args.difficulty,
            system=args.system,
            law_version=args.law_version,
            validation_mode=arm,
            max_turns=args.max_turns,
            experiment_budget=args.experiment_budget,
            trial_info={'trial_id': f'{args.trial_prefix}_{task_id(args.module, args.difficulty, args.system, args.law_version)}_{arm}'},
        )
        path = out_dir / f'{arm}.json'
        path.write_text(json.dumps(result, indent=2), encoding='utf-8')
        summary.append(
            {
                'task_id': task_id(args.module, args.difficulty, args.system, args.law_version),
                'module': args.module,
                'difficulty': args.difficulty,
                'system': args.system,
                'law_version': args.law_version,
                'arm': arm,
                'status': result['status'],
                'rounds': result['rounds'],
                'num_experiments': result['num_experiments'],
                'best_r2': result.get('probe_summary', {}).get('r2_curve', [0])[-1],
                'output': str(path),
            }
        )

    summary_path = out_dir / 'summary.json'
    summary_path.write_text(json.dumps(summary, indent=2), encoding='utf-8')
    return summary


def main() -> None:
    args = parse_args()
    summary = run_task(args)
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
