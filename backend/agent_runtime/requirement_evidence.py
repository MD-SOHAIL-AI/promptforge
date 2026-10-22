"""Host-bound evidence types and bounded recovery for rejected references."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping

EVIDENCE_CONTRACT_VERSION = 3
SOURCE_SUFFIXES = frozenset({".cpp", ".cc", ".cxx", ".ino", ".c", ".h", ".hpp", ".ini", ".json"})
DOCUMENT_SUFFIXES = frozenset({".md", ".txt"})
EVIDENCE_KINDS = frozenset({"source", "documentation", "build", "unit_tests", "simulation"})


def _plain(line):
    return re.sub(r"^(?:#{1,6}\s+)?(?:[-*]\s+|\d+[.)]\s+)?", "", line.strip()).strip()


def _document_heading(line):
    text = _plain(line)
    return bool(re.search(r"\b(?:readme(?:\.md)?|documentation|user guide|setup guide|installation guide)\b", text, re.I)
                and (line.rstrip().endswith(":") or line.lstrip().startswith("#")))


def _section_contexts(request):
    """Retain list context that would otherwise disappear during partitioning."""
    result, section, section_depth, fenced = {}, "", None, None
    for position, raw in enumerate(request.splitlines()):
        text = _plain(raw)
        fence = re.match(r"^(`{3,}|~{3,})", text)
        if fence:
            marker = fence[1][0]
            fenced = None if fenced == marker else marker if fenced is None else fenced
        if not fenced and not fence:
            heading = re.match(r"^\s*(#{1,6})\s+", raw)
            if _document_heading(raw):
                section = text[:300]
                section_depth = len(heading[1]) if heading else None
            elif section_depth and heading and len(heading[1]) <= section_depth:
                section, section_depth = "", None
            elif (raw.lstrip().startswith("#") or text.endswith(":")) and re.match(
                    r"(?:firmware|source|code|hardware|requirements|main behavior|behavior|before finishing|acceptance|tests|simulation)\b", text, re.I):
                section, section_depth = "", None
        result.setdefault(text, []).append((position, section))
    return result


def _line_kinds(text, section):
    text = _plain(text)
    if re.match(r"(?:Required components\b|Pin connections\b.*\btable\b|"
                r"(?:Simple\s+)?(?:circuit[/ ]*)?wiring explanation\b|"
                r"Required libraries\b.*\b(?:install|installation)\b|"
                r"Expected Serial Monitor output\b|Common errors\b.*\btroubleshooting\b)", text, re.I):
        return {"documentation"}
    documentation = bool(section or re.search(r"\breadme(?:\.md)?\b|\bdocumentation\b", text, re.I)
        or re.match(r"(?:explain|explanation|describe|description|document|instructions?|guides?|tutorial|setup procedure|"
                    r"wiring table|component list|GPIO pin mapping|state-machine diagram|Serial Monitor example|"
                    r"testing procedure|test checklist|project architecture|step[- ]by[- ]step|"
                    r"(?:arduino IDE|platformio) (?:setup|installation)|"
                    r"(?:provide|include|add|write|create|update|show)\s+(?:a |an |the )?"
                    r"(?:guide|tutorial|instructions|explanation|description|wiring|component list|"
                    r"GPIO pin mapping|state-machine diagram|Serial Monitor example|testing procedure|test checklist))\b", text, re.I)
        or text.startswith("Hardware:")
        or re.search(r"^(?:clearly\s+)?state\b[^\n]{0,100}\b(?:cannot|limitations?|unverified|not verified)\b", text, re.I))
    # Pure documentation instructions can mention code, builds or tests without
    # authorizing those operations. Explicit implementation clauses stay source.
    doc_action = bool(section or re.match(r"(?:explain|explanation|describe|description|document|instructions?|step[- ]by[- ]step|"
        r"(?:arduino IDE|platformio) (?:setup|installation)|"
        r"(?:provide|include|add|write|create|update|show)\s+(?:a |an |the )?"
        r"(?:guide|tutorial|instructions|explanation|description))\b", text, re.I))
    if documentation and doc_action:
        return {"documentation"}
    execution = set()
    if re.match(r"(?:compile\b|(?:run|execute)\s+(?:the\s+|a\s+)?(?:local\s+)?(?:firmware\s+)?build\b|"
                r"build\s+(?:the\s+)?firmware\s*[.!]?$|(?:verify|ensure)\s+(?:that\s+)?(?:the\s+)?firmware\s+(?:builds|compiles))", text, re.I):
        execution.add("build")
    if re.match(r"(?:run|execute|pass)\s+(?:the\s+|requested\s+)?(?:unit tests?|automated tests?|test cases?)\b", text, re.I):
        execution.add("unit_tests")
    if re.match(r"(?:run|execute|perform)\s+(?:the\s+|a\s+)?simulation\b|simulate\b", text, re.I):
        execution.add("simulation")
    if execution:
        return execution | ({"documentation"} if documentation else set())
    source = not documentation or bool(re.search(r"\b(?:source files?|firmware|implement|implementation)\b", text, re.I))
    return ({"documentation"} if documentation else set()) | ({"source"} if source else set())


def bind_requirement_types(requirements, request, *, reclassify=False):
    """Called by task creation/migration, never controlled by tool arguments."""
    contexts = _section_contexts(request)
    bound, position = [], -1
    for requirement in requirements:
        item = dict(requirement)
        existing = item.get("evidence_kinds")
        preserved = (isinstance(existing, list) and bool(existing)
                     and all(isinstance(kind, str) and kind in EVIDENCE_KINDS for kind in existing))
        kinds, section, fenced = set(), str(item.get("documentation_section") or ""), None
        matched_context = False
        for line in item["text"].splitlines():
            text = _plain(line)
            occurrence = next((entry for entry in contexts.get(text, ()) if entry[0] > position), None)
            if occurrence:
                position, section = occurrence
                matched_context = True
            fence = re.match(r"^(`{3,}|~{3,})", text)
            if fence:
                marker = fence[1][0]
                fenced = None if fenced == marker else marker if fenced is None else fenced
                continue
            if fenced:
                continue
            # Sentences remain one authoritative requirement, with multiple
            # evidence obligations when implementation and documentation mix.
            for clause in re.split(r"(?<=[.!?;])\s+|\s+and\s+(?=(?:explain|describe|document|implement|compile|build|run|execute|simulate)\b)", text, flags=re.I):
                if clause:
                    kinds.update(_line_kinds(clause, section))
        if not preserved or reclassify and matched_context:
            item["evidence_kinds"] = sorted(kinds or {"source"})
            item.pop("documentation_section", None)
            if section:
                item["documentation_section"] = section
        bound.append(item)
    return bound


def evidence_requirements(contract):
    requirements = contract.get("requirements", ())
    if contract.get("evidence_contract_version") == EVIDENCE_CONTRACT_VERSION:
        for item in requirements:
            kinds = item.get("evidence_kinds")
            if not isinstance(kinds, list) or not kinds or not all(isinstance(k, str) and k in EVIDENCE_KINDS for k in kinds):
                raise ValueError("REQUIREMENT_EVIDENCE_CONTRACT_INVALID")
        return {item["id"]: item for item in requirements}
    # Compatibility for host-created legacy fixtures and saved task contracts.
    # Normal production resumes persist the upgraded contract before generation.
    return {item["id"]: item for item in bind_requirement_types(requirements, contract.get("request", ""))}


def document_sections(path, content, requirement):
    """Suggest readable spans; their meaning is still assessed independently."""
    tokens = set(re.findall(r"[a-z0-9]+", requirement.casefold())) - {
        "a", "an", "the", "of", "how", "to", "step", "by", "for", "with", "include", "provide"}
    if tokens & {"explain", "explanation", "description"}:
        tokens.update({"code", "works"})
    headings = [(n, len(match[1]), match[2]) for n, line in enumerate(content.splitlines(), 1)
                if (match := re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line))]
    candidates = []
    for index, (start, depth, title) in enumerate(headings):
        end = next((n - 1 for n, d, _ in headings[index + 1:] if d <= depth), len(content.splitlines()))
        score = len(tokens & set(re.findall(r"[a-z0-9]+", title.casefold())))
        if score:
            candidates.append((score, {"path": path, "heading": title[:160], "start_line": start,
                                       "end_line": min(end, start + 79)}))
    return [item for _, item in sorted(candidates, key=lambda pair: (-pair[0], pair[1]["start_line"]))[:3]]


def execution_reference(kind, records, revision):
    if kind in {"build", "simulation"}:
        result = records.get(kind) or {}
        if result.get("success") is True and result.get("workspace_revision") == revision:
            return {"kind": kind, "source_revision": revision, "success": True,
                    "ref": result.get("build_id") or result.get("call_id") or kind}
    else:
        for result in records.get("tests", ()):
            if result.get("kind") == kind and result.get("success") is True and result.get("source_revision") == revision:
                return {"kind": kind, "source_revision": revision, "success": True, "ref": result.get("call_id") or kind}
    return None


class RejectedEvidenceTracker:
    """Three identical rejected submissions get a bounded recovery stop."""
    def __init__(self, saved=None):
        self.entries = dict(saved or {})

    def observe(self, report, submitted_ids):
        repeated, stop = [], []
        for check in report.get("checks", ()):
            identity = check["id"]
            if identity not in submitted_ids:
                continue
            if not check.get("issues"):
                self.entries.pop(identity, None)
                continue
            fingerprint = hashlib.sha256(json.dumps({"task": report.get("task_revision"),
                "source": report.get("workspace_revision"), "check": check}, sort_keys=True).encode()).hexdigest()
            previous = self.entries.get(identity, {})
            count = previous.get("count", 0) + 1 if previous.get("fingerprint") == fingerprint else 1
            self.entries[identity] = {"fingerprint": fingerprint, "count": count}
            check["rejection_attempts"] = count
            if count >= 2:
                repeated.append(identity)
            if count >= 3:
                stop.append(identity)
        report["evidence_recovery"] = {"repeated_ids": repeated, "stop_ids": stop, "stop": bool(stop)}
