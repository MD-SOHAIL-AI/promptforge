"""Durable, read-only request intake before an execution mode is authorized."""
import hashlib
import uuid

from .jobs import EXECUTION_PROFILES
from .orchestration_store import _decode, _encode, utc_now
from backend.model_router.run_budget import AgentBudgetExceeded


class AgentIntakeStore:
    def __init__(self, store):
        self.store = store
        with store._transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS agent_intakes (intake_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, fingerprint TEXT NOT NULL, payload_json TEXT NOT NULL)")

    def create(self, session_id, project_id, request, profile="standard"):
        fingerprint = hashlib.sha256(request.encode()).hexdigest()
        with self.store._transaction() as db:
            rows = db.execute("SELECT payload_json FROM agent_intakes WHERE session_id=? AND fingerprint=?", (session_id, fingerprint)).fetchall()
            for row in rows:
                prior = _decode(row[0])
                if prior["status"] == "paused_model_unavailable" and not prior.get("job_id"):
                    return prior
            value = {"intake_id": "intake-" + uuid.uuid4().hex, "session_id": session_id, "project_id": project_id,
                "fingerprint": fingerprint, "status": "running", "limits": dict(EXECUTION_PROFILES[profile]),
                "consumed": {key: 0 for key in EXECUTION_PROFILES[profile]}, "created_at": utc_now()}
            db.execute("INSERT INTO agent_intakes VALUES(?,?,?,?)", (value["intake_id"], session_id, fingerprint, _encode(value)))
            return value

    def reserve(self, identity, charges):
        with self.store._transaction() as db:
            value = _decode(db.execute("SELECT payload_json FROM agent_intakes WHERE intake_id=?", (identity,)).fetchone()[0])
            for key, charge in charges.items():
                used = value["consumed"][key]
                if used + charge > value["limits"][key]:
                    raise AgentBudgetExceeded(key, used, value["limits"][key], charge)
                value["consumed"][key] += charge
            db.execute("UPDATE agent_intakes SET payload_json=? WHERE intake_id=?", (_encode(value), identity))
            return value["consumed"]

    def finish(self, identity, status, elapsed, failure=None):
        with self.store._transaction() as db:
            value = _decode(db.execute("SELECT payload_json FROM agent_intakes WHERE intake_id=?", (identity,)).fetchone()[0])
            value.update(status=status, failure=failure, updated_at=utc_now())
            value["consumed"]["elapsed_seconds"] += elapsed
            db.execute("UPDATE agent_intakes SET payload_json=? WHERE intake_id=?", (_encode(value), identity))
            return value

    def attach(self, identity, job_id):
        with self.store._transaction() as db:
            row = db.execute("SELECT payload_json FROM agent_intakes WHERE intake_id=?", (identity,)).fetchone()
            if not row:
                raise ValueError("AGENT_INTAKE_NOT_FOUND")
            value = _decode(row[0])
            if value.get("job_id") and value["job_id"] != job_id:
                raise ValueError("AGENT_INTAKE_ALREADY_ATTACHED")
            job = _decode(db.execute("SELECT payload_json FROM agent_jobs WHERE job_id=?", (job_id,)).fetchone()[0])
            if job["session_id"] != value["session_id"] or job["project_id"] != value["project_id"]:
                raise ValueError("AGENT_INTAKE_BINDING_MISMATCH")
            if not value.get("job_id"):
                for key, used in value["consumed"].items():
                    job["consumed"][key] += used
                value["job_id"] = job_id
                job["intake_id"] = identity
                db.execute("UPDATE agent_jobs SET payload_json=? WHERE job_id=?", (_encode(job), job_id))
                db.execute("UPDATE agent_intakes SET payload_json=? WHERE intake_id=?", (_encode(value), identity))
            return job
