"""Addressable build observations. Restart never replays an uncertain build."""
from __future__ import annotations
import asyncio
import hashlib
from contextlib import closing
from backend.agent_runtime.orchestration_store import _encode, _decode, utc_now


class BuildOperations:
    def __init__(self, store):
        self.store = store
        self.tasks = {}
        with store._transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS desktop_build_operations(operation_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, payload_json TEXT NOT NULL)")
            for identity, payload in db.execute("SELECT operation_id,payload_json FROM desktop_build_operations").fetchall():
                value = _decode(payload)
                if value["state"] in {"queued", "running"}:
                    value.update(state="interrupted", message="The backend restarted. Inspect this operation before starting another build.", updated_at=utc_now())
                    db.execute("UPDATE desktop_build_operations SET payload_json=? WHERE operation_id=?", (_encode(value), identity))

    def get(self, identity):
        with closing(self.store._connect()) as db:
            row = db.execute("SELECT payload_json FROM desktop_build_operations WHERE operation_id=?", (identity,)).fetchone()
        if not row:
            raise KeyError(identity)
        return _decode(row[0])

    def accept(self, identity, *, project_id, environment, source_revision, execute):
        fingerprint = hashlib.sha256(_encode({"project_id": project_id, "environment": environment}).encode()).hexdigest()
        with self.store._transaction() as db:
            prior = db.execute("SELECT fingerprint,payload_json FROM desktop_build_operations WHERE operation_id=?", (identity,)).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise ValueError("BUILD_OPERATION_CONFLICT")
                return _decode(prior[1])
            value = dict(operation_id=identity, project_id=project_id, environment=environment,
                source_revision=source_revision, state="queued", created_at=utc_now(), updated_at=utc_now(), result=None)
            db.execute("INSERT INTO desktop_build_operations VALUES(?,?,?)", (identity, fingerprint, _encode(value)))
        self.tasks[identity] = asyncio.create_task(self._run(value, execute))
        return value

    def _save(self, value):
        value["updated_at"] = utc_now()
        with self.store._transaction() as db:
            db.execute("UPDATE desktop_build_operations SET payload_json=? WHERE operation_id=?", (_encode(value), value["operation_id"]))

    async def _run(self, value, execute):
        try:
            value["state"] = "running"
            self._save(value)
            response, revision = await execute()
            value.update(result=response, state="stale" if revision != value["source_revision"] else "passed" if response["result"]["success"] else "failed")
        except asyncio.CancelledError:
            value.update(state="interrupted", message="Build interrupted; its result is unknown.")
            raise
        except Exception as exc:
            value.update(state="unavailable", message="Build result could not be collected.", error_code=getattr(exc, "code", "BUILD_OPERATION_FAILED"))
        finally:
            self._save(value)
            self.tasks.pop(value["operation_id"], None)

    async def close(self):
        tasks = tuple(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
