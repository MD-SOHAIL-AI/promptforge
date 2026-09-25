"""Deterministic session-turn routing for the Forge agent."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable


class AgentTurnIntent(str, Enum):
    CHAT = "chat"
    INSPECT = "inspect"
    EDIT = "edit"
    GENERATE = "generate"
    BUILD = "build"
    SIMULATE = "simulate"
    REPAIR = "repair"
    FLASH = "flash"
    MONITOR = "monitor"
    STOP_MONITOR = "stop_monitor"
    CANCEL = "cancel"
    CONFIRM = "confirm"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class AgentTurnContext:
    active_run_status: str | None = None
    last_run_status: str | None = None
    last_build_status: str | None = None
    has_workspace: bool = True
    has_platformio_ini: bool = False

    @property
    def pending_flash_confirmation(self) -> bool:
        return self.active_run_status == "awaiting_flash_confirmation"

    @property
    def active_operation(self) -> bool:
        return bool(self.active_run_status and self.active_run_status not in {"completed", "failed", "cancelled", "blocked", "timed_out"})

    @property
    def last_failed(self) -> bool:
        return self.last_run_status in {"failed", "blocked", "timed_out"} or self.last_build_status == "failed"


@dataclass(frozen=True, slots=True)
class AgentTurnDecision:
    intent: AgentTurnIntent
    confidence: float
    routing_source: str
    requires_workspace: bool = False
    requires_write: bool = False
    requires_build: bool = False
    requires_hardware: bool = False
    requires_confirmation: bool = False
    requested_actions: tuple[str, ...] = field(default_factory=tuple)
    prohibited_actions: tuple[str, ...] = field(default_factory=tuple)

    @property
    def starts_product_run(self) -> bool:
        return self.intent in {
            AgentTurnIntent.EDIT,
            AgentTurnIntent.GENERATE,
            AgentTurnIntent.BUILD,
            AgentTurnIntent.SIMULATE,
            AgentTurnIntent.REPAIR,
        }

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "intent": self.intent.value,
            "confidence": self.confidence,
            "routing_source": self.routing_source,
            "requires_workspace": self.requires_workspace,
            "requires_write": self.requires_write,
            "requires_build": self.requires_build,
            "requires_hardware": self.requires_hardware,
            "requires_confirmation": self.requires_confirmation,
            "requested_actions": list(self.requested_actions),
            "prohibited_actions": list(self.prohibited_actions),
        }

    def metadata(self) -> dict[str, object]:
        return {
            "intent": self.intent.value,
            "routing_source": self.routing_source,
            "requires_workspace": self.requires_workspace,
            "requires_write": self.requires_write,
            "requires_build": self.requires_build,
            "requires_hardware": self.requires_hardware,
            "requires_confirmation": self.requires_confirmation,
            "requested_actions": ",".join(self.requested_actions),
            "prohibited_actions": ",".join(self.prohibited_actions),
        }


class AgentTurnRouter:
    """Classify a user turn before the product-agent runtime can execute."""

    def route(self, content: str, context: AgentTurnContext | None = None) -> AgentTurnDecision:
        context = context or AgentTurnContext()
        normalized = _normalize(content)
        prohibited = _prohibited_actions(normalized)
        read_only = _has_read_only_constraint(prohibited)

        if not normalized:
            return _decision(AgentTurnIntent.UNKNOWN, 0.0, "fallback", prohibited=prohibited)

        if _is_stop_monitor(normalized):
            return _decision(
                AgentTurnIntent.STOP_MONITOR,
                0.98,
                "rule",
                requires_hardware=True,
                requested=("stop_monitor",),
                prohibited=prohibited,
            )
        if _is_cancel(normalized):
            return _decision(AgentTurnIntent.CANCEL, 0.98, "rule", prohibited=prohibited)
        if context.pending_flash_confirmation and _is_confirmation(normalized):
            return _decision(
                AgentTurnIntent.CONFIRM,
                0.98,
                "context",
                requires_workspace=True,
                requires_hardware=True,
                requires_confirmation=True,
                requested=("flash",),
                prohibited=prohibited,
            )

        if _is_high_confidence_chat(normalized):
            return _decision(AgentTurnIntent.CHAT, 0.96, "rule", prohibited=prohibited)

        if read_only and not (
            _is_flash_action(normalized)
            or _is_monitor_action(normalized)
            or _is_repair_action(normalized, context)
            or _is_simulate_action(normalized)
            or _is_build_action(normalized)
            or _is_edit_action(normalized)
            or _is_generate_action(normalized)
        ):
            if _has_workspace_reference(normalized) or _asks_about_recent_failure(normalized) or _is_inspect_request(normalized):
                return _decision(
                    AgentTurnIntent.INSPECT,
                    0.93,
                    "rule",
                    requires_workspace=True,
                    requested=("inspect",),
                    prohibited=prohibited,
                )
            return _decision(AgentTurnIntent.CHAT, 0.9, "rule", prohibited=prohibited)

        if _is_discussion_question(normalized):
            if _has_workspace_reference(normalized) or _asks_about_recent_failure(normalized):
                return _decision(
                    AgentTurnIntent.INSPECT,
                    0.9,
                    "rule",
                    requires_workspace=True,
                    requested=("inspect",),
                    prohibited=prohibited,
                )
            return _decision(AgentTurnIntent.CHAT, 0.9, "rule", prohibited=prohibited)

        if _is_flash_action(normalized):
            if "flash" in prohibited:
                return _decision(AgentTurnIntent.UNKNOWN, 0.4, "fallback", prohibited=prohibited)
            return _decision(
                AgentTurnIntent.FLASH,
                0.94,
                "rule",
                requires_workspace=True,
                requires_hardware=True,
                requires_confirmation=True,
                requested=("flash",),
                prohibited=prohibited,
            )

        if _is_monitor_action(normalized):
            if "monitor" in prohibited or "hardware" in prohibited:
                return _decision(AgentTurnIntent.UNKNOWN, 0.4, "fallback", prohibited=prohibited)
            return _decision(
                AgentTurnIntent.MONITOR,
                0.92,
                "rule",
                requires_workspace=True,
                requires_hardware=True,
                requested=("monitor",),
                prohibited=prohibited,
            )

        if _is_repair_action(normalized, context):
            if "write" in prohibited:
                return _decision(
                    AgentTurnIntent.INSPECT,
                    0.86,
                    "rule",
                    requires_workspace=True,
                    requested=("inspect",),
                    prohibited=prohibited,
                )
            return _decision(
                AgentTurnIntent.REPAIR,
                0.92,
                "rule",
                requires_workspace=True,
                requires_write=True,
                requires_build="build" not in prohibited,
                requested=_actions("repair", "edit", "build", "simulate" if _requests_simulation(normalized) else None, prohibited=prohibited),
                prohibited=prohibited,
            )

        if _is_generate_action(normalized):
            if "write" in prohibited:
                return _decision(AgentTurnIntent.INSPECT, 0.82, "rule", requires_workspace=True, requested=("inspect",), prohibited=prohibited)
            requires_build = (
                "build" not in prohibited
                and "execute" not in prohibited
                and (_is_embedded_project_request(normalized) or _requests_validation(normalized))
            )
            return _decision(
                AgentTurnIntent.GENERATE,
                0.88,
                "rule",
                requires_workspace=True,
                requires_write=True,
                requires_build=requires_build,
                requested=_actions("generate", "build" if requires_build else None, "simulate" if _requests_simulation(normalized) else None, prohibited=prohibited),
                prohibited=prohibited,
            )

        if _is_edit_action(normalized):
            if "write" in prohibited:
                return _decision(AgentTurnIntent.INSPECT, 0.84, "rule", requires_workspace=True, requested=("inspect",), prohibited=prohibited)
            requires_build = (_requests_validation(normalized) or _requests_simulation(normalized)) and "build" not in prohibited and "execute" not in prohibited
            return _decision(
                AgentTurnIntent.EDIT,
                0.9,
                "rule",
                requires_workspace=True,
                requires_write=True,
                requires_build=requires_build,
                requested=_actions("edit", "build" if requires_build else None, "simulate" if _requests_simulation(normalized) else None, prohibited=prohibited),
                prohibited=prohibited,
            )

        if _is_build_action(normalized):
            if "build" in prohibited or "execute" in prohibited:
                return _decision(AgentTurnIntent.INSPECT, 0.82, "rule", requires_workspace=True, requested=("inspect",), prohibited=prohibited)
            return _decision(
                AgentTurnIntent.BUILD,
                0.94,
                "rule",
                requires_workspace=True,
                requires_build=True,
                requested=_actions("build", "simulate" if _requests_simulation(normalized) else None, prohibited=prohibited),
                prohibited=prohibited,
            )

        if _is_simulate_action(normalized):
            if "build" in prohibited or "execute" in prohibited:
                return _decision(AgentTurnIntent.INSPECT, 0.84, "rule", requires_workspace=True, requested=("inspect",), prohibited=prohibited)
            return _decision(
                AgentTurnIntent.SIMULATE,
                0.94,
                "rule",
                requires_workspace=True,
                requires_build=True,
                requested=("build", "simulate"),
                prohibited=prohibited,
            )

        if _is_inspect_request(normalized) or _asks_about_recent_failure(normalized):
            return _decision(
                AgentTurnIntent.INSPECT,
                0.86,
                "rule",
                requires_workspace=True,
                requested=("inspect",),
                prohibited=prohibited,
            )

        return _decision(AgentTurnIntent.UNKNOWN, 0.2, "fallback", prohibited=prohibited)



def approved_plan_execution_decision() -> AgentTurnDecision:
    """Trusted route for an already hash-bound user-approved implementation plan."""
    return _decision(
        AgentTurnIntent.EDIT,
        1.0,
        "approved_plan",
        requires_workspace=True,
        requires_write=True,
        requires_build=True,
        requested=("execute_plan", "edit", "build"),
    )

def _decision(
    intent: AgentTurnIntent,
    confidence: float,
    routing_source: str,
    *,
    requires_workspace: bool = False,
    requires_write: bool = False,
    requires_build: bool = False,
    requires_hardware: bool = False,
    requires_confirmation: bool = False,
    requested: Iterable[str] = (),
    prohibited: Iterable[str] = (),
) -> AgentTurnDecision:
    return AgentTurnDecision(
        intent=intent,
        confidence=round(max(0.0, min(1.0, confidence)), 2),
        routing_source=routing_source,
        requires_workspace=requires_workspace,
        requires_write=requires_write,
        requires_build=requires_build,
        requires_hardware=requires_hardware,
        requires_confirmation=requires_confirmation,
        requested_actions=tuple(dict.fromkeys(item for item in requested if item)),
        prohibited_actions=tuple(dict.fromkeys(prohibited)),
    )


def _normalize(value: str) -> str:
    return " ".join(value.casefold().replace("’", "'").replace("`", "'").strip().split())


def _prohibited_actions(text: str) -> tuple[str, ...]:
    prohibited: list[str] = []
    if _contains_any(text, ("don't build", "do not build", "dont build", "don't compile", "do not compile", "dont compile", "don't run", "do not run", "don't run anything", "do not run anything")):
        prohibited.extend(("build", "execute"))
    if _contains_any(text, ("don't flash", "do not flash", "dont flash", "don't upload", "do not upload", "dont upload", "don't burn", "do not burn")):
        prohibited.extend(("flash", "hardware"))
    if _contains_any(text, ("don't monitor", "do not monitor", "dont monitor", "don't open serial", "do not open serial")):
        prohibited.extend(("monitor", "hardware"))
    # A scoped instruction such as "do not create fake files" must not revoke
    # permission to create the genuine files requested elsewhere in the turn.
    # Only a blanket file/code prohibition changes the tool capability.
    if _blanket_write_prohibition(text):
        prohibited.append("write")
    if _contains_any(text, ("read only", "read-only", "just explain", "only inspect", "only explain", "inspect only", "explain only")):
        prohibited.extend(("write", "build", "flash", "monitor", "hardware"))
    return tuple(dict.fromkeys(prohibited))


def _blanket_write_prohibition(text: str) -> bool:
    if _contains_any(text, ("don't touch the files", "do not touch the files", "don't change the files", "do not change the files")):
        return True
    action = r"(?:don't|do not|dont)\s+(?:modify|change|edit|write|create)"
    if re.search(rf"{action}\s+(?:(?:any|all|the|my|existing|project|source|these|those|new)\s+)*(?:files?|code|source|anything|changes?)\b", text):
        return True
    return bool(re.search(rf"{action}(?=\s*(?:$|[.!?;,]|\b(?:or|and|but)\b))", text))


def _has_read_only_constraint(prohibited: tuple[str, ...]) -> bool:
    return bool({"write", "build", "flash", "monitor", "hardware", "execute"}.intersection(prohibited))


def _is_cancel(text: str) -> bool:
    return text.strip(" .!?") in {"cancel", "stop", "abort", "never mind", "nevermind", "no", "nope", "skip", "don't flash", "do not flash", "dont flash", "stop it", "cancel it"}


def _is_confirmation(text: str) -> bool:
    return text.strip(" .!?") in {"yes", "yep", "yeah", "go ahead", "confirm", "continue", "proceed", "yes flash it", "flash it now", "upload it"}


def _is_high_confidence_chat(text: str) -> bool:
    normalized = text.strip(" .!?")
    if normalized in {"hi", "hii", "hello", "hey", "hi there", "hello there", "hey there", "thanks", "thank you", "thank you very much", "what can you do", "help", "help me"}:
        return True
    if re.search(r"\bwhat (model|provider) (are you|are you using|is selected|is active)\b", text):
        return True
    if re.search(r"\bwhat (board|environment) (is selected|are you using|is active)\b", text):
        return True
    if re.search(r"\bwhat is (pwm|platformio|esp[\s-]*idf|arduino|uart|i2c|spi|adc)\b", text):
        return True
    if re.search(r"\bexplain (pwm|platformio|esp[\s-]*idf|arduino|uart|i2c|spi|adc)\b", text):
        return True
    return False


def _is_discussion_question(text: str) -> bool:
    stripped = text.strip()
    if re.match(r"^(how|what|why|when|where|who)\b", stripped):
        return not _direct_action_question(stripped)
    if re.match(r"^(explain|tell me about|describe)\b", stripped):
        return not _has_workspace_reference(stripped)
    return False


def _direct_action_question(text: str) -> bool:
    return bool(re.search(r"\b(can|could|would) you (please )?(build|compile|simulate|run simulation|change|update|modify|edit|create|generate|fix|repair|flash|upload|open|start)\b", text))


def _is_flash_action(text: str) -> bool:
    if re.search(r"\b(how|what|why|when)\b.*\b(flash|upload|burn)\b", text):
        return False
    # A leading flash/upload/burn verb is an explicit physical-hardware action.
    # Keep this broad enough for modifiers such as "the verified firmware" while
    # still excluding explanatory questions above.
    return bool(re.match(r"^(please )?(flash|upload|burn)\b", text))


def _is_stop_monitor(text: str) -> bool:
    # Hardware controls must be direct requests. A project specification can
    # contain adjacent requirements such as "buzzer should stop\nmonitoring
    # values can still be displayed"; normalization joins those lines.
    return bool(re.fullmatch(
        r"(?:(?:please|can you|could you) )?"
        r"(?:(?:stop|close|disconnect|end) (?:the )?(?:serial monitor|monitor|serial connection)"
        r"|stop monitoring(?: serial output)?)"
        r"(?: now| please)?[.!?]*",
        text,
    ))


def _is_monitor_action(text: str) -> bool:
    return bool(re.fullmatch(
        r"(?:(?:please|can you|could you) )?"
        r"(?:(?:open|start|connect|watch|read) (?:the )?(?:serial monitor|monitor|serial output)"
        r"|monitor serial(?: output)?)"
        r"(?: now| please)?[.!?]*",
        text,
    ))


def _is_repair_action(text: str, context: AgentTurnContext) -> bool:
    if re.search(r"\bwhy\b.*\b(error|fail|build|compile|linker)\b", text):
        return False
    if re.search(r"\b(fix|repair|solve|correct)\b.*\b(build|compile|compiler|compilation|linker|error|failure|failed)\b", text):
        return True
    if text.strip(" .!?") in {"fix it", "fix that", "repair it", "repair that", "solve it"} and context.last_failed:
        return True
    return False


def _requests_simulation(text: str) -> bool:
    return bool(re.search(r"\b(simulate|simulation|wokwi)\b", text))


def _is_simulate_action(text: str) -> bool:
    if re.match(r"^(how|what|why|when)\b.*\b(simulate|simulation|wokwi)\b", text):
        return False
    return bool(
        re.search(r"^(simulate|run simulation|run the simulation|test in wokwi|run in wokwi)( it| this| the project| firmware| the firmware)?\b", text)
        or re.search(r"^(please )?(simulate|test in wokwi|run in wokwi)\b", text)
    )


def _is_build_action(text: str) -> bool:
    if re.match(r"^(how|what|why|when)\b.*\b(build|compile)\b", text):
        return False
    return bool(
        re.search(r"^(build|compile)( it| this| the project| this project| firmware| the firmware)?\b", text)
        or re.search(r"\b(run|start) (the )?(build|compile)\b", text)
        or re.search(r"\b(make sure|ensure|verify|validate|check) .*\b(compiles?|builds?)\b", text)
    )


def _is_edit_action(text: str) -> bool:
    if re.search(r"\bwhat\b.*\b(change|edit|modify)\b", text):
        return False
    return bool(re.search(r"\b(change|update|modify|edit|refactor|rename|remove|replace|set|switch|convert|port)\b", text) or re.search(r"^add\b", text))


def _is_generate_action(text: str) -> bool:
    if re.match(r"^(how|what|why|when)\b.*\b(create|generate|make|write|build)\b", text):
        return False
    # Negated quality constraints describe what to avoid; they are not a
    # standalone request to create files.
    text = re.sub(
        r"(?:don't|do not|dont)\s+(?:create|generate|write|make)\b[^.!?;]*(?:[.!?;]|$)",
        " ", text,
    )
    return bool(
        re.search(r"\b(create|generate|make|write|develop)\b.*\b(project|firmware|sketch|platformio|esp32|blink|sensor|monitor|controller)\b", text)
        or re.search(r"\b(?:i\s+)?(?:need|want)(?: you| u)? to build (?:me )?(?:a|an|new|complete)\b.*\b(project|firmware|sketch|platformio|esp32|sensor|monitor|controller)\b", text)
        or re.search(r"\b(create|generate|make)\b.*\b(dht11|led|wifi|wi-fi|temperature|pwm|adc|uart|i2c|spi)\b", text)
        or re.search(r"^build (me |a |an ).*\b(project|firmware|sketch|platformio|esp32|sensor|monitor|controller)\b", text)
        or re.search(r"\b(create|generate|write|make)\b.*\b(files?|modules?|components?|configs?|tests?|documentation|readme|makefile|dockerfile)\b", text)
        or re.search(r"\b(create|generate|write|make)\b.*\b[\w./-]+\.(?:c|cc|cpp|cs|css|go|h|hpp|html|ini|ino|java|js|json|kt|md|php|py|rb|rs|scss|sh|sql|svelte|toml|ts|tsx|txt|vue|xml|yaml|yml)\b", text)
    )


def _is_embedded_project_request(text: str) -> bool:
    return bool(re.search(r"\b(esp32|esp8266|arduino|platformio|firmware|sketch|microcontroller)\b", text))


def _is_inspect_request(text: str) -> bool:
    if _is_monitor_action(text):
        return False
    return bool(
        re.search(r"\b(show|read|look at|inspect|review|explain)\b.*\b(src/|main\.cpp|platformio\.ini|\.ino|\.h|\.hpp|\.c|\.cpp|line \d+|file|function|workspace|project|architecture|code)\b", text)
        or re.search(r"\bwhat does line \d+ do\b", text)
        or re.search(r"\bwhy is my esp32 restarting\b", text)
    )


def _asks_about_recent_failure(text: str) -> bool:
    return bool(
        re.search(r"\b(why|what|explain)\b.*\b(last|current|that)\b.*\b(build|compile|compiler|linker|error|failure|failed)\b", text)
        or re.search(r"\b(why|what|explain)\b.*\b(build|compile|compiler|linker)\b.*\b(error|failure|failed)\b", text)
        or re.search(r"\bexplain (the )?(error|build error|compiler error|failure)\b", text)
        or re.search(r"\bwhy did (the )?(last )?(build|compile|generation) fail\b", text)
    )


def _has_workspace_reference(text: str) -> bool:
    return bool(
        re.search(r"\b(src/|include/|lib/|test/|platformio\.ini|main\.cpp|\.ino|\.h|\.hpp|\.c|\.cpp|\.ini|line \d+|this file|this function|workspace|project files?|architecture|build error|compiler error|linker error)\b", text)
    )


def _requests_validation(text: str) -> bool:
    return bool(re.search(r"\b(make sure|ensure|verify|validate|check).*\b(compiles?|builds?)\b|\band (build|compile|validate)\b", text))


def _actions(*values: str | None, prohibited: Iterable[str]) -> tuple[str, ...]:
    denied = set(prohibited)
    return tuple(dict.fromkeys(value for value in values if value and value not in denied and not (value == "build" and "execute" in denied)))


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)
