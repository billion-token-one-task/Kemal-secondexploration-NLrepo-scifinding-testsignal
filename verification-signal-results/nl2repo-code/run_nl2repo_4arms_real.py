#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from verification_signal_study.common import ensure_dir, write_json
from verification_signal_study.nl2repo.controller import apply_visibility
from verification_signal_study.nl2repo.probes import compute_nl2repo_probes
from verification_signal_study.nl2repo.splitter import build_split_manifest, export_hidden_tests
from verification_signal_study.nl2repo.tracker import compute_step_state, parse_openhands_events
from verification_signal_study.nl2repo.validator import run_validation

DEFAULT_REPO_ROOT = Path('/home/ec2-user/benchmarks/NL2RepoBench')
DEFAULT_OPENHANDS_IMAGE = 'ghcr.io/all-hands-ai/openhands:0.56'
DEFAULT_RUNTIME_IMAGE = 'ghcr.io/all-hands-ai/runtime:0.56-nikolaik'
DEFAULT_SHIM_BASE = 'http://host.docker.internal:8012'
DEFAULT_ARMS = [
    'A_sparse_final_only',
    'B_staged_modules',
    'C_dense_any_test',
    'D_progressive_release',
]
DEFAULT_MAX_ITER = 12
DEFAULT_TIMEOUT_SEC = 420


def reset_run_dir(path: Path) -> Path:
    if path.exists():
        try:
            shutil.rmtree(path)
        except PermissionError:
            subprocess.run(['sudo', '-n', 'rm', '-rf', str(path)], check=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_host_ownership(path: Path) -> None:
    subprocess.run(['sudo', '-n', 'chown', '-R', 'ec2-user:ec2-user', str(path)], check=True)


def default_out_root(task_name: str) -> Path:
    if task_name == 'cerberus':
        return ROOT / 'run_outputs' / 'nl2repo'
    return ROOT / 'run_outputs' / 'nl2repo_multitask' / task_name


def docker_name(task_name: str, arm: str) -> str:
    value = f'nl2repo-{task_name}-{arm}'.replace('_', '-').replace('/', '-')
    return value[:120]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument('--task', default='cerberus')
    parser.add_argument('--repo-root', default=str(DEFAULT_REPO_ROOT))
    parser.add_argument('--out-dir')
    parser.add_argument('--max-iter', type=int, default=DEFAULT_MAX_ITER)
    parser.add_argument('--timeout-sec', type=int, default=DEFAULT_TIMEOUT_SEC)
    parser.add_argument('--shim-base', default=DEFAULT_SHIM_BASE)
    parser.add_argument('--openhands-image', default=DEFAULT_OPENHANDS_IMAGE)
    parser.add_argument('--runtime-image', default=DEFAULT_RUNTIME_IMAGE)
    parser.add_argument('--arms', nargs='+', default=list(DEFAULT_ARMS), choices=DEFAULT_ARMS)
    return parser.parse_args()


def run_task(args: argparse.Namespace) -> list[dict[str, object]]:
    repo_root = Path(args.repo_root)
    task_name = args.task
    out_root = Path(args.out_dir) if args.out_dir else default_out_root(task_name)
    out_root.mkdir(parents=True, exist_ok=True)

    split_manifest = build_split_manifest(repo_root, task_name)
    split_path = out_root / f'{task_name}.split.json'
    write_json(split_path, split_manifest)
    hidden_tests_root = out_root / f'{task_name}_hidden_tests'
    export_hidden_tests(split_manifest['image'], split_manifest['test_targets'], hidden_tests_root)

    start_md = repo_root / 'test_files' / task_name / 'start.md'
    arms = list(args.arms)
    summary: list[dict[str, object]] = []

    def staged_releaser(workspace_root: Path, stop_flag: threading.Event) -> None:
        checkpoints = [(20, 4), (40, 8), (60, 10)]
        start = time.time()
        for seconds, step in checkpoints:
            while not stop_flag.is_set() and (time.time() - start) < seconds:
                time.sleep(1)
            if stop_flag.is_set():
                return
            ensure_host_ownership(workspace_root)
            apply_visibility(workspace_root, split_manifest, 'D_progressive_release', current_step=step, total_steps=args.max_iter)

    for arm in arms:
        run_dir = reset_run_dir(out_root / arm)
        workspace = ensure_dir(run_dir / 'workspace')
        state = ensure_dir(run_dir / 'state')
        log_path = run_dir / 'openhands.log'
        (workspace / 'start.md').write_text(start_md.read_text(encoding='utf-8'), encoding='utf-8')

        hidden_dest = ensure_dir(workspace / '.vsignal' / 'hidden_tests')
        for path in hidden_tests_root.rglob('*'):
            if path.is_dir():
                continue
            rel = path.relative_to(hidden_tests_root)
            target = hidden_dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)

        for relative_path in split_manifest['hidden_test_files']:
            parent = workspace / Path(relative_path).parent
            parent.mkdir(parents=True, exist_ok=True)
            parent.chmod(0o777)
        workspace.chmod(0o777)

        if arm == 'B_staged_modules':
            apply_visibility(workspace, split_manifest, arm)
        elif arm == 'C_dense_any_test':
            apply_visibility(workspace, split_manifest, arm)
        elif arm == 'D_progressive_release':
            apply_visibility(workspace, split_manifest, arm, current_step=0, total_steps=args.max_iter)

        config_path = run_dir / 'config.toml'
        config_path.write_text(
            f'''[core]\nsave_trajectory_path = "/.openhands/trajectory.json"\nmax_iterations = {args.max_iter}\nruntime = "docker"\ndefault_agent = "CodeActAgent"\n\n[llm.tabcode]\nmodel = "gpt-4o-mini"\napi_key = "dummy"\nbase_url = "{args.shim_base}"\n\n[sandbox]\nruntime_container_image = "{args.runtime_image}"\nvolumes = "{workspace}:/workspace:rw"\n''',
            encoding='utf-8',
        )

        prompt = (
            'According to the start.md in /workspace, implement the entire project as required. '
            'Visible tests in /workspace may be used when available. '
            'If you believe you are done, use the finish tool and do not ask the user for confirmation.'
        )
        command = [
            'sudo', '-n', 'docker', 'run', '--rm', '--name', docker_name(task_name, arm),
            '--add-host', 'host.docker.internal:host-gateway',
            '-e', f'SANDBOX_RUNTIME_CONTAINER_IMAGE={args.runtime_image}',
            '-e', 'LOG_ALL_EVENTS=true',
            '-e', 'CONFIG_FILE=/custom/path/config.toml',
            '-e', 'AGENT_LLM_CONFIG=tabcode',
            '-v', f'{config_path}:/custom/path/config.toml:ro',
            '-v', '/var/run/docker.sock:/var/run/docker.sock:rw',
            '-v', f'{state}:/.openhands:rw',
            args.openhands_image,
            'python', '-m', 'openhands.core.main', '--config-file=/custom/path/config.toml', '-t', prompt,
        ]

        stop_flag = threading.Event()
        releaser = None
        if arm == 'D_progressive_release':
            releaser = threading.Thread(target=staged_releaser, args=(workspace, stop_flag), daemon=True)
            releaser.start()

        with log_path.open('w', encoding='utf-8') as log_handle:
            subprocess.run(command, stdout=log_handle, stderr=subprocess.STDOUT, text=True, timeout=args.timeout_sec, check=False)

        stop_flag.set()
        if releaser:
            releaser.join(timeout=2)

        trajectory_path = state / 'trajectory.json'
        raw_events = parse_openhands_events(trajectory_path) if trajectory_path.exists() else []
        states = compute_step_state(raw_events, total_budget=args.max_iter) if raw_events else []

        visible_targets = split_manifest['release_plan']['C_visible_nonheldout']
        full_targets = visible_targets + split_manifest['release_plan']['heldout']
        visible_validation = run_validation(repo_root, task_name, workspace, 'C_dense_any_test', selected_targets=visible_targets, session_id=f'{task_name}-{arm}-visible', is_final=True)
        full_validation = run_validation(repo_root, task_name, workspace, 'C_dense_any_test', selected_targets=full_targets, session_id=f'{task_name}-{arm}-full', is_final=True)
        heldout_validation = run_validation(repo_root, task_name, workspace, 'C_dense_any_test', selected_targets=split_manifest['release_plan']['heldout'], session_id=f'{task_name}-{arm}-heldout', is_final=True)

        monitor_snapshots = [
            {
                'current_step': 0,
                'visible_validation': visible_validation['pytest'],
                'full_validation': full_validation['pytest'],
                'heldout_validation': heldout_validation['pytest'],
            }
        ]
        probe_summary = compute_nl2repo_probes(
            states=states,
            raw_events=raw_events,
            workspace_root=workspace,
            monitor_snapshots=monitor_snapshots,
            total_tokens=None,
        )
        result = {
            'arm': arm,
            'task_name': task_name,
            'workspace': str(workspace),
            'state_dir': str(state),
            'trajectory_path': str(trajectory_path),
            'log_path': str(log_path),
            'visible_validation': visible_validation,
            'full_validation': full_validation,
            'heldout_validation': heldout_validation,
            'probe_summary': probe_summary,
            'event_count': len(raw_events),
        }
        result_path = run_dir / 'result.json'
        result_path.write_text(json.dumps(result, indent=2), encoding='utf-8')
        summary.append(
            {
                'task_name': task_name,
                'arm': arm,
                'event_count': len(raw_events),
                'visible_passed': visible_validation['pytest'].get('passed', 0),
                'full_passed': full_validation['pytest'].get('passed', 0),
                'heldout_passed': heldout_validation['pytest'].get('passed', 0),
                'result': str(result_path),
            }
        )

    summary_path = out_root / 'summary.json'
    summary_path.write_text(json.dumps(summary, indent=2), encoding='utf-8')
    return summary


def main() -> None:
    args = parse_args()
    summary = run_task(args)
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
