"""Host-owned task binding and source-evidence checks for firmware work.

Requirements are user text, not capabilities. Source evidence establishes a
review trail; hardware behavior still requires simulation or device evidence.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Mapping

from .tool_policy import product_agent_policy
from .deliverables import infer_deliverables


def _partition_request(request: str) -> tuple[list[str], list[str]]:
    lines = [line for line in request.splitlines() if line.strip()]
    exclusions = []
    excluding = False
    active_lines = []
    for raw_line in lines:
        line = raw_line.strip(" \t-*#")
        future = bool(re.search(r"\b(future\s+(?:extensions?|features|expansion)|optional future|out of scope)\b", line, re.I))
        if excluding and re.match(r"\s*(?:#{1,6}\s+|\d+[.)]\s+)", raw_line) and not future:
            excluding = False
        if future:
            excluding = True
        if re.search(r"\b(do not|don't|never|must not|prohibited)\b", line, re.I) or re.search(
            r"\bskip\s+(?:the\s+)?(?:build|compilation|simulation|unit tests?|test cases?)\b", line, re.I
        ) or excluding:
            exclusions.append(line)
        else:
            active_lines.append(line)
    return active_lines, exclusions


def _correction_targets(request: str) -> set[str]:
    """Recognize explicit replacements without guessing at implicit conflicts."""
    pattern = (r"\b(?:instead of|rather than|replace|stop using|no longer use|"
               r"remove|drop|do not use|don't use)\s+(?:the\s+)?"
               r"([A-Za-z][A-Za-z0-9_-]{1,63})\b")
    return {match.group(1).casefold() for match in re.finditer(pattern, request, re.I)}


def _mentions_target(text: str, target: str) -> bool:
    return re.search(r"(?<![A-Za-z0-9_-])" + re.escape(target) +
                     r"(?![A-Za-z0-9_-])", text, re.I) is not None


def _newly_allowed_targets(active_lines: list[str]) -> set[str]:
    pattern = (r"\b(?:implement|include|enable|allow|use)\s+(?:the\s+)?"
               r"([A-Za-z][A-Za-z0-9_-]{1,63})\b")
    return {match.group(1).casefold() for line in active_lines
            for match in re.finditer(pattern, line, re.I)}


def _same_assignment_slot(old: str, new: str) -> bool:
    """Match only narrow, single-line hardware/timing assignments."""
    if "\n" in old or "\n" in new:
        return False
    component = re.compile(r"\b(led|button|relay|buzzer|sensor|display|motor|sda|scl)\b", re.I)
    old_components = {item.casefold() for item in component.findall(old)}
    new_components = {item.casefold() for item in component.findall(new)}
    old_pins = set(re.findall(r"\bGPIO(?:_NUM_)?(\d+)\b", old, re.I))
    new_pins = set(re.findall(r"\bGPIO(?:_NUM_)?(\d+)\b", new, re.I))
    if old_components & new_components and len(old_pins) == len(new_pins) == 1:
        return old_pins != new_pins
    timing = re.compile(r"\b(interval|debounce|timeout|period)\b", re.I)
    old_timings = {item.casefold() for item in timing.findall(old)}
    new_timings = {item.casefold() for item in timing.findall(new)}
    old_values = set(re.findall(r"\b\d+\s*(?:ms|milliseconds?|seconds?)\b", old, re.I))
    new_values = set(re.findall(r"\b\d+\s*(?:ms|milliseconds?|seconds?)\b", new, re.I))
    return bool(old_timings & new_timings and len(old_values) == len(new_values) == 1
                and {value.casefold() for value in old_values} != {value.casefold() for value in new_values})


def _retired_check_kinds(request: str) -> set[str]:
    retired = set()
    for kind, action in (("build", r"build|compile|compilation"),
                         ("simulation", r"simulate|simulation|wokwi"),
                         ("unit_tests", r"unit tests?|test cases?|automated tests?")):
        if re.search(r"\b(?:do not|don't|no longer|skip|without)\s+(?:run(?:ning)?\s+)?(?:the\s+)?(?:" + action + r")\b", request, re.I):
            retired.add(kind)
    return retired


def create_task_contract(request: str, *, project_id: str, session_id: str | None,
                         workspace: Path, intent: str, prohibited=()) -> dict:
    if not request.strip() or len(request) > 64000:
        raise ValueError("TASK_REQUEST_INVALID")
    active_lines, exclusions = _partition_request(request)
    requirements = []
    # Group a long specification without dropping clauses. Small prompts keep
    # one requirement per line; long prompts get at most 64 evidence groups.
    width = max(1, (len(active_lines) + 63) // 64)
    for start in range(0, len(active_lines), width):
        text = "\n".join(active_lines[start:start + width])
        requirements.append({"id": f"req-{len(requirements) + 1}", "stable_key":hashlib.sha256(text.encode()).hexdigest(),"text":text})
    value = {"schema_version": 1, "project_id": project_id, "session_id": session_id,
             "workspace_root": str(workspace.resolve(strict=True)), "intent": intent,
             "request": request, "requirements": requirements, "exclusions": exclusions,
             "superseded_requirements": [], "superseded_exclusions": [],
             "prohibited_actions": list(prohibited), "defaults": ["Preserve unrelated user files.",
             "Do not flash without separate confirmation."]}
    value["accepted_choices"] = [line.strip() for line in request.split("Confirmed choices:",1)[-1].splitlines() if line.strip()] if "Confirmed choices:" in request else []
    value["acceptance_checks"] = [{"id":"source", "kind":"source_review", "criterion":"Every requested requirement passes independent review against current source."}]
    requested_work = "\n".join(active_lines)
    if re.search(r"\b(build|compile|compiles|compilation)\b", requested_work, re.I):
        value["acceptance_checks"].append({"id":"firmware-build", "kind":"build",
            "criterion":"A firmware build passes for the current source revision."})
    if re.search(r"\b(simulate|simulation|wokwi)\b", requested_work, re.I):
        value["acceptance_checks"].append({"id":"simulation", "kind":"simulation",
            "criterion":"A simulation passes for the current source revision."})
    if re.search(r"\b(unit tests?|test cases?|automated tests?)\b", "\n".join(active_lines), re.I):
        value["acceptance_checks"].append({"id":"unit-tests", "kind":"unit_tests", "criterion":"Requested host logic tests pass for current source."})
    retired_kinds = _retired_check_kinds(request)
    value["acceptance_checks"] = [check for check in value["acceptance_checks"]
                                  if check["kind"] not in retired_kinds]
    value["deliverables"] = infer_deliverables(value)
    value["revision"] = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    value["revision_history"] = [value["revision"]]
    return value


def revise_task_contract(previous, request, *, project_id, session_id, workspace, intent, prohibited=()):
    """Keep earlier obligations and IDs; the latest user revision takes precedence."""
    current=create_task_contract(request,project_id=project_id,session_id=session_id,
        workspace=workspace,intent=intent,prohibited=prohibited)
    if not previous or previous.get("project_id")!=project_id or previous.get("workspace_root")!=str(workspace.resolve(strict=True)) or previous.get("session_id")!=session_id:
        return current
    targets = _correction_targets(request)
    inherited = []
    superseded = [dict(item) for item in previous.get("superseded_requirements", [])]
    for item in previous.get("requirements", []):
        if any(_mentions_target(str(item.get("text", "")), target) for target in targets) or any(
            _same_assignment_slot(str(item.get("text", "")), str(new_item.get("text", "")))
            for new_item in current["requirements"]):
            superseded.append({**item, "status": "superseded",
                               "superseded_by_request": request[:500]})
        else:
            inherited.append(dict(item))
    if len(inherited)+len(current["requirements"])>64:
        raise ValueError("TASK_REQUIREMENT_LIMIT_REACHED")
    known={item.get("stable_key") for item in inherited}
    next_id = 0
    for item in (*inherited, *superseded):
        match = re.fullmatch(r"req-(\d+)", str(item.get("id", "")))
        if match:
            next_id = max(next_id, int(match.group(1)))
    for item in current["requirements"]:
        if item["stable_key"] not in known:
            next_id += 1
            inherited.append({**item,"id":"req-"+str(next_id)})
            known.add(item["stable_key"])
    current["requirements"]=inherited
    current["superseded_requirements"] = superseded[-64:]
    allowed_targets = _newly_allowed_targets(_partition_request(request)[0])
    retained_exclusions = []
    superseded_exclusions = list(previous.get("superseded_exclusions", []))
    for exclusion in previous.get("exclusions", []):
        if any(_mentions_target(str(exclusion), target) for target in allowed_targets):
            superseded_exclusions.append(str(exclusion))
        else:
            retained_exclusions.append(str(exclusion))
    current["exclusions"]=list(dict.fromkeys([*retained_exclusions,*current["exclusions"]]))
    current["superseded_exclusions"] = superseded_exclusions[-64:]
    current["parent_revision"]=previous.get("revision")
    current["revision_rule"]="The latest request overrides conflicting earlier requirements; preserve other requirements and exclusions."
    choices={choice.split(":",1)[0]:choice for choice in [*previous.get("accepted_choices",[]),*current["accepted_choices"]]}
    current["accepted_choices"]=list(choices.values())
    checks={check["id"]:check for check in previous.get("acceptance_checks",[])}
    retired_kinds = _retired_check_kinds(request)
    checks = {key: check for key, check in checks.items() if check.get("kind") not in retired_kinds}
    checks.update({check["id"]:check for check in current["acceptance_checks"]
                   if check["kind"] not in retired_kinds})
    current["acceptance_checks"]=list(checks.values())
    current["deliverables"] = infer_deliverables(current)
    current.pop("revision",None)
    current["revision_history"] = list(previous.get("revision_history") or [previous["revision"]])[-63:]
    current["revision"]=hashlib.sha256(json.dumps(current,sort_keys=True).encode()).hexdigest()
    current["revision_history"].append(current["revision"])
    return current


def clarification_choices(request: str) -> list[dict]:
    """Ask only for explicit unresolved alternatives, not incidental keywords."""
    choices = []
    patterns = (
        ("cloud", r"\b(MQTT|ThingSpeak)\s*\(?\s*(?:or|/)\s*(?:an?\s+)?(MQTT|ThingSpeak)\b"),
        ("display", r"\b(OLED|TFT)\s*(?:or|/)\s*(OLED|TFT)\b"),
        ("temperature_sensor", r"\b(DHT11|DHT22)\s*(?:or|/)\s*(DHT11|DHT22)\b"),
        ("air_sensor", r"\b(CCS811|SGP30)\s*(?:or|/)\s*(CCS811|SGP30)\b"),
    )
    active_lines = [line for line in _partition_request(request)[0] if not re.search(
        r"\b(future|do not|don't|optional extension|not required)\b", line, re.I)]
    for key, pattern in patterns:
        match = re.search(pattern, "\n".join(active_lines), re.I)
        if match and match[1].casefold() != match[2].casefold():
            choices.append({"id": key, "options": [match[1], match[2]]})
    # General explicit alternatives: preserve the original request and ask once
    # for materially different named components, protocols, or environments.
    known = {option.casefold() for choice in choices for option in choice["options"]}
    for match in re.finditer(r"\b([A-Z][A-Za-z0-9_-]{2,24})\s+(?:or|versus)\s+([A-Z][A-Za-z0-9_-]{2,24})\b", "\n".join(active_lines)):
        options = [match[1],match[2]]
        if len(choices)<8 and not any(option.casefold() in known for option in options) and options[0]!=options[1]:
            choices.append({"id":"component_"+str(len(choices)+1),"options":options})
            known.update(option.casefold() for option in options)
    return choices


def merge_clarification_answer(pending: Mapping, answer: str) -> str:
    """A new explicit choice replaces an earlier choice for the same item."""
    previous = str(pending.get("answers") or "")
    selected = []
    for choice in pending["choices"]:
        def matches(text):
            return [option for option in choice["options"] if re.search(
                r"(?<!\w)" + re.escape(option) + r"(?!\w)", text, re.I)]
        current = matches(answer)
        values = current if current else matches(previous)
        if len(values) == 1:
            selected.append(values[0])
    return ", ".join(selected)


def resolve_clarification(pending: Mapping, answer: str) -> tuple[str, list[dict]]:
    selected = []
    resolved_request = str(pending["request"])
    unresolved = []
    for choice in pending["choices"]:
        matches = [option for option in choice["options"] if re.search(
            r"(?<!\w)" + re.escape(option) + r"(?!\w)", answer, re.I)]
        if len(matches) != 1:
            unresolved.append(choice)
        else:
            selected.append(f"{choice['id']}: {matches[0]}")
            if len(choice["options"]) != 2:
                options=sorted(choice["options"],key=lambda option:resolved_request.find(option))
                separator=r"\s*(?:,?\s*(?:or|versus|/)|,)\s*"
                resolved_request=re.sub(separator.join(re.escape(option) for option in options),matches[0],resolved_request,flags=re.I)
                continue
            first, second = choice["options"]
            separator = r"\s*\(?\s*(?:or|/)\s*(?:an?\s+)?"
            pattern = r"\b(?:" + re.escape(first) + separator + re.escape(second) + r"(?:\s+broker)?\)?|" + re.escape(second) + separator + re.escape(first) + r"(?:\s+broker)?\)?)"
            resolved_request = re.sub(pattern, matches[0], resolved_request, flags=re.I)
    if unresolved:
        return "", unresolved
    return resolved_request + "\n\nConfirmed choices:\n" + "\n".join(selected), []


def verify_requirement_evidence(contract: Mapping, checks: list, root: Path,
                                revision: str, *, read_file=None) -> dict:
    expected = {item["id"] for item in contract.get("requirements", ())}
    requirements = {item["id"]: item["text"] for item in contract.get("requirements", ())}
    received = set()
    validated = []
    policy = product_agent_policy()
    for check in checks:
        identity = check.get("id")
        if identity not in expected or identity in received:
            raise ValueError("REQUIREMENT_ID_INVALID")
        received.add(identity)
        evidence = check.get("evidence", [])
        valid = check.get("status") == "implemented" and bool(evidence)
        for item in evidence:
            path = policy.resolve_path(root, item["path"], must_exist=read_file is None)
            if read_file is None:
                if path.stat().st_size > 128000:
                    raise ValueError("REQUIREMENT_EVIDENCE_TOO_LARGE")
                content = path.read_text(encoding="utf-8")
            else:
                content = read_file(item["path"])
            # A README assertion is not evidence of executable behavior.
            documentation = bool(re.search(r"\b(readme|document(?:ation)?|explain|show|wiring table|testing procedure|instructions)\b", requirements[identity], re.I))
            if path.suffix.casefold() not in {".cpp", ".cc", ".ino", ".c", ".h", ".hpp", ".ini", ".json"} and not (documentation and path.suffix.casefold() in {".md", ".txt"}):
                valid = False
            quote = item["quote"]
            if not quote.strip() or quote not in content:
                valid = False
            elif path.suffix.casefold() not in {".md", ".txt"} and not _has_noncomment_quote(content, quote, path.suffix.casefold()):
                valid = False
        validated.append({"id": identity, "status": "referenced" if valid else "unverified",
                          "evidence": evidence})
    return {"schema_version": 2, "task_revision": contract.get("revision"),
            "workspace_revision": revision, "checks": validated,
            "missing": sorted(expected - received),
            "references_valid": bool(expected) and received == expected and all(
                item["status"] == "referenced" for item in validated),
            "valid": False,
            "verification_level": "source_reference", "hardware_verified": False}


def _has_noncomment_quote(content: str, quote: str, suffix: str) -> bool:
    if quote.lstrip().startswith(("//", "/*", "*")):
        return False
    for match in re.finditer(re.escape(quote), content):
        before = content[:match.start()]
        line = before[before.rfind("\n") + 1:].lstrip()
        if before.rfind("/*") > before.rfind("*/"):
            continue
        if line.startswith(("//", "/*", "*")):
            continue
        if suffix == ".ini" and line.startswith((";", "#")):
            continue
        return True
    return False


def acceptance_report(contract, requirements, build, evidence, revision, simulation=None):
    checks=[]
    for check in contract.get("acceptance_checks", []):
        kind=check["kind"]
        if kind == "source_review":
            from .verification import reviewed_current
            passed=reviewed_current(requirements, revision, contract.get("revision"))
        elif kind == "build":
            passed=build.get("success") is True and build.get("workspace_revision")==revision
        elif kind == "simulation":
            passed=bool(simulation and simulation.get("success") is True and simulation.get("workspace_revision")==revision)
        else:
            passed=any(item.get("kind")==kind and item.get("success") is True and item.get("source_revision")==revision for item in evidence)
        checks.append({"id":check["id"],"kind":kind,"criterion":check["criterion"],"passed":bool(passed),
                       "status": ("reviewed" if kind == "source_review" else "tested") if passed else "unverified"})
    return {"schema_version":2,"task_revision":contract.get("revision"),"source_revision":revision,
            "checks":checks,"passed":bool(checks) and all(item["passed"] for item in checks),
            "hardware_verified":False,"evidence":list(evidence)[-20:]}
