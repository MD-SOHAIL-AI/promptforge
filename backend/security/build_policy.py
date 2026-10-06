"""Fail closed before running project-controlled PlatformIO configuration."""
from configparser import RawConfigParser
import re


_REGISTRY_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*(?:@[\^~<>=!0-9A-Za-z.*,+-]+)?\Z")
_REGISTRY_LIBRARY = re.compile(
    r"(?:[A-Za-z0-9][A-Za-z0-9_.-]*/)?[A-Za-z0-9][A-Za-z0-9_. -]*"
    r"(?:\s*@\s*[\^~<>=!0-9A-Za-z.*,+ -]+)?\Z"
)


def _registry_only(section: str, key: str, value: str) -> None:
    if key in {"platform", "board"}:
        valid = bool(_REGISTRY_NAME.fullmatch(value.strip()))
    else:
        # PlatformIO accepts one registry dependency per line. URL, VCS and
        # local path forms must never reach a build in a model-writable stage.
        valid = all(
            bool(_REGISTRY_LIBRARY.fullmatch(item.strip()))
            for item in value.splitlines()
            if item.strip()
        )
    if not valid:
        raise ValueError(f"BUILD_CONFIGURATION_REQUIRES_TRUST: {section}.{key}")


def validate_build_configuration(parser: RawConfigParser) -> None:
    # Includes DEFAULT, base env, and inheritance source sections. Indirection is
    # rejected because validating only the local INI cannot establish its trust.
    forbidden = {"extra_scripts", "extra_configs", "platform_packages", "upload_command",
                 "lib_extra_dirs"}
    for section in [parser.default_section, *parser.sections()]:
        options = parser.defaults() if section == parser.default_section else dict(parser.items(section, raw=True))
        for key, value in options.items():
            if not value.strip():
                continue
            if key.casefold() in forbidden:
                raise ValueError(f"BUILD_CONFIGURATION_REQUIRES_TRUST: {section}.{key}")
            if key.casefold() in {"platform", "board", "lib_deps"}:
                _registry_only(section, key.casefold(), value)
            if key.casefold() in {"build_flags", "build_unflags"}:
                # Compiler plugins, response files and external include paths
                # can read or execute outside the model-writable project stage.
                if re.search(r"(?:-fplugin|@|(?:^|\s)-(?:I|L|include|imacros|specs|B)|[A-Za-z]:[\\/]|\.\.[\\/])", value):
                    raise ValueError(f"BUILD_CONFIGURATION_REQUIRES_TRUST: {section}.{key}")
            if key.casefold() == "targets":
                targets = value.replace(",", " ").split()
                if any(target not in {"buildprog", "checkprogsize"} for target in targets):
                    raise ValueError(f"BUILD_TARGET_FORBIDDEN: {section}.{key}")
