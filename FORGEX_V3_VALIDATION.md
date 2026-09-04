# Forge Agent V3 Validation

Validation performed on the merged source tree before packaging.

## Passed

- Python compile check across `backend`, `tests`, and `examples`.
- Full backend regression suite: **1347 passed**.
- Focused agent/runtime/API/hardware suite: **164 passed** after V3 compatibility fixes.
- Frontend state suite: **21 passed**.
- Forge V3 staged-overlay smoke: read active → edit stage → read staged version while active source remains unchanged.
- `update_plan` smoke.
- Built-in skill discovery smoke: `firmware-review`, `platformio-repair`, `serial-debug`.
- TypeScript/TSX source syntax transpile check passed for frontend/Electron implementation files.

## Environment note

The validation container did not have the `pyserial` package installed and had no network access to install it. The backend tests were therefore run with a minimal **external test-only import shim** for the `serial` package. The shim is not included in this repository or ZIP. `pyserial>=3.5,<4.0` remains declared in `requirements.txt` and must be installed normally on a real ForgeX development machine.

The source archive intentionally excludes `node_modules`, so full Next.js/Electron dependency-aware typecheck/build was not performed in this container. Run `npm install` in the repository root and `frontend/`, then execute the commands documented in `README.md`.
