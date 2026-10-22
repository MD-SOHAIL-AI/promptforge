"""Bounded compiler/linker facts. Logs are evidence, never edit authority."""
from __future__ import annotations

import re
from pathlib import Path, PurePosixPath, PureWindowsPath


_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_COMPILER = re.compile(r"^(.+?):(\d+)(?::(\d+))?:\s*(warning|error|fatal error):\s*(.+)$")
_LINKER = re.compile(r"(?P<kind>undefined reference to|multiple definition of|undefined symbol:)\s*[`'\"]?(?P<symbol>[^\r\n]+)")
_SOURCE = re.compile(r"(?P<path>(?:[A-Za-z]:)?[^\s:()]+\.(?:cpp|cc|cxx|c|ino))(?::(?P<line>\d+))?")


def contained_diagnostic_path(value, root):
    """Normalize only locations inside the project, including Windows paths."""
    value = str(value).strip().strip('"').replace("\\", "/")
    if not value or "\x00" in value:
        return None
    # Never reinterpret Windows absolute compiler paths as project-relative
    # when the backend runs on POSIX (or vice versa).
    if PureWindowsPath(value).is_absolute() and not Path(value).is_absolute():
        return None
    try:
        candidate = Path(value)
        candidate = candidate if candidate.is_absolute() else Path(root) / candidate
        relative = candidate.resolve().relative_to(Path(root).resolve()).as_posix()
        if any(part.startswith(".") for part in PurePosixPath(relative).parts):
            return None
        return relative
    except (OSError, ValueError):
        return None


def parse_build_diagnostics(text, root):
    compiler, linker, seen = [], [], set()
    # Process already bounded subprocess output before reducing the display tail.
    for raw_line in text.splitlines():
        line = _ANSI_ESCAPE.sub("", raw_line)
        match = _COMPILER.match(line)
        if match and len(compiler) < 100:
            compiler.append({"path": contained_diagnostic_path(match[1], root),
                "line": int(match[2]), "column": int(match[3] or 0),
                "severity": match[4], "message": match[5][:1000]})
        match = _LINKER.search(line)
        if not match or len(linker) >= 64:
            continue
        symbol = match["symbol"].strip().rstrip("'`\"").split("';", 1)[0][:400]
        identity = (match["kind"], symbol)
        if identity in seen:
            continue
        seen.add(identity)
        prefix = line[:match.start()].replace("\\", "/")
        locations = list(_SOURCE.finditer(prefix))
        location = locations[-1] if locations else None
        path = contained_diagnostic_path(location["path"], root) if location else None
        # GNU ld often reports only .pio/build/<env>/src/<file>.cpp.o.
        obj = re.search(r"\.pio/build/[^/]+/(.+?\.(?:cpp|cc|cxx|c|ino))\.o", prefix)
        if obj and path is None:
            path = contained_diagnostic_path(obj[1], root)
        linker.append({"stage": "link", "severity": "error", "kind": match["kind"],
            "symbol": symbol, "path": path,
            "line": int(location["line"]) if location and location["line"] else None,
            "message": match["kind"] + " " + symbol})
    return compiler, linker


def linker_repair_focus(build, paths, read_file):
    if build.get("success") or not build.get("linker_diagnostics"):
        return None
    known = sorted(path for path in paths if isinstance(path, str)
        and not PurePosixPath(path).is_absolute() and ".." not in PurePosixPath(path).parts
        and not any(part.startswith(".") for part in PurePosixPath(path).parts)
        and "\\" not in path and ":" not in path
        and PurePosixPath(path).suffix.lower() in {".h", ".hpp", ".hh", ".cpp", ".cc", ".cxx", ".c", ".ino"})
    diagnostics = [dict(item) for item in build["linker_diagnostics"][:8] if isinstance(item, dict)]
    tokens = set()
    for item in diagnostics:
        tokens.update(re.findall(r"\b[A-Za-z_]\w*\b", str(item.get("symbol", "")))[:12])
    tokens.difference_update({"const", "unsigned", "long", "char", "int", "float", "double", "void", "bool", "vtable", "for"})
    locations = {item["path"]: item.get("line") or 1 for item in diagnostics if item.get("path") in known}
    candidates, scanned = [], 0
    from .build_readiness import _code
    for path in known[:256]:
        try:
            text = read_file(path)
        except (OSError, ValueError):
            continue
        scanned += len(text)
        if scanned > 2_000_000:
            break
        code = _code(text)
        matches = [match for token in tokens for match in [re.search(r"\b" + re.escape(token) + r"\b", code)] if match]
        if path in locations or matches:
            center = locations.get(path) or code.count("\n", 0, min(match.start() for match in matches)) + 1
            candidates.append((path not in locations, PurePosixPath(path).suffix not in {".h", ".hpp", ".hh"}, path, center, text))
    import hashlib
    sources = []
    for _, _, path, center, text in sorted(candidates)[:6]:
        start = max(1, center - 4)
        lines = text.splitlines()[start - 1:start + 24]
        sources.append({"path": path, "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "start_line": start, "numbered_preview": "\n".join(f"{start+i}: {line[:180]}" for i, line in enumerate(lines))[:1200],
            "preview_only": True})
    return {"build_id": build.get("build_id"), "build_source_revision": build.get("workspace_revision"),
        "diagnostics_stale": bool(build.get("verification_stale")), "diagnostics": diagnostics,
        "current_sources": sources, "candidate_matches_are_advisory": True,
        "instruction": "The real linker could not resolve or uniquely define these symbols. Read the current declarations, "
            "implementations and callers; implement the required behavior with matching signatures and linkage, then rebuild. "
            "Source matches are candidates, not proof of ownership. Check build filters or verified dependencies if the "
            "implementation already exists. Do not delete required calls, add empty stubs, or change hardware to hide the error."}
