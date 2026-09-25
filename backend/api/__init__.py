"""FastAPI integration for ForgeX with lazy application imports."""

from __future__ import annotations

from typing import Any

__all__ = ["app", "create_app"]


def __getattr__(name: str) -> Any:
    if name in {"app", "create_app"}:
        from .app import app, create_app
        return app if name == "app" else create_app
    raise AttributeError(name)
