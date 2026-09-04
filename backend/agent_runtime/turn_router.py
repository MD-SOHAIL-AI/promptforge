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
                requested=_actions("repair", "edit", "build", prohibited=prohibited),
                prohibited=prohibited,
            )

        if _is_generate_action(normalized):
            if "write" in prohibited:
                return _decision(AgentTurnIntent.INSPECT, 0.82, "rule", requires_workspace=True, requested=("inspect",), prohibited=prohibited)
            requires_build = "build" not in prohibited and "execute" not in prohibited
            return _decision(
                AgentTurnIntent.GENERATE,
                0.88,
                "rule",
                requires_workspace=True,
                requires_write=True,
                requires_build=requires_build,
                requested=_actions("generate", "build" if requires_build else None, prohibited=prohibited),
                prohibited=prohibited,
            )

        if _is_edit_action(normalized):
            if "write" in prohibited:
                return _decision(AgentTurnIntent.INSPECT, 0.84, "rule", requires_workspace=True, requested=("inspect",), prohibited=prohibited)
            requires_build = _requests_validation(normalized) and "build" not in prohibited and "execute" not in prohibited
            return _decision(
                AgentTurnIntent.EDIT,
                0.9,
                "rule",
                requires_workspace=True,
                requires_write=True,
                requires_build=requires_build,
                requested=_actions("edit", "build" if requires_build else None, prohibited=prohibited),
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
                requested=("build",),
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
    if _contains_any(text, ("don't modify", "do not modify", "dont modify", "don't change", "do not change", "don't edit", "do not edit", "don't write", "do not write", "don't create", "do not create", "don't touch the files", "don't change the files")):
        prohibited.append("write")
    if _contains_any(text, ("read only", "read-only", "just explain", "only inspect", "only explain", "inspect only", "explain only")):
        prohibited.extend(("write", "build", "flash", "monitor", "hardware"))
    return tuple(dict.fromkeys(prohibited))


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
    return bool(re.search(r"\b(can|could|would) you (please )?(build|compile|change|update|modify|edit|create|generate|fix|repair|flash|upload|open|start)\b", text))


def _is_flash_action(text: str) -> bool:
    if re.search(r"\b(how|what|why|when)\b.*\b(flash|upload|burn)\b", text):
        return False
    return bool(re.search(r"(^|\b)(flash|upload|burn) (it|this|firmware|the firmware|my esp32|to my esp32|the board)\b", text))


def _is_stop_monitor(text: str) -> bool:
    return bool(re.search(r"\b(stop|close|disconnect|end) (the )?(serial monitor|monitor|serial connection)\b|\bstop monitoring\b", text))


def _is_monitor_action(text: str) -> bool:
    if re.search(r"\b(how|what|why|when)\b.*\b(serial monitor|monitor)\b", text):
        return False
    return bool(re.search(r"\b(open|start|connect|watch|read) (the )?(serial monitor|monitor|serial output)\b|\bmonitor serial\b", text))


def _is_repair_action(text: str, context: AgentTurnContext) -> bool:
    if re.search(r"\bwhy\b.*\b(error|fail|build|compile|linker)\b", text):
        return False
    if re.search(r"\b(fix|repair|solve|correct)\b.*\b(build|compile|compiler|compilation|linker|error|failure|failed)\b", text):
        return True
    if text.strip(" .!?") in {"fix it", "fix that", "repair it", "repair that", "solve it"} and context.last_failed:
        return True
    return False


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
    return bool(
        re.search(r"\b(create|generate|make|write|develop)\b.*\b(project|firmware|sketch|platformio|esp32|blink|sensor|monitor|controller)\b", text)
        or re.search(r"\b(create|generate|make)\b.*\b(dht11|led|wifi|wi-fi|temperature|pwm|adc|uart|i2c|spi)\b", text)
        or re.search(r"^build\b.*\b(project|firmware|sketch|platformio|esp32|sensor|monitor|controller)\b", text)
    )


def _is_inspect_request(text: str) -> bool:
    if _is_monitor_action(text):
        return False
    return bool(
        re.search(r"\b(show|read|look at|inspect|review|explain)\b.*\b(src/|main\.cpp|platformio\.ini|\.ino|\.h|\.hpp|\.c|\.cpp|line \d+|file|function|workspace|project|code)\b", text)
        or re.search(r"\bwhat does line \d+ do\b", text)
        or re.search(r"\bwhy is my esp32 restarting\b", text)
        or re.search(r"\bdiagnose\b.*\b(build|compile|compiler|linker|error|failure|failed|problem|issue)\b", text)
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
        re.search(r"\b(src/|include/|lib/|test/|platformio\.ini|main\.cpp|\.ino|\.h|\.hpp|\.c|\.cpp|\.ini|line \d+|this file|this function|workspace|project files?|build error|compiler error|linker error)\b", text)
    )


def _requests_validation(text: str) -> bool:
    return bool(re.search(r"\b(make sure|ensure|verify|validate|check).*\b(compiles?|builds?)\b|\band (build|compile|validate)\b", text))


def _actions(*values: str | None, prohibited: Iterable[str]) -> tuple[str, ...]:
    denied = set(prohibited)
    return tuple(dict.fromkeys(value for value in values if value and value not in denied and not (value == "build" and "execute" in denied)))


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)
