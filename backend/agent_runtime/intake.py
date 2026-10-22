"""Durable, read-only request intake before an execution mode is authorized."""
import hashlib
import uuid

from .jobs import EXECUTION_PROFILES
from .orchestration_store import _decode, _encode, utc_now
from backend.model_router.run_budget import AgentBudgetExceeded, validate_attempt_charges


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
                    prior["status"] = "running"
                    db.execute("UPDATE agent_intakes SET payload_json=? WHERE intake_id=?", (_encode(prior), prior["intake_id"]))
                    return prior
            value = {"intake_id": "intake-" + uuid.uuid4().hex, "session_id": session_id, "project_id": project_id,
                "fingerprint": fingerprint, "status": "running", "limits": dict(EXECUTION_PROFILES[profile]),
                "consumed": {key: 0 for key in EXECUTION_PROFILES[profile]}, "created_at": utc_now()}
            db.execute("INSERT INTO agent_intakes VALUES(?,?,?,?)", (value["intake_id"], session_id, fingerprint, _encode(value)))
            return value

    def reserve(self, identity, charges):
        numeric, attempt_id = validate_attempt_charges(charges)
        attempt_id = attempt_id or uuid.uuid4().hex
        with self.store._transaction() as db:
            row = db.execute("SELECT payload_json FROM agent_intakes WHERE intake_id=?", (identity,)).fetchone()
            if not row:
                raise ValueError("AGENT_INTAKE_NOT_FOUND")
            value = _decode(row[0])
            if value["status"] != "running" or value.get("job_id"):
                raise ValueError("AGENT_INTAKE_NOT_RUNNING")
            attempts = value.setdefault("attempts", {})
            previous = attempts.get(attempt_id)
            if previous:
                if previous["charges"] != numeric:
                    raise ValueError("AGENT_ATTEMPT_CONFLICT")
                return value["consumed"]
            for key, charge in numeric.items():
                used = value["consumed"][key]
                if used + charge > value["limits"][key]:
                    raise AgentBudgetExceeded(key, used, value["limits"][key], charge)
                value["consumed"][key] += charge
            attempts[attempt_id] = {"charges": numeric, "actual_output": None, "created_at": utc_now()}
            db.execute("UPDATE agent_intakes SET payload_json=? WHERE intake_id=?", (_encode(value), identity))
            return value["consumed"]

    def settle(self, identity, attempt_id, actual_output):
        with self.store._transaction() as db:
            row = db.execute("SELECT payload_json FROM agent_intakes WHERE intake_id=?", (identity,)).fetchone()
            if not row:
                raise ValueError("AGENT_INTAKE_NOT_FOUND")
            value = _decode(row[0])
            attempt = value.get("attempts", {}).get(attempt_id)
            if (not attempt or value.get("job_id") or type(actual_output) is not int
                    or not 0 <= actual_output <= attempt["charges"]["output_tokens"]):
                raise ValueError("AGENT_ATTEMPT_USAGE_INVALID")
            if attempt["actual_output"] is not None:
                if attempt["actual_output"] != actual_output:
                    raise ValueError("AGENT_ATTEMPT_CONFLICT")
                return value["consumed"]
            value["consumed"]["output_tokens"] -= attempt["charges"]["output_tokens"] - actual_output
            attempt.update(actual_output=actual_output, settled_at=utc_now())
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
                    if job["consumed"][key] + used > job["limits"][key]:
                        raise AgentBudgetExceeded(key, job["consumed"][key], job["limits"][key], used)
                    job["consumed"][key] += used
                for attempt_id, attempt in value.get("attempts", {}).items():
                    db.execute("INSERT INTO agent_provider_attempts VALUES(?,?,?,?,?,?,?,?)",
                        (attempt_id, job_id, job["current_run_id"], attempt["charges"]["output_tokens"],
                         attempt["actual_output"], "completed" if attempt["actual_output"] is not None else "unresolved",
                         attempt["created_at"], attempt.get("settled_at")))
                if value.get("attempts"):
                    job["attempt_ledger"] = True
                value["job_id"] = job_id
                job["intake_id"] = identity
                db.execute("UPDATE agent_jobs SET payload_json=? WHERE job_id=?", (_encode(job), job_id))
                db.execute("UPDATE agent_intakes SET payload_json=? WHERE intake_id=?", (_encode(value), identity))
            return job
