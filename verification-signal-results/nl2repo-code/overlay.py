from __future__ import annotations

import json
from pathlib import Path

from verification_signal_study.common import ensure_dir, read_json
from verification_signal_study.nl2repo.controller import write_workspace_bundle
from verification_signal_study.nl2repo.discovery import load_task


def _guide_text(task_name: str, regime: str, include_visibility_bundle: bool) -> str:
    extra = ""
    if include_visibility_bundle:
        extra = """

This overlay also contains a visibility controller bundle:

```bash
python3 tools/apply_test_visibility.py --regime REGIME --current-step 12 --total-steps 40
```

This is intended for OpenHands entrypoint / sidecar automation.
"""
    return f"""# Validation Guide

Task: `{task_name}`
Regime: `{regime}`

This workspace contains a helper command:

```bash
python3 tools/nl2repo_validate.py [--final] [--stage g1]
```

Rules for this regime:

- `A_sparse_final_only`: do not call validation until final submission.
- `B_staged_modules`: call with `--stage <group_id>`.
- `C_dense_any_test`: you may validate whenever useful.
- `D_progressive_release`: each call unlocks more hidden tests.

The validator only returns summarized feedback; hidden tests remain hidden.{extra}
"""


def materialize_overlay(
    repo_root: str,
    task_name: str,
    regime: str,
    service_url: str,
    output_dir: str,
    *,
    split_manifest_path: str | None = None,
) -> Path:
    task = load_task(repo_root, task_name)
    root = ensure_dir(output_dir)
    tools_dir = ensure_dir(root / "tools")
    context = {
        "task_name": task_name,
        "regime": regime,
        "service_url": service_url.rstrip("/"),
        "stage_groups": task.stage_groups,
    }
    (root / "VALIDATION_GUIDE.md").write_text(
        _guide_text(task_name, regime, include_visibility_bundle=bool(split_manifest_path)),
        encoding="utf-8",
    )
    (tools_dir / "run_context.json").write_text(json.dumps(context, indent=2), encoding="utf-8")
    helper = f'''#!/usr/bin/env python3
import argparse
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage")
    parser.add_argument("--final", action="store_true")
    parser.add_argument("--workspace", default=str(Path.cwd()))
    parser.add_argument("--selected-target", action="append", dest="selected_targets")
    args = parser.parse_args()

    context = json.loads((Path(__file__).resolve().parent / "run_context.json").read_text(encoding="utf-8"))
    payload = {{
        "task_name": context["task_name"],
        "workspace_path": args.workspace,
        "regime": context["regime"],
        "session_id": os.environ.get("NL2REPO_SESSION_ID", context["task_name"]),
        "stage_id": args.stage,
        "selected_targets": args.selected_targets,
        "is_final": args.final,
    }}
    request = Request(
        context["service_url"] + "/validate",
        data=json.dumps(payload).encode("utf-8"),
        headers={{"Content-Type": "application/json"}},
        method="POST",
    )
    with urlopen(request) as response:
        print(response.read().decode("utf-8"))


if __name__ == "__main__":
    main()
'''
    helper_path = tools_dir / "nl2repo_validate.py"
    helper_path.write_text(helper, encoding="utf-8")
    helper_path.chmod(0o755)

    if split_manifest_path:
        split_manifest = read_json(split_manifest_path)
        write_workspace_bundle(root, split_manifest, regime)
    return root


def render_openhands_instruction(task_name: str, regime: str = "C_dense_any_test") -> str:
    return (
        "According to start.md, VALIDATION_GUIDE.md, and TEST_VISIBILITY_PLAN.md when available, "
        "implement the project step by step. "
        f"You are working on task '{task_name}' under regime '{regime}'. "
        "Use tools/nl2repo_validate.py only according to the guide."
    )
