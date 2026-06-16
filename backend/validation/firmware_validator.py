"""Pure structural validation for generated firmware projects.

The validator operates only on in-memory project data.  It performs no builds,
filesystem access, subprocess execution, network calls, or hardware I/O.
Validation failures are returned as structured issues rather than exceptions.

Example::

    result = FirmwareValidator().validate_project(
        {
            "framework": "PlatformIO",
            "files": [
                {
                    "path": "platformio.ini",
                    "content": "[env:uno]\nplatform=atmelavr\nboard=uno\nframework=arduino",
                },
                {
                    "path": "src/main.cpp",
                    "content": "void setup() {}\nvoid loop() {}",
                },
            ],
        }
    )
    assert result.valid
"""

from __future__ import annotations

import configparser
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum, unique
from io import StringIO
from types import MappingProxyType
from typing import ClassVar

__all__ = [
    "FirmwareValidationCategory",
    "FirmwareValidationIssue",
    "FirmwareValidationResult",
    "FirmwareValidationSeverity",
    "FirmwareValidator",
]


@unique
class FirmwareValidationSeverity(str, Enum):
    """Severity of a firmware project validation finding."""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


@unique
class FirmwareValidationCategory(str, Enum):
    """Structural domain associated with a firmware issue."""

    PROJECT = "PROJECT"
    STRUCTURE = "STRUCTURE"
    FRAMEWORK = "FRAMEWORK"
    CONFIGURATION = "CONFIGURATION"
    DEPENDENCY = "DEPENDENCY"
    SOURCE = "SOURCE"
    SECURITY = "SECURITY"


@dataclass(frozen=True, slots=True)
class FirmwareValidationIssue:
    """One deterministic firmware project finding."""

    category: FirmwareValidationCategory
    severity: FirmwareValidationSeverity
    message: str
    recommendation: str
    rule_id: str
    file_path: str | None = None


@dataclass(frozen=True, slots=True)
class FirmwareValidationResult:
    """Immutable aggregate returned by firmware validation methods."""

    valid: bool
    issues: tuple[FirmwareValidationIssue, ...]
    warnings: tuple[FirmwareValidationIssue, ...]
    errors: tuple[FirmwareValidationIssue, ...]

    @classmethod
    def from_issues(
        cls,
        issues: Iterable[FirmwareValidationIssue],
    ) -> FirmwareValidationResult:
        """Build a consistently classified result from ``issues``."""

        collected = tuple(issues)
        warnings = tuple(
            issue
            for issue in collected
            if issue.severity is FirmwareValidationSeverity.WARNING
        )
        errors = tuple(
            issue
            for issue in collected
            if issue.severity is FirmwareValidationSeverity.ERROR
        )
        return cls(
            valid=not errors,
            issues=collected,
            warnings=warnings,
            errors=errors,
        )

    @property
    def is_valid(self) -> bool:
        """Alias for generic validation pipelines."""

        return self.valid

    def has_errors(self) -> bool:
        """Return whether any blocking issue was found."""

        return bool(self.errors)

    def summary(self) -> str:
        """Return a deterministic one-line summary."""

        info_count = sum(
            issue.severity is FirmwareValidationSeverity.INFO
            for issue in self.issues
        )
        state = "VALID" if self.valid else "INVALID"
        return (
            f"{state}: {len(self.issues)} issue(s) "
            f"(info={info_count}, warnings={len(self.warnings)}, "
            f"errors={len(self.errors)})"
        )


@dataclass(frozen=True, slots=True)
class _ProjectFile:
    path: str
    content: str


@dataclass(frozen=True, slots=True)
class _ProjectView:
    framework: str | None
    files: tuple[_ProjectFile, ...]

    @property
    def paths(self) -> Mapping[str, _ProjectFile]:
        first_by_path: dict[str, _ProjectFile] = {}
        for item in self.files:
            first_by_path.setdefault(item.path.casefold(), item)
        return MappingProxyType(first_by_path)


@dataclass(frozen=True, slots=True)
class _IniView:
    parser: configparser.RawConfigParser | None
    issues: tuple[FirmwareValidationIssue, ...]


class FirmwareValidator:
    """Deterministic validation of generated firmware project structure.

    Args:
        supported_libraries: Optional complete allow-list for ``lib_deps``.
            When omitted, a conservative built-in list of common embedded
            libraries is used.  Names are matched case-insensitively after
            removing owner, version, and punctuation components.
        additional_supported_libraries: Libraries to add to the default list.
    """

    SUPPORTED_FRAMEWORKS: ClassVar[frozenset[str]] = frozenset(
        {"platformio", "arduino", "espidf", "stm32cube"}
    )
    DEFAULT_SUPPORTED_LIBRARIES: ClassVar[frozenset[str]] = frozenset(
        {
            "adafruitbusio",
            "adafruitgfxlibrary",
            "adafruitneopixel",
            "adafruitunifiedsensor",
            "arduinojson",
            "asynctcp",
            "dhtsensorlibrary",
            "espasyncwebserver",
            "fastled",
            "liquidcrystal",
            "neopixel",
            "pubsubclient",
            "servo",
            "wifiManager".casefold(),
        }
    )
    _SOURCE_SUFFIXES: ClassVar[tuple[str, ...]] = (
        ".c",
        ".cc",
        ".cpp",
        ".cxx",
        ".ino",
    )
    _HEADER_SUFFIXES: ClassVar[tuple[str, ...]] = (".h", ".hh", ".hpp", ".hxx")
    _DANGEROUS_FLAGS: ClassVar[tuple[tuple[str, re.Pattern[str], str], ...]] = (
        (
            "PF-FW-014",
            re.compile(r"(?:^|\s)-fpermissive(?:\s|$)", re.I),
            "-fpermissive weakens C++ type-safety diagnostics",
        ),
        (
            "PF-FW-015",
            re.compile(r"(?:^|\s)-(?:w|Wno-error)(?:\s|$)"),
            "the compile flags suppress or downgrade compiler diagnostics",
        ),
        (
            "PF-FW-016",
            re.compile(r"(?:^|\s)-fno-stack-protector(?:\s|$)", re.I),
            "-fno-stack-protector disables stack corruption protection",
        ),
        (
            "PF-FW-017",
            re.compile(
                r"(?:^|\s)-Wl,(?:[^\s]*,)?--allow-multiple-definition(?:\s|$)",
                re.I,
            ),
            "the linker is configured to accept duplicate symbol definitions",
        ),
        (
            "PF-FW-018",
            re.compile(r"(?:^|\s)-D\s*NDEBUG(?:=\S+)?(?:\s|$)", re.I),
            "NDEBUG disables runtime assertions",
        ),
    )

    def __init__(
        self,
        supported_libraries: Iterable[str] | None = None,
        additional_supported_libraries: Iterable[str] = (),
    ) -> None:
        base = (
            self.DEFAULT_SUPPORTED_LIBRARIES
            if supported_libraries is None
            else _normalize_library_set(supported_libraries)
        )
        additions = _normalize_library_set(additional_supported_libraries)
        self._supported_libraries = frozenset(base | additions)

    def validate_project(self, project: object) -> FirmwareValidationResult:
        """Run all applicable structural checks for ``project``."""

        view, input_issues = _project_view(project)
        if view is None:
            return FirmwareValidationResult.from_issues(input_issues)

        issues: list[FirmwareValidationIssue] = list(input_issues)
        issues.extend(self.validate_structure(view).issues)
        issues.extend(self.validate_source_files(view).issues)

        framework = _normalize_identifier(view.framework)
        if framework not in self.SUPPORTED_FRAMEWORKS:
            issues.append(
                _issue(
                    FirmwareValidationCategory.FRAMEWORK,
                    "PF-FW-004",
                    f"Framework {view.framework!r} is not supported.",
                    "Select PlatformIO, Arduino, ESP-IDF, or STM32Cube.",
                )
            )
        elif framework == "platformio":
            issues.extend(self.validate_platformio(view).issues)
            issues.extend(self.validate_dependencies(view).issues)
        else:
            issues.extend(_validate_non_platformio_framework(view, framework))

        return FirmwareValidationResult.from_issues(_deduplicate(issues))

    def validate_structure(self, project: object) -> FirmwareValidationResult:
        """Validate project file paths, duplicates, required layout, and empties."""

        view, input_issues = _project_view(project)
        if view is None:
            return FirmwareValidationResult.from_issues(input_issues)
        issues: list[FirmwareValidationIssue] = list(input_issues)
        if not view.files:
            issues.append(
                _issue(
                    FirmwareValidationCategory.STRUCTURE,
                    "PF-FW-001",
                    "Generated project contains no files.",
                    "Generate a complete firmware project before building.",
                )
            )
            return FirmwareValidationResult.from_issues(_deduplicate(issues))

        seen: set[str] = set()
        for item in view.files:
            normalized = item.path.casefold()
            if normalized in seen:
                issues.append(
                    _issue(
                        FirmwareValidationCategory.STRUCTURE,
                        "PF-FW-002",
                        f"Duplicate project file path: {item.path}.",
                        "Keep exactly one generated file for each normalized path.",
                        file_path=item.path,
                    )
                )
            seen.add(normalized)
            if not _is_safe_project_path(item.path):
                issues.append(
                    _issue(
                        FirmwareValidationCategory.STRUCTURE,
                        "PF-FW-003",
                        f"Project file path {item.path!r} is not a safe relative POSIX path.",
                        "Use normalized project-relative paths without parent traversal.",
                        file_path=item.path,
                    )
                )
            if not item.content.strip():
                issues.append(
                    _issue(
                        FirmwareValidationCategory.STRUCTURE,
                        "PF-FW-005",
                        f"Project file {item.path!r} is empty.",
                        "Generate complete file content or remove the file.",
                        file_path=item.path,
                    )
                )

        framework = _normalize_identifier(view.framework)
        required = _required_paths(framework)
        missing = sorted(required - set(view.paths))
        if missing:
            issues.append(
                _issue(
                    FirmwareValidationCategory.STRUCTURE,
                    "PF-FW-006",
                    "Project is missing required files: " + ", ".join(missing) + ".",
                    "Generate every file required by the selected framework.",
                )
            )
        return FirmwareValidationResult.from_issues(_deduplicate(issues))

    def validate_source_files(self, project: object) -> FirmwareValidationResult:
        """Validate source presence and required firmware entry points."""

        view, input_issues = _project_view(project)
        if view is None:
            return FirmwareValidationResult.from_issues(input_issues)
        issues: list[FirmwareValidationIssue] = list(input_issues)
        framework = _normalize_identifier(view.framework)
        sources = tuple(
            item
            for item in view.files
            if item.path.casefold().endswith(self._SOURCE_SUFFIXES)
        )
        if not sources:
            issues.append(
                _issue(
                    FirmwareValidationCategory.SOURCE,
                    "PF-FW-007",
                    "Project contains no firmware source files.",
                    "Add at least one C, C++, or Arduino source file.",
                )
            )
            return FirmwareValidationResult.from_issues(_deduplicate(issues))

        if framework in {"platformio", "arduino"}:
            combined = "\n".join(_strip_comments(item.content) for item in sources)
            if not re.search(r"\bvoid\s+setup\s*\([^)]*\)\s*\{", combined):
                issues.append(
                    _issue(
                        FirmwareValidationCategory.SOURCE,
                        "PF-FW-008",
                        "Arduino firmware does not define setup().",
                        "Define void setup() in a source file.",
                    )
                )
            if not re.search(r"\bvoid\s+loop\s*\([^)]*\)\s*\{", combined):
                issues.append(
                    _issue(
                        FirmwareValidationCategory.SOURCE,
                        "PF-FW-009",
                        "Arduino firmware does not define loop().",
                        "Define void loop() in a source file.",
                    )
                )
        return FirmwareValidationResult.from_issues(_deduplicate(issues))

    def validate_platformio(self, project: object) -> FirmwareValidationResult:
        """Validate PlatformIO required files and strict build configuration."""

        view, input_issues = _project_view(project)
        if view is None:
            return FirmwareValidationResult.from_issues(input_issues)
        issues: list[FirmwareValidationIssue] = list(input_issues)
        paths = view.paths
        missing = sorted({"platformio.ini", "src/main.cpp"} - set(paths))
        if missing:
            issues.append(
                _issue(
                    FirmwareValidationCategory.STRUCTURE,
                    "PF-FW-006",
                    "Project is missing required files: " + ", ".join(missing) + ".",
                    "Generate every file required by the selected framework.",
                )
            )
        ini_file = paths.get("platformio.ini")
        if ini_file is None or not ini_file.content.strip():
            return FirmwareValidationResult.from_issues(_deduplicate(issues))

        ini = _parse_platformio_ini(ini_file.content)
        issues.extend(ini.issues)
        parser = ini.parser
        if parser is None:
            return FirmwareValidationResult.from_issues(_deduplicate(issues))

        environments = tuple(
            section for section in parser.sections() if section.casefold().startswith("env:")
        )
        if not environments:
            issues.append(
                _issue(
                    FirmwareValidationCategory.CONFIGURATION,
                    "PF-FW-011",
                    "platformio.ini defines no [env:<name>] environment.",
                    "Add at least one complete PlatformIO environment.",
                    file_path="platformio.ini",
                )
            )
        environment_names = {section[4:].strip() for section in environments}
        if parser.has_option("platformio", "default_envs"):
            default_envs = tuple(
                item
                for item in re.split(
                    r"[\s,]+",
                    parser.get("platformio", "default_envs", raw=True).strip(),
                )
                if item
            )
            unknown = sorted(set(default_envs) - environment_names)
            if len(default_envs) != len(set(default_envs)) or unknown:
                detail = (
                    "unknown environments: " + ", ".join(unknown)
                    if unknown
                    else "duplicate environment names"
                )
                issues.append(
                    _issue(
                        FirmwareValidationCategory.CONFIGURATION,
                        "PF-FW-025",
                        f"platformio.default_envs contains {detail}.",
                        "Reference each defined build environment at most once.",
                        file_path="platformio.ini",
                    )
                )
        for section in environments:
            environment_name = section[4:].strip()
            if not environment_name:
                issues.append(
                    _issue(
                        FirmwareValidationCategory.CONFIGURATION,
                        "PF-FW-011",
                        "platformio.ini contains an environment with an empty name.",
                        "Name every environment after [env:].",
                        file_path="platformio.ini",
                    )
                )
            extends_error = _validate_extends_chain(parser, section)
            if extends_error is not None:
                issues.append(
                    _issue(
                        FirmwareValidationCategory.CONFIGURATION,
                        "PF-FW-026",
                        extends_error,
                        "Use defined parent environments and remove inheritance cycles.",
                        file_path="platformio.ini",
                    )
                )
            for option in ("platform", "board", "framework"):
                value = _resolve_ini_option(parser, section, option)
                if not value:
                    issues.append(
                        _issue(
                            FirmwareValidationCategory.CONFIGURATION,
                            "PF-FW-012",
                            f"PlatformIO environment {section!r} does not define {option}.",
                            f"Set {option} for every build environment.",
                            file_path="platformio.ini",
                        )
                    )
            configured_framework = _resolve_ini_option(parser, section, "framework")
            if configured_framework and "arduino" not in {
                part.casefold() for part in re.split(r"[\s,]+", configured_framework) if part
            }:
                issues.append(
                    _issue(
                        FirmwareValidationCategory.FRAMEWORK,
                        "PF-FW-013",
                        (
                            f"PlatformIO environment {section!r} uses framework "
                            f"{configured_framework!r}; setup()/loop() validation expects Arduino."
                        ),
                        "Use framework = arduino or select the matching project framework.",
                        file_path="platformio.ini",
                    )
                )

            flags = _resolve_ini_option(parser, section, "build_flags")
            issues.extend(_dangerous_flag_issues(flags, section))
        return FirmwareValidationResult.from_issues(_deduplicate(issues))

    def validate_dependencies(self, project: object) -> FirmwareValidationResult:
        """Validate PlatformIO dependencies against the configured library policy."""

        view, input_issues = _project_view(project)
        if view is None:
            return FirmwareValidationResult.from_issues(input_issues)
        issues: list[FirmwareValidationIssue] = list(input_issues)
        ini_file = view.paths.get("platformio.ini")
        if ini_file is None or not ini_file.content.strip():
            return FirmwareValidationResult.from_issues(_deduplicate(issues))
        ini = _parse_platformio_ini(ini_file.content)
        issues.extend(ini.issues)
        if ini.parser is None:
            return FirmwareValidationResult.from_issues(_deduplicate(issues))

        for section in ini.parser.sections():
            if not section.casefold().startswith("env:"):
                continue
            seen: set[str] = set()
            dependencies = _dependency_lines(
                _resolve_ini_option(ini.parser, section, "lib_deps")
            )
            for dependency in dependencies:
                normalized = _normalize_library_name(dependency)
                if not normalized:
                    issues.append(
                        _issue(
                            FirmwareValidationCategory.DEPENDENCY,
                            "PF-FW-019",
                            f"Dependency {dependency!r} cannot be resolved to a library name.",
                            "Use a named, versioned PlatformIO registry dependency.",
                            file_path="platformio.ini",
                        )
                    )
                    continue
                if normalized in seen:
                    issues.append(
                        _issue(
                            FirmwareValidationCategory.DEPENDENCY,
                            "PF-FW-020",
                            f"Dependency {dependency!r} is configured more than once.",
                            "Keep one lib_deps entry for each library.",
                            file_path="platformio.ini",
                        )
                    )
                seen.add(normalized)
                if normalized not in self._supported_libraries:
                    issues.append(
                        _issue(
                            FirmwareValidationCategory.DEPENDENCY,
                            "PF-FW-021",
                            f"Library {dependency!r} is not supported by PromptForge.",
                            "Use an approved library or add it to the reviewed library policy.",
                            file_path="platformio.ini",
                        )
                    )
        return FirmwareValidationResult.from_issues(_deduplicate(issues))


def _project_view(
    project: object,
) -> tuple[_ProjectView | None, tuple[FirmwareValidationIssue, ...]]:
    if isinstance(project, _ProjectView):
        return project, ()
    if project is None:
        return None, (_input_issue("Generated firmware project is absent."),)

    framework = _as_text(_get_field(project, "framework"))
    raw_files = _get_field(project, "files")
    if framework is None:
        framework = _as_text(_get_nested_field(project, "framework"))
    if raw_files is None:
        return None, (_input_issue("Project files metadata is absent."),)

    files: list[_ProjectFile] = []
    issues: list[FirmwareValidationIssue] = []
    if isinstance(raw_files, Mapping):
        entries: tuple[object, ...] = tuple(
            {"path": path, "content": content}
            for path, content in raw_files.items()
        )
    elif isinstance(raw_files, Sequence) and not isinstance(
        raw_files, (str, bytes, bytearray)
    ):
        entries = tuple(raw_files)
    else:
        return None, (_input_issue("Project files must be a sequence or mapping."),)

    for index, entry in enumerate(entries):
        path = _as_text(_get_field(entry, "path"))
        content = _get_field(entry, "content")
        if path is None or not isinstance(content, str):
            issues.append(
                _input_issue(
                    f"Project file at index {index} must define string path and content values."
                )
            )
            continue
        files.append(_ProjectFile(path=path, content=content))
    if framework is None:
        issues.append(_input_issue("Project framework metadata is absent."))
    return _ProjectView(framework=framework, files=tuple(files)), tuple(issues)


def _required_paths(framework: str) -> frozenset[str]:
    if framework == "platformio":
        return frozenset({"platformio.ini", "src/main.cpp"})
    if framework == "espidf":
        return frozenset({"cmakelists.txt", "main/cmakelists.txt"})
    if framework == "stm32cube":
        return frozenset({"core/src/main.c", "core/inc/main.h"})
    return frozenset()


def _validate_non_platformio_framework(
    view: _ProjectView,
    framework: str,
) -> tuple[FirmwareValidationIssue, ...]:
    issues: list[FirmwareValidationIssue] = []
    paths = view.paths
    if framework == "arduino":
        sketches = tuple(path for path in paths if path.endswith(".ino"))
        if len(sketches) != 1:
            issues.append(
                _issue(
                    FirmwareValidationCategory.STRUCTURE,
                    "PF-FW-022",
                    "Arduino projects must contain exactly one .ino sketch.",
                    "Generate one primary Arduino sketch.",
                )
            )
    elif framework == "espidf":
        sources = tuple(
            item
            for path, item in paths.items()
            if path.startswith("main/") and path.endswith((".c", ".cc", ".cpp"))
        )
        combined = "\n".join(_strip_comments(item.content) for item in sources)
        if not re.search(r"\b(?:void|int)\s+app_main\s*\([^)]*\)\s*\{", combined):
            issues.append(
                _issue(
                    FirmwareValidationCategory.SOURCE,
                    "PF-FW-023",
                    "ESP-IDF project does not define app_main().",
                    "Define app_main() in a source file under main/.",
                )
            )
    elif framework == "stm32cube":
        main_file = paths.get("core/src/main.c")
        content = _strip_comments(main_file.content) if main_file else ""
        if not re.search(r"\bint\s+main\s*\([^)]*\)\s*\{", content):
            issues.append(
                _issue(
                    FirmwareValidationCategory.SOURCE,
                    "PF-FW-024",
                    "STM32Cube project does not define main().",
                    "Define int main() in Core/Src/main.c.",
                    file_path="Core/Src/main.c",
                )
            )
    return tuple(issues)


def _parse_platformio_ini(content: str) -> _IniView:
    parser = configparser.RawConfigParser(
        strict=True,
        interpolation=None,
        inline_comment_prefixes=(";", "#"),
    )
    parser.optionxform = str.casefold
    try:
        parser.read_file(StringIO(content), source="platformio.ini")
    except configparser.DuplicateSectionError as exc:
        return _IniView(
            None,
            (
                _issue(
                    FirmwareValidationCategory.CONFIGURATION,
                    "PF-FW-010",
                    f"platformio.ini contains duplicate section {exc.section!r}.",
                    "Keep each PlatformIO section exactly once.",
                    file_path="platformio.ini",
                ),
            ),
        )
    except configparser.DuplicateOptionError as exc:
        return _IniView(
            None,
            (
                _issue(
                    FirmwareValidationCategory.CONFIGURATION,
                    "PF-FW-010",
                    (
                        f"platformio.ini contains duplicate option {exc.option!r} "
                        f"in section {exc.section!r}."
                    ),
                    "Define each option once per PlatformIO section.",
                    file_path="platformio.ini",
                ),
            ),
        )
    except configparser.Error as exc:
        return _IniView(
            None,
            (
                _issue(
                    FirmwareValidationCategory.CONFIGURATION,
                    "PF-FW-010",
                    f"platformio.ini is malformed: {exc}.",
                    "Provide valid strict INI syntax.",
                    file_path="platformio.ini",
                ),
            ),
        )
    return _IniView(parser, ())


def _resolve_ini_option(
    parser: configparser.RawConfigParser,
    section: str,
    option: str,
    visited: frozenset[str] = frozenset(),
) -> str:
    if section in visited:
        return ""
    if parser.has_option(section, option):
        return parser.get(section, option, raw=True).strip()
    if parser.has_option(section, "extends"):
        parents = parser.get(section, "extends", raw=True)
        for parent in re.split(r"[\s,]+", parents):
            if not parent:
                continue
            parent_section = parent if parent.casefold().startswith("env:") else f"env:{parent}"
            if parser.has_section(parent_section):
                value = _resolve_ini_option(
                    parser,
                    parent_section,
                    option,
                    visited | {section},
                )
                if value:
                    return value
    if section != "env" and parser.has_option("env", option):
        return parser.get("env", option, raw=True).strip()
    return ""


def _validate_extends_chain(
    parser: configparser.RawConfigParser,
    section: str,
    stack: tuple[str, ...] = (),
) -> str | None:
    if section in stack:
        chain = " -> ".join((*stack, section))
        return f"PlatformIO environment inheritance is cyclic: {chain}."
    if not parser.has_option(section, "extends"):
        return None
    parents = tuple(
        item
        for item in re.split(
            r"[\s,]+",
            parser.get(section, "extends", raw=True).strip(),
        )
        if item
    )
    if not parents:
        return f"PlatformIO environment {section!r} has an empty extends option."
    for parent in parents:
        parent_section = (
            parent if parent.casefold().startswith("env:") else f"env:{parent}"
        )
        if not parser.has_section(parent_section):
            return (
                f"PlatformIO environment {section!r} extends undefined "
                f"environment {parent_section!r}."
            )
        error = _validate_extends_chain(parser, parent_section, (*stack, section))
        if error is not None:
            return error
    return None


def _dependency_lines(value: str) -> tuple[str, ...]:
    lines: list[str] = []
    for raw_line in value.splitlines():
        line = raw_line.strip().rstrip(",")
        if not line or line.startswith((";", "#")):
            continue
        lines.append(line)
    return tuple(lines)


def _normalize_library_name(dependency: str) -> str:
    value = dependency.strip()
    if not value or re.match(r"^(?:https?|git|file)\+?://", value, re.I):
        return ""
    value = re.split(r"\s*@\s*|\s*=\s*|\s+\^?~?[<>=]", value, maxsplit=1)[0]
    if "/" in value:
        value = value.rsplit("/", 1)[-1]
    return _normalize_identifier(value)


def _normalize_library_set(values: Iterable[str]) -> frozenset[str]:
    if isinstance(values, str):
        values = (values,)
    normalized: set[str] = set()
    try:
        entries = tuple(values)
    except TypeError:
        return frozenset()
    for value in entries:
        if not isinstance(value, str):
            continue
        name = _normalize_library_name(value)
        if name:
            normalized.add(name)
    return frozenset(normalized)


def _dangerous_flag_issues(
    flags: str,
    section: str,
) -> tuple[FirmwareValidationIssue, ...]:
    issues: list[FirmwareValidationIssue] = []
    flattened = " ".join(line.strip() for line in flags.splitlines())
    for rule_id, pattern, reason in FirmwareValidator._DANGEROUS_FLAGS:
        if pattern.search(flattened):
            issues.append(
                _issue(
                    FirmwareValidationCategory.SECURITY,
                    rule_id,
                    f"PlatformIO environment {section!r} uses dangerous compile flags: {reason}.",
                    "Remove the flag and fix the underlying warning or safety defect.",
                    file_path="platformio.ini",
                )
            )
    return tuple(issues)


def _strip_comments(content: str) -> str:
    without_blocks = re.sub(r"/\*.*?\*/", " ", content, flags=re.S)
    without_lines = re.sub(r"//[^\r\n]*", " ", without_blocks)
    return re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', " ", without_lines)


def _is_safe_project_path(path: str) -> bool:
    if not path or path != path.strip() or "\x00" in path or "\\" in path:
        return False
    if path.startswith("/") or re.match(r"^[A-Za-z]:", path):
        return False
    parts = path.split("/")
    return all(part not in {"", ".", ".."} for part in parts)


def _normalize_identifier(value: object) -> str:
    text = _as_text(value)
    if text is None:
        return ""
    return re.sub(r"[^a-z0-9]+", "", text.casefold())


def _as_text(value: object) -> str | None:
    if isinstance(value, Enum):
        value = value.value
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _get_field(source: object, field: str) -> object | None:
    if isinstance(source, Mapping):
        return source.get(field)
    try:
        return getattr(source, field, None)
    except Exception:
        return None


def _get_nested_field(source: object, field: str) -> object | None:
    metadata = _get_field(source, "metadata")
    return _get_field(metadata, field) if metadata is not None else None


def _input_issue(message: str) -> FirmwareValidationIssue:
    return _issue(
        FirmwareValidationCategory.PROJECT,
        "PF-FW-000",
        message,
        "Provide complete structured generated-project metadata.",
    )


def _issue(
    category: FirmwareValidationCategory,
    rule_id: str,
    message: str,
    recommendation: str,
    *,
    severity: FirmwareValidationSeverity = FirmwareValidationSeverity.ERROR,
    file_path: str | None = None,
) -> FirmwareValidationIssue:
    return FirmwareValidationIssue(
        category=category,
        severity=severity,
        message=message,
        recommendation=recommendation,
        rule_id=rule_id,
        file_path=file_path,
    )


def _deduplicate(
    issues: Iterable[FirmwareValidationIssue],
) -> tuple[FirmwareValidationIssue, ...]:
    seen: set[tuple[str, str, str | None]] = set()
    result: list[FirmwareValidationIssue] = []
    for issue in issues:
        key = (issue.rule_id, issue.message, issue.file_path)
        if key not in seen:
            seen.add(key)
            result.append(issue)
    return tuple(result)
