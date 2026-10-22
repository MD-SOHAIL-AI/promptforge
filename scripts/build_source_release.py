from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "scripts" / "source-release-manifest.json"
SKIP_DIRS = {".git", "node_modules", ".next", ".next-e2e", "test-results", "playwright-report", "dist", ".venv", "venv", "virtualenv", ".pio", ".pytest_cache", "__pycache__", ".cache", ".forgex", "hardware-signoff-reports"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".tsbuildinfo", ".bin", ".elf", ".hex", ".map", ".uf2"}
ALLOWED_WORKSPACE = {"workspace/.gitkeep", "workspace/artifacts/.gitkeep", "workspace/builds/.gitkeep", "workspace/logs/.gitkeep", "workspace/projects/.gitkeep"}


def required_paths(root: Path) -> list[str]:
    data = json.loads((root / "scripts" / "source-release-manifest.json").read_text(encoding="utf-8"))
    return [str(item).replace("\\", "/") for item in data["required"]]


def verify_required(root: Path) -> None:
    missing = [item for item in required_paths(root) if not (root / item).is_file()]
    if missing:
        raise SystemExit("Missing required source files:\n" + "\n".join(f"- {item}" for item in missing))


def include_file(path: Path) -> bool:
    rel = path.relative_to(ROOT).as_posix()
    if rel.startswith("frontend/public/vendor/monaco/"):
        return False  # Generated from the pinned dependency by frontend prebuild.
    parts = rel.split("/")
    if any(part in SKIP_DIRS for part in parts):
        return False
    if path.suffix.lower() in SKIP_SUFFIXES:
        return False
    name = path.name.lower()
    if name == ".env" or (name.startswith(".env.") and name not in {".env.example", ".env.sample", ".env.template"}):
        return False
    if parts[0] == "workspace" and rel not in ALLOWED_WORKSPACE:
        return False
    if path.is_symlink():
        return False
    if name.endswith(".zip") and "review" in name:
        return False
    return True


def build(output: Path) -> None:
    verify_required(ROOT)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(ROOT.rglob("*")):
            if not path.is_file() or not include_file(path):
                continue
            archive.write(path, path.relative_to(ROOT).as_posix())


def verify_archive(archive: Path, *, full_tests: bool) -> None:
    with tempfile.TemporaryDirectory(prefix="forgex-release-") as tmp:
        destination = Path(tmp) / "ForgeX"
        with zipfile.ZipFile(archive) as handle:
            for info in handle.infolist():
                target = (destination / info.filename).resolve()
                if destination.resolve() not in target.parents and target != destination.resolve():
                    raise SystemExit(f"Unsafe archive member: {info.filename}")
            handle.extractall(destination)
        verify_required(destination)
        env = os.environ.copy()
        env["PYTHONPATH"] = str(destination)
        subprocess.run(
            [sys.executable, "-c", "from backend.api.app import create_app; a=create_app(); assert a.title == 'ForgeX API'; print('clean-archive-startup-ok')"],
            cwd=destination,
            env=env,
            check=True,
        )
        if full_tests:
            subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=destination, env=env, check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT.parent / "ForgeX-Source.zip")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--full-tests", action="store_true")
    args = parser.parse_args()
    if args.verify_only:
        with tempfile.TemporaryDirectory(prefix="forgex-build-") as tmp:
            archive = Path(tmp) / "ForgeX-Source.zip"
            build(archive)
            verify_archive(archive, full_tests=args.full_tests)
            print("ForgeX clean source release verification passed (temporary archive removed)")
        return
    build(args.output)
    verify_archive(args.output, full_tests=args.full_tests)
    print(args.output)

if __name__ == "__main__":
    main()
