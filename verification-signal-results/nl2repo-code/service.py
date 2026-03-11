from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from verification_signal_study.nl2repo.validator import run_validation


class NL2RepoValidationHandler(BaseHTTPRequestHandler):
    repo_root: str = ""
    scratch_root: str | None = None
    state_dir: str = "/tmp/nl2repo-validation-state"

    def do_POST(self) -> None:
        if self.path != "/validate":
            self.send_error(HTTPStatus.NOT_FOUND, "Unknown endpoint")
            return
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        try:
            result = run_validation(
                repo_root=self.repo_root,
                task_name=payload["task_name"],
                workspace_path=payload["workspace_path"],
                regime=payload["regime"],
                session_id=payload.get("session_id", "default"),
                stage_id=payload.get("stage_id"),
                selected_targets=payload.get("selected_targets"),
                is_final=payload.get("is_final", False),
                scratch_root=self.scratch_root,
                state_dir=payload.get("state_dir", self.state_dir),
            )
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            body = json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False).encode("utf-8")
            self.send_response(HTTPStatus.BAD_REQUEST)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def serve(repo_root: str, host: str = "127.0.0.1", port: int = 8765, scratch_root: str | None = None, state_dir: str = "/tmp/nl2repo-validation-state") -> None:
    handler_cls = NL2RepoValidationHandler
    handler_cls.repo_root = repo_root
    handler_cls.scratch_root = scratch_root
    handler_cls.state_dir = state_dir
    server = ThreadingHTTPServer((host, port), handler_cls)
    print(f"NL2Repo validation service listening on http://{host}:{port}")
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--scratch-root")
    parser.add_argument("--state-dir", default="/tmp/nl2repo-validation-state")
    args = parser.parse_args()
    serve(args.repo_root, host=args.host, port=args.port, scratch_root=args.scratch_root, state_dir=args.state_dir)


if __name__ == "__main__":
    main()
