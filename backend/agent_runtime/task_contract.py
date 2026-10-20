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
from .requirement_evidence import (EVIDENCE_CONTRACT_VERSION, SOURCE_SUFFIXES, DOCUMENT_SUFFIXES,
    bind_requirement_types, evidence_requirements, document_sections, execution_reference)


def _split_diagnostic_context(request: str) -> tuple[str, str]:
    """Recognize copied ForgeX activity panels outside literal fenced examples.

    Keep the original request separately. A corroborated panel header starts
    diagnostic context; an explicit user-guidance heading resumes obligations.
    Ordinary mentions of ForgeX or compiler messages do not activate this rule.
    """
    segmented = _request_context_lines(request)
    return ("".join(line for line, diagnostic in segmented if not diagnostic),
            "".join(line for line, diagnostic in segmented if diagnostic))


def _request_context_lines(request: str) -> list[tuple[str, bool]]:
    lines = request.splitlines(keepends=True)
    segmented = []
    fenced = None
    diagnostic = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        fence = re.match(r"^(`{3,}|~{3,})", stripped)
        if fence:
            marker = fence.group(1)[0]
            if fenced is None:
                fenced = marker
            elif fenced == marker:
                fenced = None
        if fenced is None and not fence:
            following = [item.strip() for item in lines[index + 1:index + 30] if item.strip()]
            if (stripped == "ForgeX" and following and following[0] == "Agent activity"
                    and "thinking.summary" in following
                    and any(re.fullmatch(r"Turn \d+", item) for item in following)):
                diagnostic = True
            elif diagnostic and re.match(
                    r"^(?:#{1,6}\s*)?(?:User guidance|Latest user request|Confirmed choices):", stripped, re.I):
                diagnostic = False
        segmented.append((line, diagnostic))
    return segmented


def _partition_request(request: str) -> tuple[list[str], list[str]]:
    specification, _ = _split_diagnostic_context(request)
    return _partition_specification(specification)


def _partition_specification(request: str) -> tuple[list[str], list[str]]:
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
        if excluding:
            exclusions.append(line)
            continue
        prohibition = r"\b(?:do not|don't|never|must not|prohibited|skip\s+(?:the\s+)?(?:build|compilation|simulation|unit tests?|test cases?))\b"
        # Keep ordinary requirement lines intact, including filenames, versions
        # and pin assignments. Only mixed instructions need clause partitioning.
        clauses = re.split(
            r"(?<=[.!?;])\s+|(?:,\s*|\s+(?:and|but)\s+)(?=" + prohibition + r")",
            line, flags=re.I,
        ) if re.search(prohibition, line, re.I) else [line]
        for clause in clauses:
            if not clause.strip():
                continue
            (exclusions if re.search(prohibition, clause, re.I) else active_lines).append(clause.strip())
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


def _logical_requirement_groups(lines: list[str]) -> list[str]:
    """Keep headings, examples and lists with their instruction, without dropping text."""
    groups, current, fenced = [], [], None
    boundary = re.compile(
        r"^(?:\d+[.)]\s|Hardware:|Main behavior:|Example patterns:|Pattern [12]:|Unknown pattern:|"
        r"Suggested states:|Requirements:|Learning mode:|Serial Monitor:|Code architecture:|"
        r"Important ESP32 considerations:|Provide:|Before finishing:|"
        r"(?:Use|Add|Record|Save|Implement|Only print|Keep|Configure|Explain|Protect|Build|Fix|Verify|Review|Check|Clearly state)\b|"
        r"The (?:ESP32|system|learned pattern)\b)", re.I)
    for line in lines:
        fence = re.match(r"^(`{3,}|~{3,})", line)
        inside = fenced is not None
        if fence:
            marker = fence[1][0]
            fenced = None if fenced == marker else marker if fenced is None else fenced
        if current and not inside and not fence:
            # Ordinary instructions remain separate even in short requests.
            # A headed example/list keeps its non-instruction lines together.
            headed = current[0].endswith(":")
            if (not headed or boundary.match(line)) and (len(current) > 1 or not headed):
                groups.append("\n".join(current))
                current = []
        current.append(line)
    if current:
        groups.append("\n".join(current))
    # Merge whole groups, never slice an example or state diagram by line count.
    while len(groups) > 64:
        index = min(range(len(groups) - 1), key=lambda i: len(groups[i]) + len(groups[i + 1]))
        groups[index:index + 2] = [groups[index] + "\n" + groups[index + 1]]
    return groups


def _requirement_content(lines):
    """Section labels and project titles describe the request, not source proof."""
    context, groups, section, fenced = [], [], [], None
    label = re.compile(
        r"^(?:Project\s*:.+|Requirements\s*:?|Provide\s*:|"
        r"(?:Firmware|Code|Documentation|Document|README(?:\.md)?)\s*(?:requirements)?\s*:?)$", re.I)
    for line in lines:
        fence = re.match(r"^(`{3,}|~{3,})", line)
        inside = fenced is not None
        if fence:
            marker = fence[1][0]
            fenced = None if fenced == marker else marker if fenced is None else fenced
        if not inside and not fence and label.fullmatch(line.strip()):
            groups.extend(_logical_requirement_groups(section))
            section = []
            context.append(line)
        else:
            section.append(line)
    groups.extend(_logical_requirement_groups(section))
    return groups, context


def create_task_contract(request: str, *, project_id: str, session_id: str | None,
                         workspace: Path, intent: str, prohibited=()) -> dict:
    if not request.strip() or len(request) > 64000:
        raise ValueError("TASK_REQUEST_INVALID")
    specification, diagnostics = _split_diagnostic_context(request)
    active_lines, exclusions = _partition_specification(specification)
    requirements = []
    groups, context = _requirement_content(active_lines)
    for text in groups:
        requirements.append({"id": f"req-{len(requirements) + 1}", "stable_key":hashlib.sha256(text.encode()).hexdigest(),"text":text})
    value = {"schema_version": 1, "partition_version": 5, "request_context": context, "project_id": project_id, "session_id": session_id,
             "workspace_root": str(workspace.resolve(strict=True)), "intent": intent,
             "request": request, "requirements": requirements, "exclusions": exclusions,
             "superseded_requirements": [], "superseded_exclusions": [],
             "prohibited_actions": list(prohibited), "defaults": ["Preserve unrelated user files.",
             "Do not flash without separate confirmation."]}
    if diagnostics:
        value["diagnostic_context"] = diagnostics
    value["requirements"] = bind_requirement_types(requirements, specification)
    value["evidence_contract_version"] = EVIDENCE_CONTRACT_VERSION
    value["accepted_choices"] = [line.strip() for line in specification.split("Confirmed choices:",1)[-1].splitlines() if line.strip()] if "Confirmed choices:" in specification else []
    value["acceptance_checks"] = [{"id":"source", "kind":"source_review", "criterion":"Every requested requirement passes independent review against current source."}]
    execution_kinds = _requested_execution_kinds(value["requirements"])
    if "build" in execution_kinds:
        value["acceptance_checks"].append({"id":"firmware-build", "kind":"build",
            "criterion":"A firmware build passes for the current source revision."})
    if "simulation" in execution_kinds:
        value["acceptance_checks"].append({"id":"simulation", "kind":"simulation",
            "criterion":"A simulation passes for the current source revision."})
    if "unit_tests" in execution_kinds:
        value["acceptance_checks"].append({"id":"unit-tests", "kind":"unit_tests", "criterion":"Requested host logic tests pass for current source."})
    retired_kinds = _retired_check_kinds(specification)
    value["acceptance_checks"] = [check for check in value["acceptance_checks"]
                                  if check["kind"] not in retired_kinds]
    value["deliverables"] = infer_deliverables(value)
    value["revision"] = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    value["revision_history"] = [value["revision"]]
    return value


def _requested_execution_kinds(requirements):
    requested_work = "\n".join(item["text"] for item in requirements
                               if item["evidence_kinds"] != ["documentation"])
    return {kind for kind, pattern in (
        ("build", r"\b(build|compile|compiles|compilation)\b"),
        ("simulation", r"\b(simulate|simulation|wokwi)\b"),
        ("unit_tests", r"\b(unit tests?|test cases?|automated tests?)\b"),
    ) if re.search(pattern, requested_work, re.I)}


def bind_task_session(previous, session_id):
    """Bind a host-created anonymous task to its generated durable session."""
    if previous.get("session_id") == session_id:
        return dict(previous)
    if previous.get("session_id") is not None:
        raise ValueError("TASK_WORKSPACE_BINDING_MISMATCH")
    value = {**previous, "session_id": session_id, "parent_revision": previous.get("revision"),
             "revision_history": list(previous.get("revision_history", []))[-63:]}
    value.pop("revision", None)
    value["revision"] = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    value["revision_history"].append(value["revision"])
    return value


def repair_task_contract(previous, *, workspace):
    """Upgrade the next continuation; historical contracts and source stay intact."""
    value = _repair_partition_contract(previous, workspace=workspace)
    if not value:
        return value
    requirements = bind_requirement_types(value.get("requirements", ()),
        _split_diagnostic_context(str(value.get("request", "")))[0],
        reclassify=value.get("evidence_contract_version", 0) < EVIDENCE_CONTRACT_VERSION)
    if (value.get("evidence_contract_version") == EVIDENCE_CONTRACT_VERSION
            and requirements == value.get("requirements")):
        return value
    upgraded = {**value, "requirements": requirements, "evidence_contract_version": EVIDENCE_CONTRACT_VERSION,
                "parent_revision": previous.get("revision")}
    execution_kinds = _requested_execution_kinds(requirements)
    generated_checks = {"firmware-build": "build", "simulation": "simulation", "unit-tests": "unit_tests"}
    upgraded["acceptance_checks"] = [check for check in value.get("acceptance_checks", ())
        if check.get("id") not in generated_checks or generated_checks[check["id"]] in execution_kinds]
    upgraded["deliverables"] = infer_deliverables(upgraded)
    upgraded.pop("revision", None)
    upgraded["revision_history"] = list(value.get("revision_history") or [value.get("revision")])[-63:]
    upgraded["revision"] = hashlib.sha256(json.dumps(upgraded, sort_keys=True).encode()).hexdigest()
    upgraded["revision_history"].append(upgraded["revision"])
    return upgraded


def _repair_partition_contract(previous, *, workspace):
    value = _repair_diagnostic_contract(previous, workspace=workspace)
    if not value or value.get("partition_version", 1) >= 5:
        return value
    existing = value.get("requirements", [])
    lines = [line for item in existing for line in item["text"].splitlines()]
    groups, context = _requirement_content(lines)
    known = {}
    for item in existing:
        known.setdefault(item["text"], []).append(item)
    retired = list(value.get("superseded_requirements", []))
    next_id = max((int(m[1]) for item in [*existing, *retired]
                   if (m := re.fullmatch(r"req-(\d+)", item["id"]))), default=0)
    active = []
    for text in groups:
        if known.get(text):
            active.append(dict(known[text].pop(0)))
        else:
            next_id += 1
            active.append({"id": f"req-{next_id}", "text": text,
                           "stable_key": hashlib.sha256(text.encode()).hexdigest()})
    kept = {item["id"] for item in active}
    retired.extend({**item, "status": "superseded", "reason": "logical_requirement_grouping"}
                   for item in existing if item["id"] not in kept)
    rebuilt = {**value, "partition_version": 5, "request_context": list(dict.fromkeys([*value.get("request_context", []), *context])),
               "requirements": active, "superseded_requirements": retired,
               "parent_revision": value.get("revision"),
               "revision_history": list(value.get("revision_history", []))[-63:]}
    rebuilt.pop("revision", None)
    rebuilt["deliverables"] = infer_deliverables(rebuilt)
    rebuilt["revision"] = hashlib.sha256(json.dumps(rebuilt, sort_keys=True).encode()).hexdigest()
    rebuilt["revision_history"].append(rebuilt["revision"])
    return rebuilt


def _repair_diagnostic_contract(previous, *, workspace):
    """Upgrade the resumed contract without changing its historical journal."""
    previous = _repair_legacy_exclusions(previous, workspace=workspace)
    if not previous or previous.get("partition_version", 1) >= 3:
        return previous
    specification, diagnostics = _split_diagnostic_context(str(previous.get("request", "")))
    if not diagnostics:
        return previous

    # Legacy requirements follow request order, with inherited work before the
    # latest request. Locate the panel across group boundaries, preserving IDs
    # for untouched groups and assigning fresh IDs to surviving mixed groups.
    existing = [dict(item) for item in previous.get("requirements", [])]
    flattened = [line.strip() for item in existing for line in item["text"].splitlines() if line.strip()]
    diagnostic_flags = [diagnostic for _, diagnostic in _request_context_lines("\n".join(flattened))]
    if not any(diagnostic_flags):
        return previous
    retired = [dict(item) for item in previous.get("superseded_requirements", [])]
    next_id = max((int(match.group(1)) for item in [*existing, *retired]
                   if (match := re.fullmatch(r"req-(\d+)", str(item.get("id", ""))))), default=0)
    active, position = [], 0
    for item in existing:
        kept = []
        for line in item["text"].splitlines():
            if not line.strip():
                continue
            if not diagnostic_flags[position]:
                kept.append(line)
            position += 1
        text = "\n".join(kept)
        if text == item["text"]:
            active.append(item)
        else:
            retired.append({**item, "status": "superseded", "reason": "pasted_diagnostic_context"})
            if text:
                next_id += 1
                active.append({"id": f"req-{next_id}", "text": text,
                               "stable_key": hashlib.sha256(text.encode()).hexdigest()})
    _, clean_exclusions = _partition_specification(specification)
    _, diagnostic_exclusions = _partition_specification(diagnostics)
    exclusions = list(dict.fromkeys([*(item for item in previous.get("exclusions", [])
                                      if item not in diagnostic_exclusions), *clean_exclusions]))
    rebuilt = create_task_contract("\n".join([*(item["text"] for item in active), *exclusions]),
        project_id=previous["project_id"], session_id=previous.get("session_id"),
        workspace=workspace, intent=previous["intent"], prohibited=previous.get("prohibited_actions", ()))
    value = dict(previous)
    value.update(partition_version=3, requirements=active, exclusions=exclusions,
                 superseded_requirements=retired, diagnostic_context=diagnostics,
                 acceptance_checks=rebuilt["acceptance_checks"])
    value["deliverables"] = infer_deliverables(value)
    value["parent_revision"] = previous.get("revision")
    value["revision_history"] = list(previous.get("revision_history") or [previous.get("revision")])[-63:]
    value.pop("revision", None)
    value["revision"] = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    value["revision_history"].append(value["revision"])
    return value


def _repair_legacy_exclusions(previous, *, workspace):
    """Restore obligations excluded by the old line parser on explicit resume.

    Preserve existing IDs and the old revision as history. This changes the
    resumed contract, never the historical journal or a saved file boundary.
    """
    if not previous or previous.get("partition_version", 1) >= 2:
        return previous
    recovered, exclusions = _partition_request("\n".join(str(item) for item in previous.get("exclusions", [])))
    if not recovered:
        return previous
    rebuilt = create_task_contract("\n".join([*recovered, *exclusions]),
        project_id=previous["project_id"], session_id=previous.get("session_id"),
        workspace=workspace, intent=previous["intent"],
        prohibited=previous.get("prohibited_actions", ()))
    value = dict(previous)
    existing = [dict(item) for item in previous.get("requirements", [])]
    retired = previous.get("superseded_requirements", [])
    known = {item.get("stable_key") for item in [*existing, *retired]}
    ids = [int(match.group(1)) for item in [*existing, *retired]
           if (match := re.fullmatch(r"req-(\d+)", str(item.get("id", ""))))]
    next_id = max(ids, default=0)
    for item in rebuilt["requirements"]:
        if any(_same_assignment_slot(item["text"], other["text"]) for other in existing):
            continue
        if any(_mentions_target(item["text"], target) for target in _correction_targets("\n".join(exclusions))):
            continue
        if item["stable_key"] not in known:
            next_id += 1
            existing.append({**item, "id": f"req-{next_id}"})
            known.add(item["stable_key"])
    if len(existing) > 64:
        raise ValueError("TASK_REQUIREMENT_LIMIT_REACHED")
    value.update(partition_version=2, requirements=existing, exclusions=list(dict.fromkeys(exclusions)))
    checks = {item["id"]: item for item in rebuilt["acceptance_checks"]}
    checks.update({item["id"]: item for item in previous.get("acceptance_checks", [])})
    retired_kinds = _retired_check_kinds("\n".join(exclusions))
    value["acceptance_checks"] = [item for item in checks.values() if item["kind"] not in retired_kinds]
    value["deliverables"] = infer_deliverables(value)
    value["parent_revision"] = previous.get("revision")
    value["revision_history"] = list(previous.get("revision_history") or [previous.get("revision")])[-63:]
    value.pop("revision", None)
    value["revision"] = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    value["revision_history"].append(value["revision"])
    return value


def revise_task_contract(previous, request, *, project_id, session_id, workspace, intent, prohibited=()):
    """Keep earlier obligations and IDs; the latest user revision takes precedence."""
    previous = repair_task_contract(previous, workspace=workspace)
    current=create_task_contract(request,project_id=project_id,session_id=session_id,
        workspace=workspace,intent=intent,prohibited=prohibited)
    if not previous or previous.get("project_id")!=project_id or previous.get("workspace_root")!=str(workspace.resolve(strict=True)) or previous.get("session_id")!=session_id:
        return current
    specification, _ = _split_diagnostic_context(request)
    targets = _correction_targets(specification)
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
    current["request_context"] = list(dict.fromkeys([*previous.get("request_context", []), *current.get("request_context", [])]))
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
    retired_kinds = _retired_check_kinds(specification)
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
                                revision: str, *, read_file=None, execution_evidence=None) -> dict:
    expected = {item["id"] for item in contract.get("requirements", ())}
    requirements = evidence_requirements(contract)
    received = set()
    validated = []
    policy = product_agent_policy()
    for check in checks:
        identity = check.get("id")
        if identity not in expected or identity in received:
            raise ValueError("REQUIREMENT_ID_INVALID")
        received.add(identity)
        evidence = check.get("evidence", [])
        issues = []
        requirement = requirements[identity]
        kinds = requirement["evidence_kinds"]
        file_kinds = set(kinds) & {"source", "documentation"}
        covered = set()
        if check.get("status") != "implemented":
            issues.append("status_not_implemented")
        if not evidence and file_kinds:
            issues.append("evidence_missing")
        extracted = []
        suggestions = []
        for original in evidence:
            item = dict(original)
            path = policy.resolve_path(root, item["path"], must_exist=read_file is None)
            if read_file is None:
                if path.stat().st_size > 128000:
                    raise ValueError("REQUIREMENT_EVIDENCE_TOO_LARGE")
                content = path.read_text(encoding="utf-8")
            else:
                content = read_file(item["path"])
            suffix = path.suffix.casefold()
            evidence_kind = "documentation" if suffix in DOCUMENT_SUFFIXES else "source" if suffix in SOURCE_SUFFIXES else None
            if file_kinds and evidence_kind not in file_kinds:
                issues.append("documentation_file_required" if file_kinds == {"documentation"} else "source_file_required")
            elif evidence_kind:
                covered.add(evidence_kind)
            if suffix in DOCUMENT_SUFFIXES and "documentation" in file_kinds:
                suggestions.extend(document_sections(item["path"], content, requirement["text"]))
            if "start_line" in item:
                lines = content.splitlines(keepends=True)
                start, end = item["start_line"], item["end_line"]
                if (type(start) is not int or type(end) is not int or
                        not 1 <= start <= end <= len(lines) or end - start >= 80):
                    issues.append("line_range_invalid")
                    extracted.append(item)
                    continue
                item["quote"] = "".join(lines[start - 1:end]).rstrip("\r\n")
                item["content_sha256"] = hashlib.sha256(content.encode("utf-8")).hexdigest()
                if len(item["quote"]) > 4096:
                    issues.append("evidence_span_too_large")
                    extracted.append(item)
                    continue
            quote = item["quote"]
            if not quote.strip() or quote not in content:
                issues.append("quote_not_found")
            elif suffix not in DOCUMENT_SUFFIXES and not _has_noncomment_quote(content, quote, suffix):
                issues.append("comment_only")
            extracted.append(item)
        for missing_kind in file_kinds - covered:
            issue = "documentation_file_required" if missing_kind == "documentation" else "source_file_required"
            if evidence and issue not in issues:
                issues.append(issue)
        recorded = []
        for kind in set(kinds) - file_kinds:
            reference = execution_reference(kind, execution_evidence or {}, revision)
            if reference is None:
                issues.append(kind + "_result_required")
            else:
                recorded.append(reference)
        validated.append({"id": identity, "status": "referenced" if not issues else "unverified",
                          "evidence": extracted, "issues": list(dict.fromkeys(issues)),
                          "requirement": requirement["text"], "expected_evidence": kinds,
                          "execution_evidence": recorded, "suggested_sections": suggestions[:3]})
    return {"schema_version": 2, "task_revision": contract.get("revision"),
            "workspace_revision": revision, "checks": validated,
            "missing": [item["id"] for item in contract.get("requirements", ()) if item["id"] not in received],
            "references_valid": bool(expected) and received == expected and all(
                item["status"] == "referenced" for item in validated),
            "valid": False,
            "verification_level": "source_reference", "hardware_verified": False}


def requirement_feedback(contract: Mapping, report: Mapping) -> dict:
    """Keep actionable verification feedback within model observation limits.

    Durable reports retain every quote. Feedback omits repeated source excerpts
    so missing IDs and rejection reasons survive ordinary context compaction.
    """
    required = [item["id"] for item in contract.get("requirements", ())]
    specifications = evidence_requirements(contract)
    checks = [{"id": item["id"], "status": item["status"], "issues": item.get("issues", []),
               "requirement": specifications.get(item["id"], {}).get("text", "")[:1600],
               "expected_evidence": specifications.get(item["id"], {}).get("evidence_kinds", []),
               "submitted_evidence": [{key: ref[key] for key in ("path", "start_line", "end_line") if key in ref}
                                      for ref in item.get("evidence", ())],
               **({"suggested_sections": item["suggested_sections"]} if item.get("suggested_sections") else {}),
               **({"rejection_attempts": item["rejection_attempts"]} if item.get("rejection_attempts") else {})}
              for item in report.get("checks", ())]
    received = {item["id"] for item in checks}
    feedback = {"task_revision": contract.get("revision"), "workspace_revision": report.get("workspace_revision"),
            "required_ids": required, "checks": checks,
            "missing": [identity for identity in required if identity not in received],
            "unverified": [item["id"] for item in checks if item["status"] != "referenced"],
            "references_valid": report.get("references_valid", False), "valid": report.get("valid", False),
            "new_evidence_count": report.get("new_evidence_count", 0),
            "referenced_count": sum(item["status"] == "referenced" for item in checks),
            "rejected_count": sum(item["status"] != "referenced" for item in checks),
            "missing_count": len(required) - len(received),
            "verification_level": report.get("verification_level", "source_reference"),
            "evidence_recovery": report.get("evidence_recovery", {}),
            "next_action": ("Repeated rejected evidence must change: read the suggested documentation section or relevant implementation before submitting again. "
                            if report.get("evidence_recovery", {}).get("repeated_ids") else "") +
                "Read current files with line numbers. Submit missing or rejected IDs in batches of at most 8 using their expected_evidence types and exact {path,start_line,end_line}. "
                "For build, unit_tests or simulation, run the required host tool; only its successful result for current source counts. "
                "checks must be a JSON array. Accepted references still require independent review."}
    if report.get("references_valid") is True and not report.get("semantic_review"):
        feedback["next_action"] = ("All requirement references are accepted for the current source. "
            "Finish with a concise summary and no tool calls so the host can perform independent review. "
            "Do not resubmit unchanged citations. Accepted references alone do not establish completion.")
    # Preserve every ID/type/rejection reason even when optional explanatory
    # detail would overflow the model's bounded observation window.
    for item in sorted(checks, key=lambda check: check["status"] not in {"referenced", "reviewed"}):
        if item["status"] in {"referenced", "reviewed"}:
            for key in ("requirement", "submitted_evidence", "suggested_sections"):
                item.pop(key, None)
    for key in ("suggested_sections", "submitted_evidence", "requirement"):
        for item in reversed(checks):
            if len(json.dumps(feedback)) <= 15500:
                return feedback
            if key in item:
                item.pop(key)
                feedback["details_truncated"] = True
    return feedback


def _has_noncomment_quote(content: str, quote: str, suffix: str) -> bool:
    # Mask comments in the whole file, retaining offsets and string literals.
    # This handles spans that begin inside a comment and then include code.
    if suffix == ".ini":
        executable = re.sub(r"(?m)^[ \t]*[;#][^\r\n]*", lambda m: " " * len(m[0]), content)
    else:
        tokens = r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\r\n]*|/\*[\s\S]*?(?:\*/|$)'
        executable = re.sub(tokens, lambda m: " " * len(m[0]) if m[0].startswith(("//", "/*")) else m[0], content)
    for match in re.finditer(re.escape(quote), content):
        if executable[match.start():match.end()].strip():
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
