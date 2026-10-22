"""ForgeX CLI: a client of the same authenticated engine used by Electron."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

MODES = {"inspect": "read_only", "plan": "plan_only", "review": "review_changes", "auto": "auto"}
SETTLED = {"completed", "completed_with_warning", "failed", "blocked", "timed_out", "cancelled",
           "awaiting_flash_confirmation", "paused_model_unavailable"}


class EngineClient:
    def __init__(self, url, token):
        parsed = urlsplit(url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path.strip("/"):
            raise ValueError("CLI_ENGINE_URL_MUST_BE_LOOPBACK")
        if not token:
            raise ValueError("Set FORGEX_LOCAL_TOKEN to attach to an existing engine.")
        self.url, self.token = url.rstrip("/"), token

    def request(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        request = Request(self.url + path, data=data, method=method,
                          headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except HTTPError as exc:
            payload = exc.read(65536).decode("utf-8", errors="replace")
            raise ValueError(f"Engine request failed ({exc.code}): {payload}") from exc

    def run(self, project, instruction, *, mode="review", environment=None, provider=None, model=None, trust=False):
        imported = self.request("POST", "/projects/import", {"path": str(Path(project).resolve(strict=True))})
        project_id = imported["project_id"]
        if trust:
            self.request("POST", f"/agent-runtime/projects/{quote(project_id, safe='')}/trust", {"trusted": True})
        session = self.request("POST", "/agent-runtime/sessions", {"kind": "agent", "project_id": project_id})
        session_id = session.get("session_id") or session["session"]["session_id"]
        response = self.request("POST", f"/agent-runtime/sessions/{quote(session_id, safe='')}/messages",
            {"content": instruction, "autonomy": MODES[mode], "environment": environment,
             "provider_id": provider, "model_id": model})
        run = response.get("run")
        if not run:
            return response
        run_id = run["run_id"]
        job_id = run.get("job_id")
        try:
            while True:
                job = None
                if job_id:
                    job_response = self.request("GET", f"/agent-runtime/jobs/{quote(job_id, safe='')}")
                    job = job_response.get("job", job_response)
                    current = job.get("current_run_id")
                    if current and current != run_id:
                        run_id = current
                        run_response = self.request("GET", f"/agent-runtime/runs/{quote(run_id, safe='')}")
                        run = run_response.get("run", run_response)
                auto_recovery_pending = (job is not None and not job.get("paused_by") and not job.get("blocking_reason")
                    and job.get("recovery_policy") == "safe_auto" and job.get("auto_recovery_count", 0) < 3
                    and bool(job.get("retry_due_at") or job.get("status") == "interrupted"))
                if run["status"] in SETTLED and not auto_recovery_pending:
                    break
                time.sleep(0.5)
                run = self.request("GET", f"/agent-runtime/runs/{quote(run_id, safe='')}")
                run = run.get("run", run)
        except KeyboardInterrupt:
            self.request("POST", f"/agent-runtime/sessions/{quote(session_id, safe='')}/cancel", {})
            raise
        return run

    def confirm_flash(self, run_id, *, approval_id, manifest_sha256, port):
        path = "/agent-runtime/runs/" + quote(run_id, safe="")
        current = self.request("GET", path)
        run = current.get("run", current)
        manifest = (run.get("build_result") or {}).get("artifact_manifest") or {}
        if (run.get("status") != "awaiting_flash_confirmation" or run.get("approval_id") != approval_id
                or manifest.get("sha256") != manifest_sha256 or run.get("flash_port") != port):
            raise ValueError("FLASH_APPROVAL_BINDING_MISMATCH")
        return self.request("POST", path + "/confirm-flash", {"port": port})


@contextmanager
def engine(url=None):
    if url:
        yield EngineClient(url, os.environ.get("FORGEX_LOCAL_TOKEN"))
        return
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    token = secrets.token_urlsafe(32)
    environment = {**os.environ, "FORGEX_LOCAL_TOKEN": token, "FORGEX_BACKEND_PORT": str(port),
                   "FORGEX_BACKEND_URL": f"http://127.0.0.1:{port}", "FORGEX_ENABLE_SHADOW_HARNESS": "0"}
    environment.pop("FORGEX_HARNESS_URL", None)
    environment.pop("FORGEX_HARNESS_TOKEN", None)
    child = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.api.app:create_app", "--factory",
        "--host", "127.0.0.1", "--port", str(port), "--no-access-log"], env=environment,
        cwd=Path(__file__).resolve().parents[1], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        client = EngineClient(environment["FORGEX_BACKEND_URL"], token)
        deadline = time.monotonic() + 40
        while True:
            if child.poll() is not None:
                raise RuntimeError("ForgeX engine exited during startup. Run the serve command to inspect startup errors.")
            try:
                client.request("GET", "/health")
                break
            except (URLError, ValueError):
                if time.monotonic() >= deadline:
                    raise TimeoutError("ForgeX engine startup timed out.")
                time.sleep(0.2)
        yield client
    finally:
        child.terminate()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="forgex", description="Embedded engineering tasks through the shared ForgeX engine")
    parser.add_argument("--url", default=os.environ.get("FORGEX_BACKEND_URL"), help="Attach to a loopback engine; requires FORGEX_LOCAL_TOKEN")
    commands = parser.add_subparsers(dest="command", required=True)
    profile = commands.add_parser("profile", help="Inspect project configuration and existing compiler metadata")
    profile.add_argument("project")
    profile.add_argument("--environment")
    serve = commands.add_parser("serve", help="Start the engine without Electron")
    serve.add_argument("--port", type=int, default=8000)
    run = commands.add_parser("run")
    run.add_argument("project")
    run.add_argument("instruction")
    run.add_argument("--mode", choices=MODES, default="review")
    run.add_argument("--environment")
    run.add_argument("--provider")
    run.add_argument("--model")
    run.add_argument("--trust", action="store_true", help="Trust this project to execute builds and tests")
    commands.add_parser("jobs")
    commands.add_parser("tools", help="Read the host's canonical tool schemas and capability profiles")
    for name in ("job-status", "pause", "resume"):
        command = commands.add_parser(name)
        command.add_argument("job_id")
        if name == "resume":
            command.add_argument("--key", help="Idempotency key for this explicit resume")
    for name in ("status", "changes", "apply", "cancel"):
        command = commands.add_parser(name)
        command.add_argument("run_id")
    flash = commands.add_parser("confirm-flash", help="Consume an existing one-time approval; never builds firmware")
    flash.add_argument("run_id")
    flash.add_argument("--approval", required=True)
    flash.add_argument("--manifest", required=True)
    flash.add_argument("--port", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "profile":
            from .services.project_profile import inspect_project_profile
            print(inspect_project_profile(args.project, environment=args.environment).model_dump_json(indent=2))
            return 0
        if args.command == "serve":
            if not os.environ.get("FORGEX_LOCAL_TOKEN"):
                raise ValueError("Set FORGEX_LOCAL_TOKEN before starting a persistent engine.")
            os.environ["FORGEX_BACKEND_PORT"] = str(args.port)
            import uvicorn
            uvicorn.run("backend.api.app:create_app", factory=True, host="127.0.0.1", port=args.port, access_log=False)
            return 0
        if args.command != "run" and not args.url:
            raise ValueError("Use --url to attach to the engine that owns this job.")
        with engine(args.url) as client:
            if args.command == "run":
                result = client.run(args.project, args.instruction, mode=args.mode, environment=args.environment,
                    provider=args.provider, model=args.model, trust=args.trust)
            elif args.command == "confirm-flash":
                result = client.confirm_flash(args.run_id, approval_id=args.approval, manifest_sha256=args.manifest, port=args.port)
            elif args.command == "jobs":
                result = client.request("GET", "/agent-runtime/jobs")
            elif args.command == "tools":
                result = client.request("GET", "/agent-runtime/tools")
            elif args.command in {"job-status", "pause", "resume"}:
                path = "/agent-runtime/jobs/" + quote(args.job_id, safe="")
                suffix = {"job-status":"", "pause":"/pause", "resume":"/resume"}[args.command]
                body = {"idempotency_key": args.key or secrets.token_hex(16)} if args.command == "resume" else {}
                result = client.request("GET" if args.command == "job-status" else "POST", path + suffix,
                                        None if args.command == "job-status" else body)
            else:
                path = "/agent-runtime/runs/" + quote(args.run_id, safe="")
                suffix = {"status": "", "changes": "/changes", "apply": "/apply-changes", "cancel": "/cancel"}[args.command]
                result = client.request("GET" if args.command in {"status", "changes"} else "POST", path + suffix,
                                        None if args.command in {"status", "changes"} else {})
            print(json.dumps(result, indent=2, ensure_ascii=False))
            status = result.get("status") or (result.get("run") or {}).get("status")
            return 1 if status in {"failed", "blocked", "timed_out", "cancelled", "paused_model_unavailable"} else 0
    except KeyboardInterrupt:
        return 130
    except (ValueError, OSError, RuntimeError, TimeoutError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
