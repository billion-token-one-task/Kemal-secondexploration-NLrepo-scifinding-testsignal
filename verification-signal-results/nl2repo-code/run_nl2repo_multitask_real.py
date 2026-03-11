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

from verification_signal_study.common import ensure_dir
from verification_signal_study.nl2repo.discovery import discover_tasks

DEFAULT_REPO_ROOT = Path('/home/ec2-user/benchmarks/NL2RepoBench')
DEFAULT_PILOT_MANIFEST = ROOT / 'experiments' / 'manifests' / 'nl2repo_pilot.json'


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo-root', default=str(DEFAULT_REPO_ROOT))
    parser.add_argument('--out-root')
    parser.add_argument('--tasks', nargs='*')
    parser.add_argument('--pilot-manifest', default=str(DEFAULT_PILOT_MANIFEST))
    parser.add_argument('--max-tasks', type=int, default=4)
    parser.add_argument('--concurrency', type=int, default=2)
    parser.add_argument('--poll-seconds', type=float, default=5.0)
    parser.add_argument('--sort-by', choices=['manifest', 'test_case_count', 'name'], default='test_case_count')
    parser.add_argument('--dry-run', action='store_true')
    return parser.parse_args()


def load_task_records(repo_root: Path, pilot_manifest: Path) -> list[dict[str, object]]:
    if pilot_manifest.exists():
        data = json.loads(pilot_manifest.read_text(encoding='utf-8'))
        records = data.get('tasks')
        if isinstance(records, list) and records:
            return records
    discovered = discover_tasks(repo_root)
    return [task.to_dict() for task in discovered]


def choose_tasks(args: argparse.Namespace) -> list[dict[str, object]]:
    repo_root = Path(args.repo_root)
    records = load_task_records(repo_root, Path(args.pilot_manifest))
    if args.tasks:
        by_name = {record['name']: record for record in records}
        missing = [name for name in args.tasks if name not in by_name]
        if missing:
            raise KeyError(f'Unknown task(s): {", ".join(missing)}')
        return [by_name[name] for name in args.tasks]
    if args.sort_by == 'test_case_count':
        records = sorted(records, key=lambda item: (item['test_case_count'], item['name']))
    elif args.sort_by == 'name':
        records = sorted(records, key=lambda item: item['name'])
    return records[: args.max_tasks]


def write_status(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding='utf-8')


def main() -> None:
    args = parse_args()
    out_root = Path(args.out_root) if args.out_root else ROOT / 'run_outputs' / 'nl2repo_multitask' / time.strftime('batch_%Y%m%d_%H%M%S')
    out_root.mkdir(parents=True, exist_ok=True)
    tasks = choose_tasks(args)
    selection = [
        {
            'task_name': record['name'],
            'test_case_count': record['test_case_count'],
            'image': record.get('image'),
            'output_dir': str(out_root / record['name']),
        }
        for record in tasks
    ]
    (out_root / 'tasks.json').write_text(json.dumps(selection, indent=2), encoding='utf-8')

    if args.dry_run:
        print(json.dumps(selection, indent=2))
        return

    pending = list(selection)
    active: dict[str, dict[str, object]] = {}
    completed: list[dict[str, object]] = []
    status_path = out_root / 'status.json'
    summary_path = out_root / 'summary.json'

    while pending or active:
        while pending and len(active) < args.concurrency:
            item = pending.pop(0)
            task_name = item['task_name']
            task_out = Path(item['output_dir'])
            ensure_dir(task_out)
            log_path = task_out / 'launcher.log'
            handle = log_path.open('w', encoding='utf-8')
            cmd = [
                sys.executable,
                str(ROOT / 'scripts' / 'run_nl2repo_4arms_real.py'),
                '--repo-root', args.repo_root,
                '--task', task_name,
                '--out-dir', str(task_out),
            ]
            process = subprocess.Popen(cmd, stdout=handle, stderr=subprocess.STDOUT, text=True)
            active[task_name] = {
                'task_name': task_name,
                'test_case_count': item['test_case_count'],
                'output_dir': str(task_out),
                'pid': process.pid,
                'process': process,
                'log_handle': handle,
                'started_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            }

        finished: list[str] = []
        for task_name, item in active.items():
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
            finished.append(task_name)
        for task_name in finished:
            active.pop(task_name, None)

        status_payload = {
            'out_root': str(out_root),
            'pending_tasks': [item['task_name'] for item in pending],
            'active_tasks': [
                {
                    'task_name': item['task_name'],
                    'pid': item['pid'],
                    'test_case_count': item['test_case_count'],
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
                'task_name': item['task_name'],
                'test_case_count': item['test_case_count'],
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
