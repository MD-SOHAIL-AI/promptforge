"""Resolve ESP32 partition inputs; validation uses the installed Espressif tool."""
import json
import os
import re
from pathlib import Path


class PartitionPreflightError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def resolve_option(parser, environment, option):
    def resolve(section, key, visited=()):
        identity = (section, key)
        if identity in visited or len(visited) > 32:
            raise PartitionPreflightError("PARTITION_CONFIGURATION_INVALID", "Cyclic configuration inheritance or interpolation")
        if not parser.has_section(section):
            raise PartitionPreflightError("PARTITION_CONFIGURATION_INVALID", f"Undefined section: {section}")
        visited = (*visited, identity)
        if parser.has_option(section, key):
            value = parser.get(section, key, raw=True).strip()
        else:
            value = ""
            for parent in reversed(re.split(r"[,\n]+", parser.get(section, "extends", fallback="", raw=True))):
                parent = parent.strip()
                if not parent:
                    continue
                if not parser.has_section(parent) and parser.has_section("env:" + parent):
                    parent = "env:" + parent
                value = resolve(parent, key, visited)
                if value:
                    break
            if not value and section.startswith("env:") and parser.has_section("env"):
                value = resolve("env", key, visited)
        def interpolate(match):
            reference = match.group(1)
            if "." not in reference:
                raise PartitionPreflightError("PARTITION_CONFIGURATION_INVALID", "Unresolved configuration value")
            group, name = reference.split(".", 1)
            if group == "sysenv":
                result = os.getenv(name, "")
            else:
                result = resolve(group, name, visited)
            if not result:
                raise PartitionPreflightError("PARTITION_CONFIGURATION_INVALID", f"Unresolved configuration reference: {reference}")
            return result
        return re.sub(r"\$\{([^}]+)\}", interpolate, value)
    return resolve("env:" + environment, option)


def partition_input(project, parser, environment, core_dir):
    project = Path(project).resolve(strict=True)
    core = Path(core_dir).expanduser().resolve()
    partition = resolve_option(parser, environment, "board_build.partitions")
    if not partition:
        # Default tables are resolved and verified by the canonical PlatformIO build.
        return None
    platform = resolve_option(parser, environment, "platform")
    if "espressif32" not in platform.casefold():
        return None
    framework = resolve_option(parser, environment, "framework")
    board = resolve_option(parser, environment, "board")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", board):
        raise PartitionPreflightError("PARTITION_CONFIGURATION_INVALID", "Invalid board identity")
    candidate = project / partition
    lexical = Path(partition)
    if lexical.is_absolute() or ".." in lexical.parts or ":" in partition or "\\" in partition:
        raise PartitionPreflightError("PARTITION_PATH_UNSAFE", "Partition path must be relative to the project")
    source = candidate
    packages = core / "packages"
    if not source.is_file() and len(lexical.parts) == 1:
        roots = [packages / "framework-arduinoespressif32/tools/partitions",
                 packages / "framework-espidf/components/partition_table"]
        source = next((root / partition for root in roots if (root / partition).is_file()), candidate)
    if not source.is_file():
        raise PartitionPreflightError("PARTITION_TABLE_MISSING", f"Partition table does not exist: {partition}")
    current = source.absolute()
    while current != current.parent:
        if current.is_symlink() or getattr(current.lstat(), "st_file_attributes", 0) & 0x400:
            raise PartitionPreflightError("PARTITION_PATH_UNSAFE", "Partition path crosses a link or junction")
        current = current.parent
    if source.suffix.lower() != ".csv" or source.stat().st_size > 256 * 1024:
        raise PartitionPreflightError("PARTITION_TABLE_INVALID", "Partition tables must be bounded CSV files")
    flash = resolve_option(parser, environment, "board_upload.flash_size")
    if not flash:
        board_path = project / "boards" / (board + ".json")
        if not board_path.is_file():
            board_path = core / "platforms/espressif32/boards" / (board + ".json")
        try:
            flash = json.loads(board_path.read_text(encoding="utf-8"))["upload"]["flash_size"]
        except (OSError, ValueError, KeyError):
            raise PartitionPreflightError("PARTITION_FLASH_SIZE_UNKNOWN", "Install the selected board platform or configure board_upload.flash_size") from None
    flash = str(flash).upper()
    if flash not in {f"{size}MB" for size in (1, 2, 4, 8, 16, 32, 64, 128)}:
        raise PartitionPreflightError("PARTITION_FLASH_SIZE_UNKNOWN", "Unsupported flash capacity")
    tools = [packages / "framework-espidf/components/partition_table/gen_esp32part.py",
             packages / "framework-arduinoespressif32/tools/gen_esp32part.py"] if "espidf" in framework else [packages / "framework-arduinoespressif32/tools/gen_esp32part.py"]
    tool = next((tool for tool in tools if tool.is_file()), None)
    if tool is None:
        raise PartitionPreflightError("PARTITION_TOOL_UNAVAILABLE", "Install the selected ESP32 framework to validate its partition table")
    offset = "0x8000"
    if "espidf" in framework:
        for name in ("sdkconfig." + environment, "sdkconfig", "sdkconfig.defaults"):
            path = project / name
            if path.is_file():
                match = re.search(r"^CONFIG_PARTITION_TABLE_OFFSET=(0x[0-9a-fA-F]+|\d+)\s*$", path.read_text(encoding="utf-8"), re.M)
                if match:
                    offset = match.group(1)
                    break
    return {"source": str(source), "tool": str(tool), "flash_size": flash, "offset": offset, "environment": environment}
