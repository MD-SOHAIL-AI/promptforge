"""Bounded project and compiler facts for a selected embedded environment."""
from __future__ import annotations

import configparser
import json
import os
import shlex
from pathlib import Path

from pydantic import BaseModel, Field

from .platformio_service import _discover_from_parser, _resolve_option


class EnvironmentProfile(BaseModel):
    name: str
    board: str
    framework: str
    platform: str
    dependencies: list[str] = Field(default_factory=list)
    build_flags: str = ""


class CompilationUnit(BaseModel):
    path: str
    compiler: str
    arguments: list[str]
    includes: list[str]
    defines: list[str]


class ProjectProfile(BaseModel):
    schema_version: int = 1
    adapter: str
    selected_environment: str | None = None
    selection_required: bool = False
    environments: list[EnvironmentProfile] = Field(default_factory=list)
    compilation_units: list[CompilationUnit] = Field(default_factory=list)
    compiler_context_available: bool = False
    warnings: list[str] = Field(default_factory=list)
    configuration_files: list[str] = Field(default_factory=list)
    documentation: list[dict[str, str]] = Field(default_factory=list)
    qualification: str = "unqualified"


def inspect_project_profile(project, *, environment=None):
    root = Path(project).resolve(strict=True)
    warnings = []
    ini = root / "platformio.ini"
    profiles = []
    selected = environment
    if ini.is_file() and not ini.is_symlink():
        if ini.stat().st_size > 128_000:
            raise ValueError("PROJECT_CONFIGURATION_TOO_LARGE")
        parser = configparser.RawConfigParser()
        parser.read(ini, encoding="utf-8")
        for item in _discover_from_parser(parser):
            profiles.append(EnvironmentProfile(**item.to_dict(),
                dependencies=[line.strip() for line in _resolve_option(parser, item.name, "lib_deps").splitlines() if line.strip()],
                build_flags=_resolve_option(parser, item.name, "build_flags")))
        if selected and selected not in {item.name for item in profiles}:
            raise ValueError("PROJECT_ENVIRONMENT_NOT_FOUND")
        if selected is None:
            defaults = parser.get("platformio", "default_envs", fallback="", raw=True).replace(",", " ").split()
            if len(defaults) == 1 and defaults[0] in {item.name for item in profiles}:
                selected = defaults[0]
            elif len(profiles) == 1:
                selected = profiles[0].name
        adapter, files = "platformio", ["platformio.ini"]
    elif (root / "CMakeLists.txt").is_file() and (root / "sdkconfig").is_file():
        adapter = "esp-idf"
        files = [name for name in ("CMakeLists.txt", "sdkconfig", "sdkconfig.defaults", "partitions.csv", "dependencies.lock") if (root / name).is_file()]
        warnings.append("Native ESP-IDF integration is experimental and requires a separately configured IDF toolchain.")
    else:
        adapter, files = "unknown", []
        warnings.append("No supported project configuration was found.")
    if len(profiles) > 1 and selected is None:
        warnings.append("Select an environment before inferring board, framework or compiler facts.")
    units = []
    candidates = [root / ".pio/build" / selected / "compile_commands.json"] if selected else []
    if selected or adapter == "esp-idf":
        candidates.extend([root / "compile_commands.json", root / "build/compile_commands.json"])
    for database in candidates:
        if not database.is_file() or database.is_symlink():
            continue
        try:
            database.resolve(strict=True).relative_to(root)
            if database.stat().st_size > 4_000_000:
                raise ValueError("COMPILATION_DATABASE_TOO_LARGE")
            entries = json.loads(database.read_text(encoding="utf-8"))
            if not isinstance(entries, list) or len(entries) > 2000:
                raise ValueError("COMPILATION_DATABASE_INVALID")
            scoped = database.parent == root / ".pio/build" / str(selected)
            for entry in entries:
                directory = Path(entry["directory"])
                if not directory.is_absolute():
                    directory = root / directory
                path = Path(entry["file"])
                if not path.is_absolute():
                    path = directory / path
                if path.is_symlink():
                    continue
                try:
                    relative = path.resolve(strict=True).relative_to(root).as_posix()
                except (OSError, ValueError):
                    continue
                if any(part in {".pio", "build"} for part in Path(relative).parts):
                    continue
                arguments = entry.get("arguments")
                if arguments is None:
                    arguments = shlex.split(entry["command"], posix=os.name != "nt")
                    arguments = [arg[1:-1] if len(arg) >= 2 and arg[0] == arg[-1] == '"' else arg for arg in arguments]
                if not isinstance(arguments, list) or not arguments or len(arguments) > 512 or any(not isinstance(arg, str) or len(arg) > 4096 for arg in arguments):
                    raise ValueError("COMPILATION_COMMAND_INVALID")
                # A root database may contain commands for several environments.
                normalized = " ".join(arguments).replace("\\", "/")
                if adapter == "platformio" and len(profiles) > 1 and not scoped and ".pio/build/" + str(selected) + "/" not in normalized:
                    continue
                includes, defines = [], []
                for index, arg in enumerate(arguments):
                    if arg in {"-I", "-isystem", "-D"} and index + 1 < len(arguments):
                        (defines if arg == "-D" else includes).append(arguments[index + 1])
                    elif arg.startswith("-I") and len(arg) > 2:
                        includes.append(arg[2:])
                    elif arg.startswith("-D") and len(arg) > 2:
                        defines.append(arg[2:])
                units.append(CompilationUnit(path=relative, compiler=arguments[0], arguments=arguments,
                                             includes=includes, defines=defines))
            break
        except (OSError, ValueError, KeyError, TypeError) as exc:
            warnings.append("Compiler context unavailable: " + str(exc)[:160])
            units = []
    if not units:
        warnings.append("No current compilation database; symbol and hardware extraction are lexical hints.")
    docs = [{"title": "PlatformIO compilation database", "url": "https://docs.platformio.org/en/latest/integration/compile_commands.html", "version": "toolchain-dependent"}]
    current = next((item for item in profiles if item.name == selected), None)
    if current and "arduino" in current.framework.lower():
        docs.append({"title": "Arduino ESP32", "url": "https://docs.espressif.com/projects/arduino-esp32/en/latest/", "version": "unknown; inspect installed framework package before using version-specific APIs"})
    if adapter == "esp-idf":
        docs.append({"title": "ESP-IDF", "url": "https://docs.espressif.com/projects/esp-idf/en/stable/esp32/", "version": "unknown; run idf.py --version"})
    return ProjectProfile(adapter=adapter, selected_environment=selected,
        selection_required=len(profiles) > 1 and selected is None, environments=profiles,
        compilation_units=units, compiler_context_available=bool(units), warnings=warnings,
        configuration_files=files, documentation=docs,
        qualification="candidate" if current and current.board == "esp32dev" and current.framework == "arduino" else "experimental" if adapter == "esp-idf" else "unqualified")
