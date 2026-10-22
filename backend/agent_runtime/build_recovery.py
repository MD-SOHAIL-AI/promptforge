"""Keep actionable build blockers current across model context compaction."""
import hashlib
import re
from pathlib import PurePosixPath


def source_repair_focus(build, paths):
    if build.get("error_code") != "LOCAL_SOURCES_INCOMPLETE":
        return None
    directory = build.get("source_directory")
    if (not isinstance(directory, str) or not directory or PurePosixPath(directory).is_absolute()
            or ".." in PurePosixPath(directory).parts or "\\" in directory or ":" in directory):
        return None
    if any(str(path).startswith(directory + "/") for path in paths):
        return None
    return {key: build[key] for key in ("error_code", "source_directory", "instruction", "execution_attempted")
        if key in build}


def compiler_repair_focus(build, paths, read_file, *, declarations=()):
    """Pin actual compiler errors with current callers and paired declarations.

    This is evidence for the model, not a C++ parser or permission to edit.
    Only paths in the host's contained source inventory may be read.
    """
    if build.get("success"):
        return None
    known = {path for path in paths if isinstance(path, str)
        and not PurePosixPath(path).is_absolute() and ".." not in PurePosixPath(path).parts
        and "\\" not in path and ":" not in path and not any(part.startswith(".") for part in PurePosixPath(path).parts)}
    diagnostics, locations, owners = [], {}, set()
    for item in build.get("compiler_diagnostics") or ():
        if not isinstance(item, dict) or item.get("severity") not in {"error", "fatal error"}:
            continue
        path, line = item.get("path"), item.get("line")
        if path not in known or not isinstance(line, int) or isinstance(line, bool) or line < 1:
            continue
        message = str(item.get("message") or "")[:400]
        diagnostic = {"path": path, "line": line, "message": message}
        if diagnostic in diagnostics:
            continue
        diagnostics.append(diagnostic)
        locations.setdefault(path, line)
        owners.add(PurePosixPath(path).stem)
        owners.update(re.findall(r"\b(?:class|struct)\s+([A-Za-z_]\w*)", message))
        owners.update(re.findall(r"\b([A-Za-z_]\w*)::", message))
        for quoted in re.findall(r"'([^'\n]+)'", message):
            owners.update(re.findall(r"\b[A-Za-z_]\w*\b", quoted))
        if len(diagnostics) == 8:
            break
    if not diagnostics:
        return None
    # Call sites explain the error; the matching header and implementation
    # establish the API that actually exists. Do not invent one from prose.
    from .build_readiness import _code
    declared_paths = set()
    for item in declarations:
        path, line = item.get("path"), item.get("line")
        if (path not in known or PurePosixPath(path).suffix not in {".h", ".hpp"}
                or not isinstance(line, int) or isinstance(line, bool) or line < 1):
            continue
        declared = set(re.findall(r"\b(?:class|struct|enum(?:\s+(?:class|struct))?)\s+([A-Za-z_]\w*)",
            _code(str(item.get("declaration") or ""))))
        if declared & owners:
            declared_paths.add(path)
            locations.setdefault(path, line)
    related = sorted(declared_paths | {path for path in known if PurePosixPath(path).stem in owners
        and PurePosixPath(path).suffix in {".h", ".hpp", ".cpp", ".cc"}})
    candidates = list(locations)[:2] + related + list(locations)[2:]
    sources, seen = [], set()
    remaining = 6500
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        try:
            text = read_file(path)
        except (OSError, ValueError):
            continue
        lines = text.splitlines()
        center = min(locations.get(path, 1), max(1, len(lines)))
        start = max(1, center - 4)
        preview_lines = 50 if PurePosixPath(path).suffix in {".h", ".hpp"} else 19
        selected = lines[start - 1:start - 1 + preview_lines]
        content = "\n".join(f"{start + offset}: {line[:240]}" for offset, line in enumerate(selected))
        if len(content) > min(1600, remaining):
            content = content[:min(1600, remaining)]
        sources.append({"path": path, "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "start_line": start, "numbered_preview": content, "preview_only": True})
        remaining -= len(content)
        if len(sources) == 6 or remaining < 400:
            break
    return {"build_id": build.get("build_id"), "build_source_revision": build.get("workspace_revision"),
        "diagnostics_stale": bool(build.get("verification_stale")), "diagnostics": diagnostics,
        "current_sources": sources,
        "instruction": "Repair these compiler-reported callers against the current declared interfaces. "
            "Read complete affected files before editing; previews are not complete file or citation evidence. "
            "Keep related header, implementation and callers consistent, preserve required behavior, "
            "and rebuild the changed source before claiming the error is resolved."}


class ConfigurationRepairRequired(ValueError):
    code = "BUILD_CONFIGURATION_REPAIR_REQUIRED"

    def __init__(self, focus):
        super().__init__(focus["instruction"])
        self.details = focus


def dependency_repair_focus(build, read_file):
    configuration_error = build.get("error_code") in {"BUILD_CONFIGURATION_MISSING", "BUILD_CONFIGURATION_INVALID",
        "BUILD_CONFIGURATION_REQUIRES_TRUST"}
    if not configuration_error and build.get("error_code") != "DEPENDENCY_UNRESOLVED":
        return None
    dependencies = build if configuration_error else build.get("dependencies") or {}
    fingerprint = dependencies.get("configuration_sha256")
    if not fingerprint:
        return None
    try:
        text = read_file("platformio.ini").lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
        current = hashlib.sha256(text.encode("utf-8")).hexdigest()
    except UnicodeError:
        current = "unreadable"
    except (OSError, ValueError):
        current = "missing"
    if fingerprint != current:
        return None
    if configuration_error:
        policy_correction = ("The build policy remains enforced. Remove unnecessary blocked settings; "
            "PlatformIO includes the standard include/ directory automatically, so it does not need an -I flag. "
            "If required behavior needs a blocked setting, report the blocker rather than bypassing the policy. "
            if build["error_code"] == "BUILD_CONFIGURATION_REQUIRES_TRUST" else "")
        return {"error_code": build["error_code"], "configuration_sha256": current,
                "repair_paths": ["platformio.ini"], "instruction":
                "Create or repair platformio.ini before changing other source files. "
                + policy_correction +
                "Preserve the requested board, framework and environment; ask for a target decision if unresolved. "
                "Inspect the saved source and revalidate configuration before rebuilding.",
                "correction": build.get("correction"), "message": build.get("message")}
    return {"error_code": "DEPENDENCY_UNRESOLVED", "configuration_sha256": current,
            "repair_paths": ["platformio.ini"], "missing": dependencies.get("missing", [])[:32],
            "instruction": "Repair these dependency declarations before rewriting source modules. "
                "Remove framework-provided facilities from lib_deps; inspect registry candidates for external packages. "
                "Read platformio.ini and preserve the selected environment and requested hardware."}
