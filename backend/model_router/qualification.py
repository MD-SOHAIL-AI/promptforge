"""Published task-test scopes, separate from catalog prices and endpoint health."""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
from pathlib import Path


_RECORDS = Path(__file__).parent / "data/task-qualifications.json"


@lru_cache(maxsize=2)
def _read_records(path, mtime_ns):
    del mtime_ns
    file = Path(path)
    if file.is_symlink() or file.stat().st_size > 512_000:
        return {}
    value = json.loads(file.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) and value.get("schema_version") == 1 else {}


def model_qualification(provider_id, model_id, endpoint, *, records=None, fingerprint=None):
    from backend.contracts.progress import ModelQualification
    from backend.runtime_contract import BACKEND_SOURCE_FINGERPRINT
    fingerprint = fingerprint or BACKEND_SOURCE_FINGERPRINT
    if records is None:
        try:
            records = _read_records(str(_RECORDS), _RECORDS.stat().st_mtime_ns)
        except (OSError, ValueError, TypeError):
            records = {}
    endpoint_hash = hashlib.sha256((endpoint or "").rstrip("/").encode()).hexdigest()
    scopes = []
    items = records.get("models", []) if isinstance(records, dict) else []
    if not isinstance(items, list):
        items = []
    for item in items[:256]:
        if not isinstance(item, dict) or (item.get("provider_id"), item.get("model_id")) != (provider_id, model_id):
            continue
        if item.get("source_fingerprint") != fingerprint or item.get("endpoint_sha256") != endpoint_hash:
            continue
        if (item.get("reviewed") is not True or item.get("critical_passed") is not True
                or type(item.get("generation_passes")) is not int or not 86 <= item["generation_passes"] <= 90
                or type(item.get("maintenance_passes")) is not int or not 57 <= item["maintenance_passes"] <= 60
                or type(item.get("generation_attempts")) is not int or item["generation_attempts"] != 90
                or type(item.get("maintenance_attempts")) is not int or item["maintenance_attempts"] != 60):
            continue
        if (item.get("execution_profile") not in {"standard", "advanced"}
                or not all(isinstance(item.get(key), str) and 0 < len(item[key]) <= 256 for key in ("target", "framework", "checked_at"))):
            continue
        scopes.append({key: item[key] for key in ("target", "framework", "execution_profile", "checked_at", "source_fingerprint")})
    if not scopes:
        return ModelQualification().model_dump(mode="json")
    return ModelQualification(status="tested", scopes=scopes,
        message="Task tests passed for the listed target, framework, profile and runtime. Other configurations remain untested.").model_dump(mode="json")
