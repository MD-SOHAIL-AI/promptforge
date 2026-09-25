"""Conservative deterministic repairs for well-known embedded build failures."""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


_ARDUINO_SYMBOLS = (
    "Serial",
    "pinMode",
    "digitalRead",
    "digitalWrite",
    "analogRead",
    "analogWrite",
    "delay",
    "millis",
    "micros",
)
_CPP_SUFFIXES = frozenset({".cpp", ".cc", ".cxx"})
_HEADER_SUFFIXES = frozenset({".h", ".hpp"})
_MAX_SOURCE_BYTES = 1_000_000
_MAX_REPAIRED_FILES = 8


@dataclass(frozen=True, slots=True)
class DeterministicRepair:
    repair_id: str
    changed_paths: tuple[str, ...]
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "repair_id": self.repair_id,
            "changed_paths": list(self.changed_paths),
            "reason": self.reason,
        }


def repair_missing_arduino_core_include(
    workspace_root: str | Path,
    build_result: Mapping[str, object],
) -> DeterministicRepair | None:
    """Insert ``Arduino.h`` only when framework and compiler evidence agree.

    This intentionally handles one narrow class of generated-code defect. It
    never guesses board pins, libraries, types, or APIs and never follows a
    symlink outside the managed stage.
    """

    root = Path(workspace_root).resolve(strict=True)
    if not _uses_arduino_framework(root):
        return None
    diagnostics = build_result.get("diagnostics")
    if not isinstance(diagnostics, str) or not diagnostics.strip():
        return None
    implicated_symbols = tuple(
        symbol
        for symbol in _ARDUINO_SYMBOLS
        if re.search(
            rf"(?:['`\u2018\u2019]\s*)?{re.escape(symbol)}(?:\s*['`\u2018\u2019])?.{{0,80}}(?:not declared|was not declared)",
            diagnostics,
            flags=re.IGNORECASE,
        )
    )
    header_candidates = _diagnostic_header_paths(root, diagnostics)
    if not implicated_symbols and not header_candidates:
        return None

    diagnostic_paths = _diagnostic_cpp_paths(root, diagnostics)
    candidates = diagnostic_paths or tuple(
        path
        for source_root in (root / "src", root / "lib")
        if source_root.is_dir()
        for path in source_root.rglob("*")
        if path.suffix.casefold() in _CPP_SUFFIXES
    )
    changed: list[str] = []
    for candidate in (*header_candidates, *candidates):
        if len(changed) >= _MAX_REPAIRED_FILES:
            break
        path = _safe_source_path(root, candidate, _HEADER_SUFFIXES | _CPP_SUFFIXES)
        if path is None or path.stat().st_size > _MAX_SOURCE_BYTES:
            continue
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            continue
        if re.search(r"^\s*#\s*include\s*[<\"]Arduino\.h[>\"]", text, re.MULTILINE):
            continue
        if path.suffix.casefold() in _CPP_SUFFIXES and not any(
            re.search(rf"\b{re.escape(symbol)}\b", text) for symbol in implicated_symbols
        ):
            continue
        newline = "\r\n" if "\r\n" in text else "\n"
        repaired = f"#include <Arduino.h>{newline}{newline}{text.lstrip(chr(0xfeff))}"
        _atomic_write(path, repaired.encode("utf-8"))
        changed.append(path.relative_to(root).as_posix())
    if not changed:
        return None
    return DeterministicRepair(
        repair_id="arduino-core-include-v1",
        changed_paths=tuple(changed),
        reason=(
            "Arduino framework types or symbols were rejected in generated source "
            "that omitted <Arduino.h>."
        ),
    )


def repair_verified_cpp_build_errors(
    workspace_root: str | Path, build_result: Mapping[str, object],
) -> DeterministicRepair | None:
    """Apply only source repairs supported by this build's compiler diagnostics."""
    include_repair = repair_missing_arduino_core_include(workspace_root, build_result)
    collision_repair = _repair_system_name_collision(workspace_root, build_result)
    dependency_repair = _repair_misnamed_platformio_dependency(workspace_root, build_result)
    declaration_repair = _repair_missing_member_declaration(workspace_root, build_result)
    signature_repair = _repair_member_definition_signatures(workspace_root, build_result)
    constructor_repair = _repair_defaulted_constructor_definition(workspace_root, build_result)
    repairs = tuple(item for item in (
        include_repair, collision_repair, dependency_repair, declaration_repair,
        signature_repair, constructor_repair,
    ) if item is not None)
    if not repairs:
        return None
    return DeterministicRepair(
        repair_id="verified-cpp-build-repair-v1",
        changed_paths=tuple(dict.fromkeys(path for item in repairs for path in item.changed_paths)),
        reason=" ".join(item.reason for item in repairs),
    )


def _repair_misnamed_platformio_dependency(
    workspace_root: str | Path, build_result: Mapping[str, object],
) -> DeterministicRepair | None:
    """Replace an unresolved lib_deps name only when registry evidence is unique."""
    root = Path(workspace_root).resolve(strict=True)
    ini = root / "platformio.ini"
    if not ini.is_file() or ini.is_symlink() or ini.stat().st_size > 256_000:
        return None
    diagnostics = build_result.get("diagnostics")
    candidates = build_result.get("dependency_candidates")
    if not isinstance(diagnostics, str) or not isinstance(candidates, list):
        return None
    missing_headers = {
        header.casefold()
        for header in re.findall(
            r"fatal error:\s*([^\s:<>\r\n]+\.(?:h|hpp)):\s*No such file or directory",
            diagnostics, re.IGNORECASE,
        )
    }
    if not missing_headers and "Could not find the package with" not in diagnostics:
        return None
    try:
        original = ini.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None
    lines = original.splitlines(keepends=True)
    in_lib_deps = False
    changed = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("["):
            in_lib_deps = False
        match = re.match(r"^(\s*lib_deps\s*=\s*)(.*?)(\r?\n)?$", line, re.IGNORECASE)
        if match:
            in_lib_deps = True
            prefix, value, ending = match.group(1), match.group(2), match.group(3) or ""
        elif in_lib_deps and (line[:1].isspace() or not stripped):
            prefix = line[:len(line) - len(line.lstrip())]
            value = stripped
            ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
        else:
            in_lib_deps = False
            continue
        if not value or value.startswith((";", "#")):
            continue
        dependency = re.split(r"\s*@\s*", value, maxsplit=1)[0].strip()
        if not dependency:
            continue
        verified: set[str] = set()
        for candidate in candidates:
            if not isinstance(candidate, dict) or candidate.get("source") != "platformio_registry":
                continue
            package = candidate.get("package")
            query = candidate.get("query")
            if not isinstance(package, str) or not isinstance(query, str) or "/" not in package:
                continue
            publisher, name = package.split("/", 1)
            if not publisher or not name or not re.fullmatch(r"[\w. -]{1,100}", package.replace("/", " ")):
                continue
            if query.casefold().startswith("header:"):
                if query[7:].casefold() not in missing_headers:
                    continue
            elif _dependency_name_key(query) != _dependency_name_key(value):
                continue
            if dependency.casefold() == package.casefold():
                continue
            if not _dependency_matches_candidate(dependency, publisher, name):
                continue
            verified.add(package)
        if len(verified) != 1:
            continue
        replacement = next(iter(verified))
        if "@" in value:
            replacement += " @ " + value.split("@", 1)[1].strip()
        lines[index] = prefix + replacement + ending
        changed = True
    if not changed:
        return None
    _atomic_write(ini, "".join(lines).encode("utf-8"))
    return DeterministicRepair(
        repair_id="platformio-registry-dependency-v1",
        changed_paths=("platformio.ini",),
        reason="PlatformIO registry identified the canonical package for an unresolved library name.",
    )


def _dependency_name_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.split("@", 1)[0].casefold())


def _dependency_matches_candidate(dependency: str, publisher: str, name: str) -> bool:
    key = _dependency_name_key(dependency)
    publisher_key = _dependency_name_key(publisher)
    name_key = _dependency_name_key(name)
    if not publisher_key or not name_key:
        return False
    while key.startswith(publisher_key):
        key = key[len(publisher_key):]
    return key == name_key


def _repair_missing_member_declaration(
    workspace_root: str | Path, build_result: Mapping[str, object],
) -> DeterministicRepair | None:
    """Declare a defined zero-argument member when the compiler locates its class."""
    root = Path(workspace_root).resolve(strict=True)
    diagnostics = build_result.get("diagnostics")
    if not isinstance(diagnostics, str):
        return None
    error = re.search(
        r"(?m)^(.+?\.cpp):\d+(?::\d+)?: error: no declaration matches "
        r"'void ([A-Za-z_]\w*)::([A-Za-z_]\w*)\(\)'",
        diagnostics,
    )
    if error is None:
        return None
    source_raw = Path(error.group(1).strip())
    source = _safe_source_path(root, source_raw if source_raw.is_absolute() else root / source_raw)
    if source is None or source.stat().st_size > _MAX_SOURCE_BYTES:
        return None
    class_name, method_name = error.group(2), error.group(3)
    note = re.search(
        rf"(?m)^(.+?\.(?:h|hpp)):\d+(?::\d+)?: note: 'class {re.escape(class_name)}' defined here",
        diagnostics,
    )
    if note is None:
        return None
    header_raw = Path(note.group(1).strip())
    header = _safe_source_path(root, header_raw if header_raw.is_absolute() else root / header_raw, _HEADER_SUFFIXES)
    if header is None or header.stat().st_size > _MAX_SOURCE_BYTES:
        return None
    try:
        source_text = source.read_text(encoding="utf-8-sig")
        header_text = header.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None
    definition = re.compile(rf"(?m)^\s*void\s+{re.escape(class_name)}::{re.escape(method_name)}\s*\(\s*\)\s*\{{")
    if len(definition.findall(source_text)) != 1 or re.search(rf"\b{re.escape(method_name)}\s*\(", header_text):
        return None
    class_pattern = re.compile(rf"(?s)\bclass\s+{re.escape(class_name)}\s*\{{(.*?)\n\s*\}}\s*;")
    classes = list(class_pattern.finditer(header_text))
    if len(classes) != 1:
        return None
    class_body = classes[0].group(1)
    private = re.search(r"(?m)^(\s*)private:\s*(\r?\n)", class_body)
    if private is None:
        return None
    insert_at = classes[0].start(1) + private.end()
    newline = private.group(2)
    updated = header_text[:insert_at] + f"    void {method_name}();{newline}" + header_text[insert_at:]
    _atomic_write(header, updated.encode("utf-8"))
    return DeterministicRepair(
        repair_id="cpp-member-declaration-v1",
        changed_paths=(header.relative_to(root).as_posix(),),
        reason="Compiler found a defined zero-argument member missing from its class declaration.",
    )


def _repair_member_definition_signatures(
    workspace_root: str | Path, build_result: Mapping[str, object],
) -> DeterministicRepair | None:
    root = Path(workspace_root).resolve(strict=True)
    diagnostics = build_result.get("diagnostics")
    if not isinstance(diagnostics, str):
        return None
    changed: list[str] = []
    errors = re.finditer(
        r"(?m)^(.+?\.cpp):\d+(?::\d+)?: error: no declaration matches "
        r"'void ([A-Za-z_]\w*)::([A-Za-z_]\w*)\(\)'",
        diagnostics,
    )
    for error in errors:
        if len(changed) >= _MAX_REPAIRED_FILES:
            break
        raw = Path(error.group(1).strip())
        source = _safe_source_path(root, raw if raw.is_absolute() else root / raw)
        if source is None or source.stat().st_size > _MAX_SOURCE_BYTES:
            continue
        class_name, method_name = error.group(2), error.group(3)
        candidate = re.search(
            rf"(?m)^(.+?\.(?:h|hpp)):\d+(?::\d+)?: note: candidate is: "
            rf"'void {re.escape(class_name)}::{re.escape(method_name)}\(uint32_t\)'",
            diagnostics,
        )
        if candidate is None:
            continue
        header_raw = Path(candidate.group(1).strip())
        header = _safe_source_path(root, header_raw if header_raw.is_absolute() else root / header_raw, _HEADER_SUFFIXES)
        if header is None or header.stat().st_size > _MAX_SOURCE_BYTES:
            continue
        try:
            header_text = header.read_text(encoding="utf-8-sig")
            source_text = source.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            continue
        if not re.search(rf"\bvoid\s+{re.escape(method_name)}\s*\(\s*uint32_t\s+currentMillis\s*\)\s*;", header_text):
            continue
        definition = re.compile(rf"(?m)^(\s*void\s+{re.escape(class_name)}::{re.escape(method_name)}\s*)\(\s*\)(\s*\{{)")
        if len(definition.findall(source_text)) != 1:
            continue
        updated = definition.sub(r"\g<1>(uint32_t currentMillis)\g<2>", source_text)
        _atomic_write(source, updated.encode("utf-8"))
        relative = source.relative_to(root).as_posix()
        if relative not in changed:
            changed.append(relative)
    if not changed:
        return None
    return DeterministicRepair(
        repair_id="cpp-member-signature-v1",
        changed_paths=tuple(changed),
        reason="Compiler identified member definitions missing the parameter declared in their header.",
    )


def _repair_defaulted_constructor_definition(
    workspace_root: str | Path, build_result: Mapping[str, object],
) -> DeterministicRepair | None:
    root = Path(workspace_root).resolve(strict=True)
    diagnostics = build_result.get("diagnostics")
    if not isinstance(diagnostics, str):
        return None
    error = re.search(
        r"(?m)^(.+?\.cpp):\d+(?::\d+)?: error: definition of explicitly-defaulted "
        r"'([A-Za-z_]\w*)::\2\(\)'",
        diagnostics,
    )
    if error is None:
        return None
    raw = Path(error.group(1).strip())
    source = _safe_source_path(root, raw if raw.is_absolute() else root / raw)
    if source is None or source.stat().st_size > _MAX_SOURCE_BYTES:
        return None
    class_name = error.group(2)
    note = re.search(
        rf"(?m)^(.+?\.(?:h|hpp)):\d+(?::\d+)?: note: .*?"
        rf"{re.escape(class_name)}::{re.escape(class_name)}\(\)' explicitly defaulted here",
        diagnostics,
    )
    if note is None:
        return None
    header_raw = Path(note.group(1).strip())
    header = _safe_source_path(root, header_raw if header_raw.is_absolute() else root / header_raw, _HEADER_SUFFIXES)
    if header is None or header.stat().st_size > _MAX_SOURCE_BYTES:
        return None
    try:
        header_text = header.read_text(encoding="utf-8-sig")
        source_text = source.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None
    if not re.search(rf"\b{re.escape(class_name)}\s*\(\s*\)\s*=\s*default\s*;", header_text):
        return None
    definition = re.compile(
        rf"(?m)^\s*{re.escape(class_name)}::{re.escape(class_name)}\s*\(\s*\)\s*\{{([^{{}}]*)\}}\s*\n?"
    )
    matches = list(definition.finditer(source_text))
    if len(matches) != 1:
        return None
    body = re.sub(r"/\*.*?\*/|//[^\n]*", "", matches[0].group(1), flags=re.DOTALL)
    if body.strip():
        return None
    updated = source_text[:matches[0].start()] + source_text[matches[0].end():]
    _atomic_write(source, updated.encode("utf-8"))
    return DeterministicRepair(
        repair_id="cpp-defaulted-constructor-v1",
        changed_paths=(source.relative_to(root).as_posix(),),
        reason="Compiler found a redundant empty definition of a constructor defaulted in its header.",
    )


def _diagnostic_header_paths(root: Path, diagnostics: str) -> tuple[Path, ...]:
    paths: list[Path] = []
    for line in diagnostics.splitlines():
        match = re.match(
            r"^(.+?\.(?:h|hpp)):\d+(?::\d+)?:\s*error:\s*(.+)$",
            line.strip(), re.IGNORECASE,
        )
        if not match or not re.search(
            r"(?:['`\u2018\u2019](?:uint8_t|int\d+_t|HIGH|LOW|INPUT|OUTPUT|"
            r"Serial|pinMode|digitalRead|digitalWrite|analogRead|analogWrite|delay|millis|micros)"
            r"['`\u2018\u2019]\s*(?:does not name a type|was not declared in this scope))",
            match.group(2), re.IGNORECASE,
        ):
            continue
        raw = Path(match.group(1).strip())
        candidate = raw if raw.is_absolute() else root / raw
        safe = _safe_source_path(root, candidate, _HEADER_SUFFIXES)
        if safe is not None and safe not in paths:
            paths.append(safe)
    return tuple(paths)


def _repair_system_name_collision(
    workspace_root: str | Path, build_result: Mapping[str, object],
) -> DeterministicRepair | None:
    root = Path(workspace_root).resolve(strict=True)
    if not _uses_arduino_framework(root):
        return None
    diagnostics = build_result.get("diagnostics")
    if not isinstance(diagnostics, str) or "previous declaration 'int system(" not in diagnostics:
        return None
    match = re.search(
        r"(?m)^(.+?\.cpp):\d+(?::\d+)?:\s*error:\s*"
        r"'([A-Za-z_][\w:]*) system' redeclared as different kind of symbol",
        diagnostics,
    )
    if match is None:
        return None
    raw = Path(match.group(1).strip())
    path = _safe_source_path(root, raw if raw.is_absolute() else root / raw)
    if path is None or path.stat().st_size > _MAX_SOURCE_BYTES:
        return None
    try:
        source = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None
    class_name = match.group(2)
    declaration = re.compile(rf"(?m)^(\s*{re.escape(class_name)}\s+)system(\s*;\s*)$")
    if len(declaration.findall(source)) != 1:
        return None
    replacement = class_name.rsplit("::", 1)[-1]
    replacement = replacement[0].lower() + replacement[1:] + "Instance"
    if re.search(rf"\b{re.escape(replacement)}\b", source):
        return None
    rewritten = declaration.sub(rf"\g<1>{replacement}\g<2>", source)
    rewritten = re.sub(r"\bsystem(?=\s*(?:\.|->))", replacement, rewritten)
    if re.search(r"\bsystem\b", rewritten) or rewritten == source:
        return None
    _atomic_write(path, rewritten.encode("utf-8"))
    return DeterministicRepair(
        repair_id="cpp-system-name-collision-v1",
        changed_paths=(path.relative_to(root).as_posix(),),
        reason="Compiler identified a global system variable that collides with the C library system function.",
    )


def _uses_arduino_framework(root: Path) -> bool:
    path = root / "platformio.ini"
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 256_000:
        return False
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return False
    return bool(
        re.search(
            r"^\s*framework\s*=\s*[^;#\r\n]*\barduino\b",
            text,
            flags=re.IGNORECASE | re.MULTILINE,
        )
    )


def _diagnostic_cpp_paths(root: Path, diagnostics: str) -> tuple[Path, ...]:
    found: list[Path] = []
    seen: set[str] = set()
    for line in diagnostics.splitlines():
        match = re.match(r"^(.+?\.(?:cpp|cc|cxx)):\d+(?::\d+)?:", line.strip(), re.IGNORECASE)
        if not match:
            continue
        raw = Path(match.group(1).strip())
        candidate = raw if raw.is_absolute() else root / raw
        safe = _safe_source_path(root, candidate)
        if safe is None:
            continue
        key = os.path.normcase(str(safe))
        if key not in seen:
            seen.add(key)
            found.append(safe)
    return tuple(found)


def _safe_source_path(
    root: Path, candidate: Path, suffixes: frozenset[str] = _CPP_SUFFIXES,
) -> Path | None:
    try:
        if candidate.is_symlink() or not candidate.is_file():
            return None
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    if resolved.suffix.casefold() not in suffixes:
        return None
    return resolved


def _atomic_write(path: Path, content: bytes) -> None:
    mode = path.stat().st_mode
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
