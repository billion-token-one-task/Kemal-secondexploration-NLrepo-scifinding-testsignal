#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_PILOT_MANIFEST = ROOT / 'experiments' / 'manifests' / 'newtonbench_pilot.json'
DEFAULT_BENCH_ROOT = Path('/home/ec2-user/benchmarks/NewtonBench')


def task_id(item: dict[str, str]) -> str:
    return '__'.join([item['module'], item['difficulty'], item['system'], item['law_version']])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument('--benchmark-root', default=str(DEFAULT_BENCH_ROOT))
    parser.add_argument('--pilot-manifest', default=str(DEFAULT_PILOT_MANIFEST))
    parser.add_argument('--task-ids', nargs='*')
    parser.add_argument('--max-tasks', type=int, default=20)
    parser.add_argument('--concurrency', type=int, default=2)
    parser.add_argument('--poll-seconds', type=float, default=10.0)
    parser.add_argument('--out-root')
    parser.add_argument('--model-name', default='gpt-5.3-codex')
    parser.add_argument('--max-turns', type=int, default=6)
    parser.add_argument('--experiment-budget', type=int, default=6)
    parser.add_argument('--dry-run', action='store_true')
    return parser.parse_args()


def load_tasks(pilot_manifest: Path) -> list[dict[str, str]]:
    data = json.loads(pilot_manifest.read_text(encoding='utf-8'))
    return list(data['tasks'])


def choose_tasks(args: argparse.Namespace) -> list[dict[str, str]]:
    tasks = load_tasks(Path(args.pilot_manifest))
    if args.task_ids:
        by_id = {task_id(item): item for item in tasks}
        missing = [item for item in args.task_ids if item not in by_id]
        if missing:
            raise KeyError(f'Unknown task ids: {", ".join(missing)}')
        return [by_id[item] for item in args.task_ids]
    return tasks[: args.max_tasks]


def write_status(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding='utf-8')


def main() -> None:
    args = parse_args()
    out_root = Path(args.out_root) if args.out_root else ROOT / 'run_outputs' / 'newtonbench_multitask' / time.strftime('batch_%Y%m%d_%H%M%S')
    out_root.mkdir(parents=True, exist_ok=True)

    tasks = choose_tasks(args)
    selected = [
        {
            **item,
            'task_id': task_id(item),
            'output_dir': str(out_root / task_id(item)),
        }
        for item in tasks
    ]
    (out_root / 'tasks.json').write_text(json.dumps(selected, indent=2), encoding='utf-8')

    if args.dry_run:
        print(json.dumps(selected, indent=2))
        return

    pending = list(selected)
    active: dict[str, dict[str, object]] = {}
    completed: list[dict[str, object]] = []
    status_path = out_root / 'status.json'
    summary_path = out_root / 'summary.json'

    while pending or active:
        while pending and len(active) < args.concurrency:
            item = pending.pop(0)
            current_task_id = item['task_id']
            task_out = Path(item['output_dir'])
            task_out.mkdir(parents=True, exist_ok=True)
            log_path = task_out / 'launcher.log'
            handle = log_path.open('w', encoding='utf-8')
            cmd = [
                sys.executable,
                str(ROOT / 'scripts' / 'run_newtonbench_4arms_real.py'),
                '--benchmark-root', args.benchmark_root,
                '--module', item['module'],
                '--difficulty', item['difficulty'],
                '--system', item['system'],
                '--law-version', item['law_version'],
                '--model-name', args.model_name,
                '--max-turns', str(args.max_turns),
                '--experiment-budget', str(args.experiment_budget),
                '--out-dir', str(task_out),
                '--trial-prefix', 'newton_multi',
            ]
            process = subprocess.Popen(cmd, stdout=handle, stderr=subprocess.STDOUT, text=True)
            active[current_task_id] = {
                'task_id': current_task_id,
                'module': item['module'],
                'difficulty': item['difficulty'],
                'system': item['system'],
                'law_version': item['law_version'],
                'output_dir': str(task_out),
                'pid': process.pid,
                'process': process,
                'log_handle': handle,
                'started_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            }

        finished: list[str] = []
        for current_task_id, item in active.items():
            process: subprocess.Popen[str] = item['process']
            returncode = process.poll()
            if returncode is None:
                continue
            item['returncode'] = returncode
            item['finished_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            summary_file = Path(item['output_dir']) / 'summary.json'
            if summary_file.exists():
                item['summary_path'] = str(summary_file)
                item['summary'] = json.loads(summary_file.read_text(encoding='utf-8'))
            item.pop('process').wait()
            item.pop('log_handle').close()
            completed.append(item)
            finished.append(current_task_id)
        for current_task_id in finished:
            active.pop(current_task_id, None)

        status_payload = {
            'out_root': str(out_root),
            'pending_tasks': [item['task_id'] for item in pending],
            'active_tasks': [
                {
                    'task_id': item['task_id'],
                    'module': item['module'],
                    'difficulty': item['difficulty'],
                    'system': item['system'],
                    'law_version': item['law_version'],
                    'pid': item['pid'],
                    'output_dir': item['output_dir'],
                    'started_at': item['started_at'],
                }
                for item in active.values()
            ],
            'completed_tasks': [
                {
                    key: value
                    for key, value in item.items()
                    if key not in {'summary'}
                }
                for item in completed
            ],
        }
        write_status(status_path, status_payload)
        time.sleep(args.poll_seconds)

    summary_payload = {
        'out_root': str(out_root),
        'task_count': len(completed),
        'tasks': [
            {
                'task_id': item['task_id'],
                'module': item['module'],
                'difficulty': item['difficulty'],
                'system': item['system'],
                'law_version': item['law_version'],
                'returncode': item.get('returncode'),
                'output_dir': item['output_dir'],
                'summary_path': item.get('summary_path'),
                'summary': item.get('summary'),
            }
            for item in completed
        ],
    }
    write_status(summary_path, summary_payload)
    print(json.dumps(summary_payload, indent=2))


if __name__ == '__main__':
    main()
