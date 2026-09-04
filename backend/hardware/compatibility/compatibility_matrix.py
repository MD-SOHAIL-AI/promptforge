"""Deterministic compatibility queries backed by board metadata JSON files.

Board metadata is the sole source of compatibility policy. This module only
normalizes the metadata schema and performs immutable lookups; it contains no
board-specific capability rules.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, TypeAlias

__all__ = [
    "Board",
    "CompatibilityMatrix",
    "CompatibilityMetadataError",
    "Framework",
    "Library",
    "Peripheral",
]


class CompatibilityMetadataError(ValueError):
    """Board compatibility metadata is missing, malformed, or ambiguous."""


@dataclass(frozen=True, slots=True, order=True)
class Framework:
    """Canonical framework declared by board metadata."""

    name: str


@dataclass(frozen=True, slots=True, order=True)
class Peripheral:
    """Canonical peripheral declared by board metadata."""

    name: str


@dataclass(frozen=True, slots=True, order=True)
class Library:
    """Canonical library declared by board metadata."""

    name: str


@dataclass(frozen=True, slots=True)
class Board:
    """Immutable normalized board compatibility record."""

    board_id: str
    board_name: str
    vendor: str | None
    architecture: str | None
    frameworks: tuple[Framework, ...]
    peripherals: tuple[Peripheral, ...]
    libraries: Mapping[str, tuple[Library, ...]]
    flash_size: int | None = None
    ram_size: int | None = None
    gpio_count: int | None = None
    valid_gpio: tuple[int | str, ...] = ()
    reserved_gpio: tuple[int | str, ...] = ()
    input_only_gpio: tuple[int | str, ...] = ()
    peripheral_limits: Mapping[str, int] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def supported_libraries(
        self,
        framework: str | Framework | None = None,
    ) -> tuple[Library, ...]:
        """Return libraries for a framework or the board-wide union."""

        if framework is not None:
            return self.libraries.get(_normalize(framework), ())
        unique: dict[str, Library] = {}
        for libraries in self.libraries.values():
            for library in libraries:
                unique.setdefault(_normalize(library), library)
        return tuple(sorted(unique.values(), key=lambda item: item.name.casefold()))


BoardQuery: TypeAlias = str | Board | Enum
NameQuery: TypeAlias = str | Framework | Peripheral | Library | Enum


class CompatibilityMatrix:
    """Read-only compatibility engine loaded from board metadata.

    Board queries accept the JSON filename, canonical ``board_name``, or a
    unique shortened board name derived from metadata. Name matching is case-
    and punctuation-insensitive. Results are returned in stable lexical order.
    """

    def __init__(self, boards_directory: str | Path | None = None) -> None:
        directory = (
            Path(boards_directory)
            if boards_directory is not None
            else Path(__file__).resolve().parents[1] / "boards"
        )
        boards = _load_board_directory(directory)
        self._install(boards)

    @classmethod
    def from_metadata(
        cls,
        metadata: Mapping[str, Mapping[str, object]],
    ) -> CompatibilityMatrix:
        """Build a matrix from loaded metadata without filesystem access."""

        if not isinstance(metadata, Mapping):
            raise CompatibilityMetadataError("board metadata must be a mapping")
        boards = tuple(
            _parse_board(board_id, raw, source=f"metadata[{board_id!r}]")
            for board_id, raw in sorted(
                metadata.items(), key=lambda item: str(item[0]).casefold()
            )
        )
        instance = cls.__new__(cls)
        instance._install(boards)
        return instance

    def _install(self, boards: tuple[Board, ...]) -> None:
        if not boards:
            raise CompatibilityMetadataError("board metadata cannot be empty")
        board_map = {board.board_id: board for board in boards}
        if len(board_map) != len(boards):
            raise CompatibilityMetadataError("duplicate normalized board IDs")
        self._boards: Mapping[str, Board] = MappingProxyType(board_map)
        self._aliases: Mapping[str, str] = MappingProxyType(
            _build_aliases(boards)
        )

    @property
    def boards(self) -> tuple[Board, ...]:
        """Return every board in deterministic display-name order."""

        return tuple(
            sorted(self._boards.values(), key=lambda item: item.board_name.casefold())
        )

    def get_board(self, board: BoardQuery) -> Board | None:
        """Resolve a board identifier, returning ``None`` when unknown."""

        if isinstance(board, Board):
            return self._boards.get(board.board_id)
        board_id = self._aliases.get(_normalize(board))
        return self._boards.get(board_id) if board_id else None

    def is_framework_supported(
        self,
        board: BoardQuery,
        framework: NameQuery,
    ) -> bool:
        """Return whether a framework is declared for a board."""

        resolved = self.get_board(board)
        target = _normalize(framework)
        return bool(
            resolved
            and target
            and any(_normalize(item) == target for item in resolved.frameworks)
        )

    def is_peripheral_supported(
        self,
        board: BoardQuery,
        peripheral: NameQuery,
    ) -> bool:
        """Return whether a peripheral is declared for a board."""

        resolved = self.get_board(board)
        target = _normalize(peripheral)
        return bool(
            resolved
            and target
            and any(_normalize(item) == target for item in resolved.peripherals)
        )

    def is_library_supported(
        self,
        board: BoardQuery,
        library: NameQuery,
        framework: NameQuery | None = None,
    ) -> bool:
        """Return whether a library is approved for a board/framework.

        If no framework is supplied, a declaration under any board framework
        is sufficient. PlatformIO owner and version syntax is normalized.
        """

        resolved = self.get_board(board)
        target = _normalize_library(library)
        if resolved is None or not target:
            return False
        if framework is not None:
            if not self.is_framework_supported(resolved, framework):
                return False
            libraries = resolved.supported_libraries(_query_text(framework))
        else:
            libraries = resolved.supported_libraries()
        return any(_normalize_library(item) == target for item in libraries)

    def get_compatible_boards(
        self,
        framework: NameQuery | None = None,
        peripheral: NameQuery | Iterable[NameQuery] | None = None,
        library: NameQuery | Iterable[NameQuery] | None = None,
    ) -> tuple[str, ...]:
        """Return boards satisfying every supplied compatibility filter."""

        peripherals = _query_values(peripheral)
        libraries = _query_values(library)
        compatible: list[str] = []
        for board in self.boards:
            if framework is not None and not self.is_framework_supported(
                board, framework
            ):
                continue
            if any(
                not self.is_peripheral_supported(board, item)
                for item in peripherals
            ):
                continue
            if any(
                not self.is_library_supported(board, item, framework)
                for item in libraries
            ):
                continue
            compatible.append(board.board_name)
        return tuple(compatible)

    def get_supported_frameworks(self, board: BoardQuery) -> tuple[str, ...]:
        """Return canonical framework names in metadata order."""

        resolved = self.get_board(board)
        return tuple(item.name for item in resolved.frameworks) if resolved else ()


def _load_board_directory(directory: Path) -> tuple[Board, ...]:
    if not directory.is_dir():
        raise CompatibilityMetadataError(
            f"board metadata directory does not exist: {directory}"
        )
    files = tuple(sorted(directory.glob("*.json"), key=lambda item: item.name.casefold()))
    if not files:
        raise CompatibilityMetadataError(
            f"board metadata directory contains no JSON files: {directory}"
        )
    boards: list[Board] = []
    for path in files:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CompatibilityMetadataError(
                f"cannot load board metadata {path.name}: {exc}"
            ) from exc
        boards.append(_parse_board(path.stem, raw, source=path.name))
    return tuple(boards)


def _parse_board(board_id: object, raw: object, *, source: str) -> Board:
    identifier = _required_text(board_id, field="board id", source=source)
    if not isinstance(raw, Mapping):
        raise CompatibilityMetadataError(f"{source} must contain a JSON object")
    board_name = _required_text(raw.get("board_name"), field="board_name", source=source)
    frameworks = _required_name_sequence(
        raw.get("frameworks", raw.get("supported_frameworks")),
        field="frameworks",
        source=source,
        value_type=Framework,
    )
    declared_peripherals = _required_name_sequence(
        raw.get("peripherals"),
        field="peripherals",
        source=source,
        value_type=Peripheral,
    )
    peripherals = _with_supported_capabilities(raw, declared_peripherals)
    return Board(
        board_id=_normalize_id(identifier),
        board_name=board_name,
        vendor=_optional_text(raw.get("vendor"), field="vendor", source=source),
        architecture=_optional_text(
            raw.get("architecture"), field="architecture", source=source
        ),
        frameworks=frameworks,
        peripherals=peripherals,
        libraries=MappingProxyType(
            _parse_libraries(raw.get("libraries", {}), frameworks, source)
        ),
        flash_size=_optional_nonnegative_integer(
            raw.get("flash_size", raw.get("flash")),
            field="flash_size",
            source=source,
        ),
        ram_size=_optional_nonnegative_integer(
            raw.get("ram_size", raw.get("ram")),
            field="ram_size",
            source=source,
        ),
        gpio_count=_optional_nonnegative_integer(
            raw.get("gpio_count"), field="gpio_count", source=source
        ),
        valid_gpio=_pin_sequence(
            raw.get("valid_gpio", ()), field="valid_gpio", source=source
        ),
        reserved_gpio=_pin_sequence(
            raw.get("reserved_gpio", ()), field="reserved_gpio", source=source
        ),
        input_only_gpio=_pin_sequence(
            raw.get("input_only_gpio", ()),
            field="input_only_gpio",
            source=source,
        ),
        peripheral_limits=MappingProxyType(
            _peripheral_limits(raw.get("peripheral_limits", {}), source)
        ),
    )


def _parse_libraries(
    value: object,
    frameworks: tuple[Framework, ...],
    source: str,
) -> dict[str, tuple[Library, ...]]:
    framework_names = {_normalize(item): item.name for item in frameworks}
    result: dict[str, tuple[Library, ...]] = {
        name: () for name in framework_names
    }
    if value is None:
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        libraries = _name_sequence(
            value, field="libraries", source=source, value_type=Library
        )
        return {name: libraries for name in framework_names}
    if not isinstance(value, Mapping):
        raise CompatibilityMetadataError(
            f"{source}.libraries must be an object or array"
        )
    for raw_framework, raw_libraries in value.items():
        framework_key = _normalize(raw_framework)
        if framework_key not in framework_names:
            raise CompatibilityMetadataError(
                f"{source}.libraries references undeclared framework {raw_framework!r}"
            )
        result[framework_key] = _name_sequence(
            raw_libraries,
            field=f"libraries.{raw_framework}",
            source=source,
            value_type=Library,
        )
    return result


def _with_supported_capabilities(
    raw: Mapping[object, object],
    peripherals: tuple[Peripheral, ...],
) -> tuple[Peripheral, ...]:
    """Include boolean ``*_support`` capabilities as peripherals."""

    result = list(peripherals)
    seen = {_normalize(item) for item in result}
    for key, enabled in raw.items():
        if not isinstance(key, str) or not key.casefold().endswith("_support"):
            continue
        if enabled is not True:
            continue
        capability = key[: -len("_support")].strip("_ ")
        normalized = _normalize(capability)
        if not normalized or normalized in seen:
            continue
        display_name = _display_capability(capability)
        result.append(Peripheral(display_name))
        seen.add(normalized)
    return tuple(result)


def _display_capability(value: str) -> str:
    words = re.findall(r"[a-z0-9]+", value.casefold())
    formatted: list[str] = []
    for word in words:
        if word == "wifi":
            formatted.append("WiFi")
        elif len(word) <= 4:
            formatted.append(word.upper())
        else:
            formatted.append(word.capitalize())
    return " ".join(formatted)


def _required_name_sequence(
    value: object,
    *,
    field: str,
    source: str,
    value_type: type[Framework] | type[Peripheral],
) -> Any:
    result = _name_sequence(
        value, field=field, source=source, value_type=value_type
    )
    if not result:
        raise CompatibilityMetadataError(f"{source}.{field} cannot be empty")
    return result


def _name_sequence(
    value: object,
    *,
    field: str,
    source: str,
    value_type: type[Framework] | type[Peripheral] | type[Library],
) -> Any:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise CompatibilityMetadataError(f"{source}.{field} must be an array")
    result: list[Any] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        name = _required_text(item, field=f"{field}[{index}]", source=source)
        normalized = _normalize(name)
        if normalized in seen:
            raise CompatibilityMetadataError(
                f"{source}.{field} contains duplicate value {name!r}"
            )
        seen.add(normalized)
        result.append(value_type(name))
    return tuple(result)


def _build_aliases(boards: tuple[Board, ...]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for board in boards:
        for candidate in {
            board.board_id,
            board.board_name,
            _short_board_name(board.board_name),
        }:
            normalized = _normalize(candidate)
            if not normalized:
                continue
            existing = aliases.get(normalized)
            if existing is not None and existing != board.board_id:
                raise CompatibilityMetadataError(
                    f"ambiguous board alias {candidate!r}"
                )
            aliases[normalized] = board.board_id
    return aliases


def _short_board_name(name: str) -> str:
    value = re.sub(r"\b(?:devkit|rev)\s*[a-z0-9-]*\b", " ", name, flags=re.I)
    value = re.sub(r"\bblue\s+pill\b", " ", value, flags=re.I)
    return " ".join(value.split())


def _query_values(
    value: NameQuery | Iterable[NameQuery] | None,
) -> tuple[NameQuery, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, Enum, Framework, Peripheral, Library)):
        return (value,)
    try:
        return tuple(value)
    except TypeError:
        return (value,)


def _normalize_library(value: object) -> str:
    text = _query_text(value).strip()
    if not text:
        return ""
    text = re.split(r"\s*@\s*|\s*=\s*|\s+\^?~?[<>=]", text, maxsplit=1)[0]
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    return _normalize(text)


def _normalize_id(value: str) -> str:
    return "_".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _normalize(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", _query_text(value).casefold())


def _query_text(value: object) -> str:
    if isinstance(value, (Framework, Peripheral, Library)):
        return value.name
    if isinstance(value, Board):
        return value.board_id
    if isinstance(value, Enum):
        value = value.value
    return value.strip() if isinstance(value, str) else ""


def _required_text(value: object, *, field: str, source: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise CompatibilityMetadataError(
            f"{source}.{field} must be a non-empty NUL-free string"
        )
    return value.strip()


def _optional_text(value: object, *, field: str, source: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field=field, source=source)


def _optional_nonnegative_integer(
    value: object,
    *,
    field: str,
    source: str,
) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CompatibilityMetadataError(
            f"{source}.{field} must be a non-negative integer"
        )
    return value


def _pin_sequence(
    value: object,
    *,
    field: str,
    source: str,
) -> tuple[int | str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise CompatibilityMetadataError(f"{source}.{field} must be an array")
    result: list[int | str] = []
    seen: set[str] = set()
    for index, pin in enumerate(value):
        if isinstance(pin, bool) or not isinstance(pin, (int, str)):
            raise CompatibilityMetadataError(
                f"{source}.{field}[{index}] must be an integer or string"
            )
        if isinstance(pin, str):
            pin = _required_text(pin, field=f"{field}[{index}]", source=source)
        normalized = _normalize_pin(pin)
        if normalized in seen:
            raise CompatibilityMetadataError(
                f"{source}.{field} contains duplicate pin {pin!r}"
            )
        seen.add(normalized)
        result.append(pin)
    return tuple(result)


def _peripheral_limits(value: object, source: str) -> dict[str, int]:
    if not isinstance(value, Mapping):
        raise CompatibilityMetadataError(
            f"{source}.peripheral_limits must be an object"
        )
    result: dict[str, int] = {}
    for peripheral, limit in value.items():
        name = _required_text(
            peripheral, field="peripheral_limits key", source=source
        )
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise CompatibilityMetadataError(
                f"{source}.peripheral_limits.{name} must be a non-negative integer"
            )
        normalized = _normalize(name)
        if normalized in result:
            raise CompatibilityMetadataError(
                f"{source}.peripheral_limits contains duplicate peripheral {name!r}"
            )
        result[normalized] = limit
    return result


def _normalize_pin(value: int | str) -> str:
    if isinstance(value, int):
        return str(value)
    return re.sub(r"[^a-z0-9]+", "", value.casefold())
