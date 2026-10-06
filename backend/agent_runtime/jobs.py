"""Persistent objectives above run attempts; no effect replay authority."""
from __future__ import annotations

import hashlib
import json
import random
import uuid
from backend.model_router.run_budget import AgentBudgetExceeded
from contextlib import closing
from datetime import datetime, timedelta, timezone

from .orchestration_store import utc_now, _encode, _decode

DEFAULT_LIMITS = {"elapsed_seconds": 900, "tool_calls_used": 100,
                  "requests": 120, "output_tokens": 245760, "input_bytes": 8000000}
EXECUTION_PROFILES = {
    "standard": DEFAULT_LIMITS,
    "advanced": {"elapsed_seconds": 2700, "tool_calls_used": 300,
                 "requests": 360, "output_tokens": 737280, "input_bytes": 24000000},
}
TERMINAL = {"completed", "completed_with_warning", "failed", "cancelled", "blocked", "timed_out"}
TRANSIENT_PROVIDER_ERRORS = frozenset({"API_RATE_LIMITED", "PROVIDER_RATE_LIMITED", "API_NETWORK_ERROR",
    "PROVIDER_TIMEOUT", "PROVIDER_BUSY", "PROVIDER_UNAVAILABLE"})
OUTAGE_WINDOW_SECONDS = 600
MAX_RECOVERY_CYCLES = 3


def _upgrade(value):
    if value.get("schema_version", 1) < 2 and value.get("status") == "paused_model_unavailable":
        started = value.get("updated_at") or utc_now()
        value.setdefault("outage", {"started_at": started,
            "deadline_at": (_when(started) + timedelta(seconds=OUTAGE_WINDOW_SECONDS)).isoformat(),
            "cycles": value.get("auto_recovery_count", 0)})
    value["schema_version"] = 3
    value.setdefault("outage", None)
    value.setdefault("durable_wait_seconds", 0.0)
    return value


def _now():
    return datetime.now(timezone.utc)


def _when(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _finish_wait(value):
    started = value.pop("durable_wait_started_at", None)
    if started:
        value["durable_wait_seconds"] = value.get("durable_wait_seconds", 0) + max(0, (_now() - _when(started)).total_seconds())


def _outage_block(value, now=None):
    outage = value.get("outage")
    if not outage:
        return None
    now = now or _now()
    if outage["cycles"] >= MAX_RECOVERY_CYCLES:
        return "AGENT_AUTO_RECOVERY_LIMIT"
    if now >= _when(outage["deadline_at"]) or (value.get("retry_due_at") and _when(value["retry_due_at"]) >= _when(outage["deadline_at"])):
        return "AGENT_OUTAGE_WINDOW_EXHAUSTED"
    return None


def recovery_time_reserve(limits):
    """Leave enough runtime for one useful provider turn and validation."""
    return min(120.0, max(30.0, float(limits["elapsed_seconds"]) * 0.2))


class AgentJobStore:
    def __init__(self, store):
        self.store = store
        from .engine import shared_engine_enabled
        with store._transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS agent_provider_attempts (
                attempt_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, run_id TEXT NOT NULL,
                reserved_output INTEGER NOT NULL, actual_output INTEGER,
                status TEXT NOT NULL, created_at TEXT NOT NULL, settled_at TEXT)""")
            db.execute("CREATE TABLE IF NOT EXISTS agent_review_units(job_id TEXT NOT NULL, cache_key TEXT NOT NULL, unit_id TEXT NOT NULL, payload_json TEXT NOT NULL, checksum TEXT NOT NULL, PRIMARY KEY(job_id,cache_key,unit_id))")
            for identity, payload in db.execute("SELECT job_id,payload_json FROM agent_jobs").fetchall():
                value = _upgrade(_decode(payload))
                if not shared_engine_enabled():
                    value["recovery_policy"] = "manual"
                encoded = _encode(value)
                if encoded != payload:
                    db.execute("UPDATE agent_jobs SET payload_json=? WHERE job_id=?", (encoded, identity))

    def create(self, *, project_id, session_id, workspace_root, run_id, legacy=False,
               execution_profile="standard"):
        from .engine import shared_engine_enabled
        if execution_profile not in EXECUTION_PROFILES:
            raise ValueError("AGENT_EXECUTION_PROFILE_INVALID")
        limits = EXECUTION_PROFILES[execution_profile]
        identity = "job-" + uuid.uuid4().hex
        now = utc_now()
        value = {"schema_version": 2, "job_id": identity, "project_id": project_id,
                 "session_id": session_id, "workspace_root": str(workspace_root),
                 "current_run_id": run_id, "run_ids": [run_id], "status": "running",
                 "recovery_policy": "manual" if legacy or not shared_engine_enabled() else "safe_auto", "paused_by": None,
                 "execution_profile": execution_profile,
                 "limits": dict(limits), "consumed": {key: 0 for key in limits},
                 "milestones": [], "blocking_reason": None, "auto_recovery_count": 0,
                 "retry_due_at": None, "created_at": now, "updated_at": now, "pinned": False,
                 "outage": None, "durable_wait_seconds": 0.0}
        self.save(value)
        return value

    def save(self, value):
        value = _upgrade({**_decode(_encode(value)), "updated_at": utc_now()})
        with self.store._transaction() as connection:
            prior = connection.execute("SELECT payload_json FROM agent_jobs WHERE job_id=?",(value["job_id"],)).fetchone()
            if prior:
                previous = _decode(prior[0])
                for key in value["consumed"]:
                    value["consumed"][key] = (previous["consumed"][key] if key == "output_tokens" and previous.get("attempt_ledger") else max(value["consumed"][key],previous["consumed"][key]))
                    value["limits"][key] = max(value["limits"][key],previous["limits"][key])
                if previous.get("attempt_ledger"):
                    value["attempt_ledger"] = True
                    value["output_tokens_reserved"] = previous.get("output_tokens_reserved", 0)
            connection.execute("""INSERT INTO agent_jobs(job_id,project_id,session_id,status,payload_json,updated_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET status=excluded.status,
                payload_json=excluded.payload_json,updated_at=excluded.updated_at""",
                (value["job_id"], value["project_id"], value["session_id"], value["status"], _encode(value), value["updated_at"]))
        return value

    def mutate(self, identity, change):
        with self.store._transaction() as connection:
            row=connection.execute("SELECT payload_json FROM agent_jobs WHERE job_id=?",(identity,)).fetchone()
            if not row:
                raise KeyError(identity)
            value=_upgrade(_decode(row[0]))
            change(value)
            if _encode(value) == row[0]:
                return value
            value["updated_at"]=utc_now()
            connection.execute("UPDATE agent_jobs SET status=?,payload_json=?,updated_at=? WHERE job_id=?",(value["status"],_encode(value),value["updated_at"],identity))
            return value

    def get(self, identity):
        with closing(self.store._connect()) as connection:
            row = connection.execute("SELECT payload_json FROM agent_jobs WHERE job_id=?", (identity,)).fetchone()
        if not row:
            raise KeyError(identity)
        return _upgrade(_decode(row[0]))

    def list(self, project_id=None):
        with closing(self.store._connect()) as connection:
            rows = connection.execute("SELECT payload_json FROM agent_jobs" +
                (" WHERE project_id=?" if project_id else "") + " ORDER BY updated_at DESC",
                (project_id,) if project_id else ()).fetchall()
        return tuple(_upgrade(_decode(row[0])) for row in rows)

    def attach(self, identity, run_id):
        def change(value):
            _finish_wait(value)
            value.pop("recovery_claim", None)
            if run_id not in value["run_ids"]:
                value["run_ids"].append(run_id)
            value.update(current_run_id=run_id,status="running",retry_due_at=None,blocking_reason=None)
            value.pop("request_recovery", None)
        return self.mutate(identity,change)

    def update_run(self, identity, run, checkpoint=None):
        def change(value):
            if value["current_run_id"] != run.run_id:
                return
            saved=(checkpoint or {}).get("resume") or checkpoint or {}
            provider=saved.get("provider_budget") or {}
            for key in value["consumed"]:
                if key == "output_tokens" and value.get("attempt_ledger"):
                    continue  # Physical reservation/settlement owns this dimension.
                used=saved.get(key,provider.get(key,0))
                if isinstance(used,(int,float)) and not isinstance(used,bool):
                    value["consumed"][key]=max(value["consumed"][key],max(0,used))
            value["milestones"]=list(getattr(run,"agent_plan",()) or saved.get("plan",()))
            value["status"]="paused" if value.get("paused_by") else "interrupted" if run.classification=="AGENT_RUNTIME_INTERRUPTED" else run.status
            if run.status != "running":
                value.pop("request_recovery", None)
            if run.status in {"completed", "completed_with_warning", "cancelled"}:
                _finish_wait(value)
                value.update(outage=None, retry_due_at=None)
            if value.get("blocking_reason") == "AGENT_RESUME_WORKSPACE_UNAVAILABLE":
                _finish_wait(value)
                value["retry_due_at"] = None
                if run.status not in {"completed", "completed_with_warning", "cancelled"}:
                    value["status"] = "blocked"
                return
            if (run.status == "blocked" and run.classification == "AGENT_PROJECT_TRUST_REQUIRED"
                    and not self.is_trusted(value["project_id"], value["workspace_root"])):
                _finish_wait(value)
                value.update(blocking_reason="AGENT_PROJECT_TRUST_REQUIRED", retry_due_at=None)
            if run.status=="paused_model_unavailable" and run.classification in TRANSIENT_PROVIDER_ERRORS and not value.get("retry_due_at"):
                now = _now()
                if not value.get("outage"):
                    value["outage"] = {"started_at": now.isoformat(), "deadline_at": (now + timedelta(seconds=OUTAGE_WINDOW_SECONDS)).isoformat(),
                        "cycles": value.get("auto_recovery_count", 0), "provider_id": getattr(run, "provider_id", None), "model_id": getattr(run, "model_id", None)}
                value["outage"].update(error_code=run.classification, http_status=getattr(run,"http_status",None),
                    origin=getattr(run,"last_failure_origin",None) or "provider", request_reached_provider=bool(getattr(run,"request_reached_provider",False)))
                base_delay = 30 if run.classification in {"PROVIDER_UNAVAILABLE", "PROVIDER_TIMEOUT", "API_NETWORK_ERROR"} else 5
                delay=max(min(120,base_delay*2**min(value.get("auto_recovery_count",0),3))*random.uniform(1.0,1.25),float(getattr(run,"retry_after_ms",0) or 0)/1000)
                value["retry_due_at"]=(datetime.now(timezone.utc)+timedelta(seconds=delay)).isoformat().replace("+00:00","Z")
                value["durable_wait_started_at"] = utc_now()
                reason = _outage_block(value)
                if reason:
                    value.update(status="paused_model_unavailable", blocking_reason=reason)
            if run.status=="paused_model_unavailable" and run.classification in TRANSIENT_PROVIDER_ERRORS and (
                value["limits"]["elapsed_seconds"] - value["consumed"]["elapsed_seconds"] < recovery_time_reserve(value["limits"])
            ):
                value.update(status="paused", paused_by="budget",
                             blocking_reason="AGENT_JOB_TIME_RESERVE_REQUIRED", retry_due_at=None)
            if run.status not in {"completed","completed_with_warning"} and (run.classification in {
                "AGENT_PROVIDER_BUDGET_EXCEEDED","AGENT_JOB_BUDGET_EXHAUSTED","AGENT_RUNTIME_TOOL_BUDGET_EXCEEDED","AGENT_RUNTIME_LIMIT_EXCEEDED", "AGENT_VERIFICATION_RESERVE_REACHED"}
                or any(value["consumed"][key]>=limit for key,limit in value["limits"].items())):
                value.update(status="paused",paused_by="budget",blocking_reason="AGENT_JOB_BUDGET_EXHAUSTED")
        return self.mutate(identity,change)

    def public(self, value):
        value = _upgrade(dict(value))
        # Include the current cooldown in the projection without advancing
        # active runtime or changing the stored start of the waiting interval.
        _finish_wait(value)
        recovery = value.get("request_recovery")
        if value.get("outage") and not recovery:
            outage = value["outage"]
            phase = "blocked" if value.get("blocking_reason") or value.get("paused_by") else "probe" if value.get("recovery_claim") else "retry_wait" if value.get("retry_due_at") and _when(value["retry_due_at"]) > _now() else "ready_to_resume"
            recovery = {**outage, "phase": phase,
                "attempt": outage["cycles"], "max_attempts": MAX_RECOVERY_CYCLES,
                "retry_due_at": value.get("retry_due_at"), "outage_deadline_at": outage["deadline_at"]}
        if recovery:
            from backend.contracts.recovery import AgentRecoveryState
            recovery = AgentRecoveryState.model_validate(recovery).model_dump(mode="json")
        return {key: value[key] for key in ("schema_version", "job_id", "project_id", "session_id",
            "current_run_id", "run_ids", "status", "recovery_policy", "paused_by", "milestones",
            "blocking_reason", "auto_recovery_count", "retry_due_at", "created_at", "updated_at")} | {
            "execution_profile": value.get("execution_profile", "standard"),
            "recovery": recovery,
            "durable_wait_seconds": value["durable_wait_seconds"],
            "budget": {"limits": value["limits"], "consumed": value["consumed"],
                       "accounting": self.attempt_accounting(value["job_id"]),
                       "remaining": {key: max(0, limit-value["consumed"][key]) for key, limit in value["limits"].items()}}}

    def record_retry(self, identity, run_id, event):
        def change(value):
            if value["current_run_id"] != run_id:
                return
            phase = str(event["event_type"]).rsplit(".", 1)[-1]
            if phase == "settled":
                value.pop("request_recovery", None)
                _finish_wait(value)
                value.update(outage=None, auto_recovery_count=0, retry_due_at=None)
                return
            value["request_recovery"] = {"phase": "retrying" if phase == "scheduled" else "running" if phase == "started" else "cancelled",
                **{key: event.get(key) for key in ("attempt", "max_attempts", "error_code", "retry_due_at", "http_status",
                    "provider_id", "model_id", "origin", "request_reached_provider")}}
        return self.mutate(identity, change)

    def begin_manual_recovery(self, identity):
        def change(value):
            _finish_wait(value)
            value.update(outage=None, auto_recovery_count=0)
            value.pop("recovery_claim", None)
            if value.get("blocking_reason") in {"AGENT_AUTO_RECOVERY_LIMIT", "AGENT_OUTAGE_WINDOW_EXHAUSTED"}:
                value["blocking_reason"] = None
        return self.mutate(identity, change)

    def claim_recovery(self, identity, owner):
        """Only a due, bounded, unpaused job can be claimed by a scheduler."""
        claimed = False
        def change(value):
            nonlocal claimed
            now = _now()
            if value["recovery_policy"] != "safe_auto" or value.get("paused_by") or value["status"] not in {"paused_model_unavailable", "interrupted", "failed"}:
                return
            reason = _outage_block(value, now)
            if reason:
                value["blocking_reason"] = reason
                return
            if value.get("retry_due_at") and _when(value["retry_due_at"]) > now:
                return
            claim = value.get("recovery_claim")
            if claim and _when(claim["expires_at"]) > now:
                return
            value["recovery_claim"] = {"owner": owner, "expires_at": (now + timedelta(seconds=90)).isoformat()}
            value["auto_recovery_count"] += 1
            if value.get("outage"):
                value["outage"]["cycles"] += 1
            claimed = True
        self.mutate(identity, change)
        return claimed

    def trust(self, project_id, workspace_root, trusted):
        value = {"project_id": project_id, "workspace_root": str(workspace_root),
                 "trusted": bool(trusted), "updated_at": utc_now()}
        with self.store._transaction() as connection:
            connection.execute("INSERT INTO agent_project_trust(project_id,payload_json) VALUES(?,?) "
                "ON CONFLICT(project_id) DO UPDATE SET payload_json=excluded.payload_json", (project_id, _encode(value)))
        return value

    def is_trusted(self, project_id, workspace_root):
        with closing(self.store._connect()) as connection:
            row = connection.execute("SELECT payload_json FROM agent_project_trust WHERE project_id=?", (project_id,)).fetchone()
        value = _decode(row[0]) if row else {}
        return value.get("trusted") is True and value.get("workspace_root") == str(workspace_root)

    def extend_budget(self, identity, extension, request_key):
        # A caller supplies an idempotency key so transport retries cannot multiply credit.
        allowed = {"elapsed_seconds", "tool_calls_used", "requests"}
        if set(extension) - allowed or any(type(v) is not int or not 1 <= v <= DEFAULT_LIMITS[k] for k,v in extension.items()):
            raise ValueError("AGENT_JOB_BUDGET_EXTENSION_INVALID")
        fingerprint = hashlib.sha256(json.dumps(extension,sort_keys=True).encode()).hexdigest()
        with self.store._transaction() as connection:
            row=connection.execute("SELECT fingerprint FROM agent_job_requests WHERE job_id=? AND request_key=?",(identity,request_key)).fetchone()
            stored=connection.execute("SELECT payload_json FROM agent_jobs WHERE job_id=?",(identity,)).fetchone()
            if not stored:
                raise KeyError(identity)
            value=_decode(stored[0])
            if row:
                if row[0] != fingerprint:
                    raise ValueError("AGENT_JOB_REQUEST_CONFLICT")
                return value
            for key,increment in extension.items():
                value["limits"][key]=min(DEFAULT_LIMITS[key]*10,value["limits"][key]+increment)
            if "requests" in extension:
                for key in ("output_tokens","input_bytes"):
                    value["limits"][key]=min(DEFAULT_LIMITS[key]*10,value["limits"][key]+DEFAULT_LIMITS[key]*extension["requests"]//DEFAULT_LIMITS["requests"])
            value.update(paused_by=None,blocking_reason=None,updated_at=utc_now())
            connection.execute("INSERT INTO agent_job_requests(job_id,request_key,fingerprint) VALUES(?,?,?)",(identity,request_key,fingerprint))
            connection.execute("UPDATE agent_jobs SET payload_json=?,updated_at=? WHERE job_id=?",(_encode(value),value["updated_at"],identity))
        return value

    def reserve_resume(self,identity,request_key,selection=None,checkpoint_id=None):
        fingerprint = hashlib.sha256(b"resume" if not selection and not checkpoint_id else _encode({"selection": selection, "checkpoint_id": checkpoint_id}).encode()).hexdigest()
        with self.store._transaction() as connection:
            row=connection.execute("SELECT fingerprint,payload_json FROM agent_job_requests WHERE job_id=? AND request_key=?",(identity,request_key)).fetchone()
            if row:
                if row[0] != fingerprint:
                    raise ValueError("AGENT_JOB_REQUEST_CONFLICT")
                return _decode(row[1])["source_run_id"]
            row=connection.execute("SELECT payload_json FROM agent_jobs WHERE job_id=?",(identity,)).fetchone()
            if not row:
                raise KeyError(identity)
            source=_decode(row[0])["current_run_id"]
            connection.execute("INSERT INTO agent_job_requests VALUES(?,?,?,?)",(identity,request_key,fingerprint,_encode({"source_run_id":source})))
            return source

    def persist_provider_budget(self, identity, consumed):
        with self.store._transaction() as connection:
            row=connection.execute("SELECT payload_json FROM agent_jobs WHERE job_id=?",(identity,)).fetchone()
            if not row:
                raise KeyError(identity)
            value=_decode(row[0])
            for key in ("requests","output_tokens","input_bytes"):
                if consumed[key]>value["limits"][key]:
                    raise AgentBudgetExceeded(key, value["consumed"][key],
                                              value["limits"][key],
                                              max(0, consumed[key]-value["consumed"][key]),
                                              code="AGENT_JOB_BUDGET_EXHAUSTED")
                value["consumed"][key]=max(value["consumed"][key],consumed[key])
            value["updated_at"]=utc_now()
            connection.execute("UPDATE agent_jobs SET payload_json=?,updated_at=? WHERE job_id=?",(_encode(value),value["updated_at"],identity))

    def reserve_provider_attempt(self, identity, run_id, charges):
        """Atomically charge every physical request, including independent reviews."""
        with self.store._transaction() as connection:
            row = connection.execute("SELECT payload_json FROM agent_jobs WHERE job_id=?", (identity,)).fetchone()
            if not row:
                raise KeyError(identity)
            value = _decode(row[0])
            if value["current_run_id"] != run_id or value["status"] not in {"running", "queued"} or value.get("paused_by"):
                raise ValueError("AGENT_JOB_NOT_RUNNING")
            attempt_id = charges.get("_attempt_id") or uuid.uuid4().hex
            previous = connection.execute("SELECT job_id,run_id,reserved_output FROM agent_provider_attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            if previous:
                if tuple(previous) != (identity, run_id, charges["output_tokens"]):
                    raise ValueError("AGENT_ATTEMPT_CONFLICT")
                return {key: value["consumed"][key] for key in ("requests", "output_tokens", "input_bytes")}
            reserved = {}
            for key in ("requests", "output_tokens", "input_bytes"):
                charge = charges[key]
                if not isinstance(charge, int) or isinstance(charge, bool) or charge < 0:
                    raise ValueError("AGENT_BUDGET_CHARGE_INVALID")
                used = value["consumed"][key]
                if used + charge > value["limits"][key]:
                    raise AgentBudgetExceeded(key, used, value["limits"][key], charge,
                                              code="AGENT_JOB_BUDGET_EXHAUSTED")
                reserved[key] = used + charge
            value["consumed"].update(reserved)
            value["attempt_ledger"] = True
            value["output_tokens_reserved"] = value.get("output_tokens_reserved", 0) + charges["output_tokens"]
            value["updated_at"] = utc_now()
            connection.execute("INSERT INTO agent_provider_attempts VALUES(?,?,?,?,?,?,?,?)",
                (attempt_id, identity, run_id, charges["output_tokens"], None, "unresolved", value["updated_at"], None))
            connection.execute("UPDATE agent_jobs SET payload_json=?,updated_at=? WHERE job_id=?",
                               (_encode(value), value["updated_at"], identity))
            return reserved

    def settle_provider_attempt(self, identity, attempt_id, actual_output):
        """Only explicit completed usage can release a reservation, exactly once."""
        if type(actual_output) is not int or actual_output < 0:
            raise ValueError("AGENT_ATTEMPT_USAGE_INVALID")
        with self.store._transaction() as connection:
            row = connection.execute("SELECT reserved_output,actual_output,status FROM agent_provider_attempts WHERE attempt_id=? AND job_id=?", (attempt_id, identity)).fetchone()
            if not row or actual_output > row[0]:
                raise ValueError("AGENT_ATTEMPT_USAGE_INVALID")
            payload = connection.execute("SELECT payload_json FROM agent_jobs WHERE job_id=?", (identity,)).fetchone()
            value = _decode(payload[0])
            if row[2] == "completed":
                if row[1] != actual_output:
                    raise ValueError("AGENT_ATTEMPT_USAGE_CONFLICT")
                return value["consumed"]
            value["consumed"]["output_tokens"] -= row[0] - actual_output
            value["updated_at"] = utc_now()
            connection.execute("UPDATE agent_provider_attempts SET actual_output=?,status='completed',settled_at=? WHERE attempt_id=?", (actual_output, value["updated_at"], attempt_id))
            connection.execute("UPDATE agent_jobs SET payload_json=?,updated_at=? WHERE job_id=?", (_encode(value), value["updated_at"], identity))
            return value["consumed"]

    def attempt_accounting(self, identity):
        with closing(self.store._connect()) as connection:
            row = connection.execute("SELECT COALESCE(SUM(reserved_output),0), COALESCE(SUM(CASE WHEN status='completed' THEN actual_output ELSE 0 END),0), COALESCE(SUM(CASE WHEN status='unresolved' THEN reserved_output ELSE 0 END),0), COUNT(*) FROM agent_provider_attempts WHERE job_id=?", (identity,)).fetchone()
        return dict(zip(("reserved_output_tokens", "actual_completed_output_tokens", "unresolved_output_tokens", "physical_attempts"), row))

    def review_units(self, identity, cache_key):
        with closing(self.store._connect()) as connection:
            rows = connection.execute("SELECT unit_id,payload_json,checksum FROM agent_review_units WHERE job_id=? AND cache_key=?", (identity, cache_key)).fetchall()
        return {row[0]: _decode(row[1]) for row in rows if hashlib.sha256(row[1].encode()).hexdigest() == row[2]}

    def save_review_unit(self, identity, cache_key, unit):
        encoded = _encode(unit)
        if len(encoded.encode()) > 16000:
            raise ValueError("SEMANTIC_REVIEW_UNIT_RESULT_TOO_LARGE")
        with self.store._transaction() as connection:
            connection.execute("INSERT INTO agent_review_units VALUES(?,?,?,?,?) ON CONFLICT(job_id,cache_key,unit_id) DO UPDATE SET payload_json=excluded.payload_json,checksum=excluded.checksum",
                (identity, cache_key, unit["unit_id"], encoded, hashlib.sha256(encoded.encode()).hexdigest()))

    def charge_tool(self, identity, operation_id):
        """Reserve a call before execution; transport events cannot double-charge."""
        key = "tool:" + operation_id
        with self.store._transaction() as connection:
            if connection.execute("SELECT 1 FROM agent_job_requests WHERE job_id=? AND request_key=?", (identity,key)).fetchone():
                return
            row = connection.execute("SELECT payload_json FROM agent_jobs WHERE job_id=?", (identity,)).fetchone()
            if not row:
                raise KeyError(identity)
            value = _decode(row[0])
            if value["consumed"]["tool_calls_used"] >= value["limits"]["tool_calls_used"]:
                raise ValueError("AGENT_JOB_BUDGET_EXHAUSTED")
            value["consumed"]["tool_calls_used"] += 1
            value["updated_at"] = utc_now()
            connection.execute("INSERT INTO agent_job_requests(job_id,request_key,fingerprint) VALUES(?,?,?)", (identity,key,"tool"))
            connection.execute("UPDATE agent_jobs SET payload_json=?,updated_at=? WHERE job_id=?", (_encode(value),value["updated_at"],identity))

    def record_elapsed(self, identity, elapsed):
        with self.store._transaction() as connection:
            row=connection.execute("SELECT payload_json FROM agent_jobs WHERE job_id=?",(identity,)).fetchone()
            if not row:
                raise KeyError(identity)
            value=_decode(row[0])
            value["consumed"]["elapsed_seconds"]=max(value["consumed"]["elapsed_seconds"],elapsed)
            value["updated_at"]=utc_now()
            connection.execute("UPDATE agent_jobs SET payload_json=?,updated_at=? WHERE job_id=?",(_encode(value),value["updated_at"],identity))

    def retire_checkpoints(self, *, days=30):
        cutoff = (datetime.now(timezone.utc)-timedelta(days=days)).isoformat().replace("+00:00", "Z")
        retired = []
        for value in self.list():
            if value["status"] not in TERMINAL or value.get("pinned") or value["updated_at"] >= cutoff:
                continue
            for run_id in value["run_ids"]:
                if not self.store.latest_run_checkpoint(run_id):
                    continue
                if any(op["status"] in {"started", "unknown"} for op in self.store.operations(run_id)):
                    continue
                payload = self.store.load_run(run_id) or {}
                if payload.get("recoverable") or payload.get("change_set_id"):
                    continue
                with self.store._transaction() as connection:
                    connection.execute("DELETE FROM agent_run_checkpoints WHERE run_id=?", (run_id,))
                    context = dict(payload.get("_recovery_context") or {})
                    for key in ("resume_checkpoint","last_safe_checkpoint"):
                        context.pop(key,None)
                    payload["_recovery_context"] = context
                    payload["checkpoint_retired_at"] = utc_now()
                    connection.execute("UPDATE agent_runs SET payload_json=? WHERE run_id=?",(_encode(payload),run_id))
                retired.append(run_id)
        return retired
