# PlatformIO build and repair

Use this skill when a PlatformIO firmware build fails.

1. Read `platformio.ini` before changing framework, board, environment, or dependency settings.
2. Treat compiler/linker diagnostics as evidence. Search for the exact symbol, include, path, or library named by the diagnostic.
3. Make the smallest source/configuration change that addresses the observed failure.
4. Re-run `build_firmware` after each repair. Never claim success without a successful build result.
5. Do not change board targets or pin assignments merely to silence an error unless the user asked for that change.
6. Preserve generated firmware artifact metadata after a verified build so a later flash request can reuse it safely.
7. Verify external libraries with the advertised `resolve_dependency` tool and use unambiguous `owner/name` registry IDs in `lib_deps`. Do not invent package names or function names. Omit libraries supplied by the selected framework, such as `Preferences` in Arduino-ESP32.
8. When dependency validation fails, use its concrete error code and diagnostics. A command or encoding failure is not evidence that the registry is offline. Correct missing or ambiguous dependencies before retrying an unchanged build.
9. Validate the requested version as well as the package identity. Read registry metadata or installed headers to establish compatible versions and APIs; do not guess replacement versions.
10. Complete every missing project-local header before rebuilding. For an interface mismatch, read both the header declaration and implementation, then update callers to the same constructor and method signatures. Group related module edits into a coherent milestone.
