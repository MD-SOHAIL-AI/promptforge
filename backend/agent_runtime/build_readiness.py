"""Advisory local include closure before compiling a generated project."""
import posixpath
import re
from pathlib import PurePosixPath

from .deliverables import explicit_artifacts


def local_header_readiness(paths, read_file, request="", *, source_directory="src", include_directory="include"):
    known = set(paths)
    expected = set(explicit_artifacts(request))
    folders = {"sensors", "control", "network", "storage", "system", "display", "config"}
    prefix = (source_directory or "src").rstrip("/") + "/"
    folders.update(PurePosixPath(path[len(prefix):]).parts[0] for path in known
                   if path.startswith(prefix) and "/" in path[len(prefix):])
    missing, interfaces, pairs, conflicts, missing_implementations = {}, [], [], [], []
    text_cache, code_cache = {}, {}
    text_bytes = 0

    def read_once(path):
        nonlocal text_bytes
        if path not in text_cache:
            value = read_file(path)
            text_bytes += len(value.encode("utf-8"))
            if text_bytes > 2_000_000:
                raise ValueError("advisory_source_budget_reached")
            text_cache[path] = value
        return text_cache[path]

    def code_once(path):
        if path not in code_cache:
            code_cache[path] = _code(read_once(path))
        return code_cache[path]

    for path in sorted(known):
        if PurePosixPath(path).suffix.lower() not in {".c", ".cpp", ".cc", ".cxx", ".h", ".hh", ".hpp", ".ino"}:
            continue
        try:
            text = read_once(path)
        except (OSError, ValueError):
            continue
        for include in re.findall(r'^\s*#\s*include\s*"([^"\n]+)"', text, re.M):
            candidates = {posixpath.normpath(posixpath.join(str(PurePosixPath(path).parent), include))}
            candidates.update(posixpath.normpath(directory + "/" + include)
                              for directory in (source_directory, include_directory) if directory is not None)
            if candidates & known:
                continue
            local = include.split("/")[0] in folders or bool(candidates & expected)
            if local:
                safe = sorted(candidate for candidate in candidates if not candidate.startswith(("../", "/")))
                if safe:
                    target = next((candidate for candidate in safe if candidate.startswith(prefix)), safe[0])
                    missing.setdefault(target, []).append(path)
        if path.endswith((".h", ".hpp")):
            implementations = [candidate for candidate in sorted(known)
                               if PurePosixPath(candidate).suffix in {".cpp", ".cc", ".c"}
                               and PurePosixPath(candidate).stem == PurePosixPath(path).stem]
            if implementations:
                pairs.append({"header": path, "implementations": implementations[:4],
                              "correction": "Read both the header and implementation before repairing an API mismatch."})
                for implementation in implementations[:4]:
                    try:
                        conflicts.extend(_interface_advice(path, text, implementation, read_once(implementation)))
                    except (OSError, ValueError):
                        continue
            for owner, body in _class_bodies(_code(text)):
                external_methods = {match["name"] for match in _METHOD.finditer(_member_code(body))
                    if match["ending"] == ";" and not re.search(r"\b(?:default|delete)\b|=\s*0\s*;", match[0])}
                if not external_methods or re.search(r"\btemplate\s*<", _code(text)):
                    continue
                remaining, scanned = set(external_methods), 0
                for candidate in [path, *(item for item in sorted(known) if item != path)][:256]:
                    if PurePosixPath(candidate).suffix.lower() not in {".cpp", ".cc", ".cxx", ".h", ".hpp", ".ino"}:
                        continue
                    try:
                        code = code_once(candidate)
                    except (OSError, ValueError):
                        continue
                    scanned += len(code)
                    if scanned > 2_000_000:
                        break
                    defined = re.findall(r"\b" + re.escape(owner) + r"::(~?\w+)\s*\([^;{}]*\)\s*(?:(?:const|noexcept)\s*)?(?::[^;{}]*)?\{", code)
                    remaining.difference_update(defined)
                    if not remaining:
                        break
                if remaining:
                    missing_implementations.append({"header": path, "class": owner,
                        "methods": sorted(remaining)[:16], "advisory": True,
                        "correction": "No definition was located for these declarations. Implement the required behavior or identify its existing definition; compile and link to verify."})
            for number, line in enumerate(text.splitlines(), 1):
                if re.search(r"\b(?:class|struct|enum)\s+|[\w~]+\s*\([^;{}]*\)\s*(?:const\s*)?;", line):
                    interfaces.append({"path": path, "line": number, "declaration": line.strip()[:240]})
                    if len(interfaces) >= 80:
                        break
    return {"ready": not missing,
            "missing_local_headers": [{"path": path, "included_by": users[:8]} for path, users in sorted(missing.items())][:64],
            "interfaces": interfaces[:80], "interface_pairs": pairs[:40],
            "missing_local_implementations": missing_implementations[:32],
            "interface_conflicts": conflicts[:32], "verification_level": "include_readiness"}


def _code(text):
    # Preserve line numbers and avoid reporting code inside comments or literals.
    return re.sub(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
                  lambda match: re.sub(r"[^\n]", " ", match.group()), text, flags=re.S)


_METHOD = re.compile(r"\b(?P<name>~?\w+)\s*\((?P<params>[^;{}]*)\)\s*(?P<qualifiers>(?:(?:const|noexcept|override|final)\s*)*)(?:=\s*(?:0|default|delete)\s*)?(?::[^;{}]*)?(?P<ending>[;{])")


def _method_signature(match):
    params = re.sub(r"\s+", "", match["params"])
    return match["name"], "" if params == "void" else params, "const" in match["qualifiers"].split()


def _member_code(body):
    depth, output = 0, []
    for character in body:
        if character == "{":
            output.append("{" if depth == 0 else " ")
            depth += 1
        elif character == "}":
            depth -= 1
            output.append("}" if depth == 0 else " ")
        else:
            output.append(character if depth == 0 or character == "\n" else " ")
    return "".join(output)


def _class_bodies(code):
    """Balance braces so inline methods and single-line classes stay intact."""
    for match in re.finditer(r"\b(?:class|struct)\s+(\w+)[^{;]*\{", code):
        start, depth = match.end(), 1
        for index in range(start, len(code)):
            depth += (code[index] == "{") - (code[index] == "}")
            if depth == 0:
                yield match[1], code[start:index]
                break


def _interface_advice(header, declaration, implementation, source):
    """Advisory evidence only: the compiler owns actual C++ validity."""
    classes, inline = {}, {}
    for owner, body in _class_bodies(_code(declaration)):
        methods = list(_METHOD.finditer(_member_code(body)))
        classes[owner] = {match["name"] for match in methods}
        inline[owner] = {_method_signature(match) for match in methods if match["ending"] == "{"}
    code = _code(source)
    findings = []
    for match in re.finditer(r"\b(?P<owner>\w+)::(?P<name>~?\w+)\s*\((?P<params>[^;{}]*)\)\s*(?P<qualifiers>(?:(?:const|noexcept)\s*)*)(?::[^;{}]*)?\{", code):
        owner, method = match["owner"], match["name"]
        names = [name for name in classes if name.casefold() == owner.casefold()]
        if len(names) != 1:
            continue
        declared = names[0]
        issue = (f"Implementation uses {owner}; the paired header declares {declared}." if owner != declared else
                 f"{owner}::{method} is not declared in the paired header." if method not in classes[declared]
                 and method not in {declared, "~" + declared} else
                 f"{owner}::{method} is already defined inline in the paired header; an out-of-line definition may redefine it."
                 if _method_signature(match) in inline[declared] else None)
        if issue:
            findings.append({"header": header, "implementation": implementation,
                "line": code.count("\n", 0, match.start()) + 1, "issue": issue,
                "advisory": True, "correction": "Read and align both files; compile to verify the complete interface."})
    return findings[:16]
