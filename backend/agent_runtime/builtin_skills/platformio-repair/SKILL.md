# PlatformIO build and repair

Use this skill when a PlatformIO firmware build fails.

1. Read `platformio.ini` before changing framework, board, environment, or dependency settings.
2. Treat compiler/linker diagnostics as evidence. Search for the exact symbol, include, path, or library named by the diagnostic.
3. Make the smallest source/configuration change that addresses the observed failure.
4. Re-run `build_firmware` after each repair. Never claim success without a successful build result.
5. Do not change board targets or pin assignments merely to silence an error unless the user asked for that change.
6. Preserve generated firmware artifact metadata after a verified build so a later flash request can reuse it safely.
