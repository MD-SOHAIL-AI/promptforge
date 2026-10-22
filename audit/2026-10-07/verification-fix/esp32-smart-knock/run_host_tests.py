"""Build and run the actual firmware with hardware stubs; no device access."""
import argparse
import pathlib
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--compiler", default="g++", help="C++ compiler path; Zig supported")
args = parser.parse_args()
root = pathlib.Path(__file__).resolve().parent
output = root / ".host-build"
output.mkdir(exist_ok=True)
compiler = pathlib.Path(args.compiler)
command = [str(compiler)]
if compiler.stem == "zig":
    command.append("c++")
command += ["-std=c++17", "-Wall", "-Wextra", "-Werror", "-I", str(root / "tests/host/stubs")]
command += [str(root / "tests/host/firmware_tests.cpp")]
command += [str(root / path) for path in (
    "src/knock/knock_detector.cpp", "src/pattern/pattern_manager.cpp", "src/storage/pattern_storage.cpp",
    "src/output/buzzer.cpp", "src/output/led_controller.cpp")]
binary = output / ("firmware-tests.exe" if sys.platform == "win32" else "firmware-tests")
command += ["-o", str(binary)]
subprocess.run(command, check=True)
result = subprocess.run([str(binary)], capture_output=True, text=True)
(output / "results.log").write_text(result.stdout + result.stderr, encoding="utf-8")
print(result.stdout, end="")
if result.returncode:
    print(result.stderr, file=sys.stderr)
raise SystemExit(result.returncode)
