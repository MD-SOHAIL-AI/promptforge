"""Host-owned deliverable obligations and bounded, evidence-based progress."""
from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Mapping, Sequence


_SOURCE_SUFFIXES = {".c", ".cpp", ".cc", ".cxx", ".ino", ".s", ".rs"}
_PATH = re.compile(r"(?<![A-Za-z0-9_./-])(?:platformio\.ini|README\.md|(?:src|include|test|tests|lib|docs)/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.(?:cpp|c|cc|cxx|h|hpp|ino|rs|py|ini|md))\b", re.I)


def explicit_artifacts(text: str) -> list[str]:
    return list(dict.fromkeys(_PATH.findall(text)))[:64]


def has_firmware_source(paths: Sequence[str]) -> bool:
    return any(PurePosixPath(path).suffix.casefold() in _SOURCE_SUFFIXES
               and ("/" not in path or path.startswith("src/")) for path in paths)


def infer_deliverables(contract: Mapping[str, object]) -> list[dict[str, object]]:
    """Use active requirements and acceptance checks; exclusions stay excluded."""
    requirements = contract.get("requirements") or ()
    text = "\n".join(str(item.get("text") or "") for item in requirements if isinstance(item, Mapping))
    if "requirements" not in contract:
        text = str(contract.get("request") or "")
    result = [{"id": "file:" + path, "kind": "file", "path": path}
              for path in explicit_artifacts(text)]
    kinds = [item.get("kind") for item in result]
    is_project = bool(re.search(r"\b(?:create|generate|build|complete)\b", text, re.I)
                      and re.search(r"\b(?:firmware|embedded|esp32|arduino|platformio)\b", text, re.I))
    for kind, requested in (
        ("firmware_source", is_project),
        ("project_configuration", is_project and bool(re.search(r"\bplatformio\b", text, re.I))),
        ("documentation", bool(re.search(r"\b(?:readme|documentation|wiring|calibration|test checklist)\b", text, re.I))),
    ):
        if requested and kind not in kinds:
            result.append({"id": kind, "kind": kind})
    for check in contract.get("acceptance_checks") or ():
        if isinstance(check, Mapping):
            result.append({"id": "check:" + str(check["id"]), "kind": "acceptance",
                           "check_kind": check["kind"], "criterion": check["criterion"]})
    documentation = next((item for item in result if item["kind"] == "documentation"), None)
    if documentation is not None:
        documentation["paths"] = [item["path"] for item in result if item["kind"] == "file" and str(item["path"]).casefold().endswith((".md", ".txt"))] or ["README.md"]
        documentation["topics"] = [topic for topic, pattern in (
            ("wiring", r"\bwiring\b"), ("calibration", r"\bcalibrat\w*\b"),
            ("test_checklist", r"\b(?:test(?:ing)? checklist|testing procedure)\b"),
            ("setup", r"\b(?:setup|installation)\b"), ("usage", r"\b(?:usage|instructions)\b")) if re.search(pattern, text, re.I)]
        documentation["requirement_ids"] = [str(item["id"]) for item in requirements if isinstance(item, Mapping)
            and re.search(r"\b(?:readme|documentation|wiring|calibration|test checklist|instructions)\b", str(item.get("text", "")), re.I)]
    return result[:96]


def project_deliverables(contract: Mapping[str, object], *, paths: Sequence[str],
                         build: Mapping[str, object], report: Mapping[str, object],
                         source_revision: str) -> list[dict[str, object]]:
    manifest = contract.get("deliverables") or infer_deliverables(contract)
    result = []
    for item in manifest:
        if not isinstance(item, Mapping):
            continue
        kind = item.get("kind")
        present = (item.get("path") in paths if kind == "file" else
                   has_firmware_source(paths) if kind == "firmware_source" else
                   "platformio.ini" in paths if kind == "project_configuration" else
                   all(path in paths for path in item.get("paths", ("README.md",))) if kind == "documentation" else False)
        # Presence is only a work hint. It never establishes feature correctness.
        status = "present" if present else "missing"
        if present:
            from .verification import reviewed_current
            required = set(item.get("requirement_ids") or ())
            reviewed = {check["id"] for check in report.get("checks", ()) if check.get("status") == "reviewed"}
            if reviewed_current(report, source_revision, contract.get("revision")) and (kind != "documentation" or required and required <= reviewed):
                status = "reviewed"
                if kind in {"file", "firmware_source", "project_configuration"} and build.get("success") is True and build.get("workspace_revision") == source_revision:
                    status = "built"
        if kind == "acceptance":
            from .verification import reviewed_current
            acceptance = report.get("acceptance") or {}
            check_passed = (isinstance(acceptance, Mapping)
                and acceptance.get("source_revision") == source_revision
                and acceptance.get("task_revision") == contract.get("revision")
                and any(check.get("kind") == item.get("check_kind") and check.get("passed") is True
                        for check in acceptance.get("checks", ()) if isinstance(check, Mapping)))
            passed = ((build.get("success") is True and build.get("workspace_revision") == source_revision)
                      if item.get("check_kind") == "build" else
                      reviewed_current(report, source_revision, contract.get("revision"))
                      if item.get("check_kind") == "source_review" else check_passed)
            status = "verified" if passed else "unverified"
        result.append({**item, "status": status, "source_revision": source_revision})
    return result
