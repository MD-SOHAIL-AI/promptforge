"""Responsive catalog reads with one bounded refresh per provider."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import time

import httpx

CATALOG_REFRESH_TIMEOUT_SECONDS = 10.0
CATALOG_REFRESH_INTERVAL_SECONDS = 60.0


class CatalogRequests:
    def __init__(self, router):
        self.router = router
        self._tasks = {}
        self._refreshed = {}
        self._errors = {}

    def _view(self, provider_id):
        registry = self.router.registry
        with registry.storage.read_snapshot():
            cached = bool(registry.storage.provider_models(provider_id))
            models = self.router.catalog.models(provider_id)
            metadata = registry.storage.model_catalog_metadata(provider_id)
            from .qualification import model_qualification
            endpoint = registry.base_url(provider_id)
            serialized = [{**model.to_dict(), "qualification": model_qualification(provider_id, model.model_id, endpoint)} for model in models]
            return cached, {"models": serialized,
                "count": len(models), "source": "cache" if cached else "catalog",
                "checked_at": metadata.get("checked_at"), "error": self._errors.get(provider_id)}

    async def models(self, provider_id, *, refresh=False):
        cached, value = await asyncio.to_thread(self._view, provider_id)
        if cached and not refresh:
            if time.monotonic() - self._refreshed.get(provider_id, 0) >= CATALOG_REFRESH_INTERVAL_SECONDS:
                self._start(provider_id)
            return value
        return await asyncio.shield(self._start(provider_id))

    def _start(self, provider_id):
        task = self._tasks.get(provider_id)
        if task is None or task.done():
            task = asyncio.create_task(self._refresh(provider_id))
            self._tasks[provider_id] = task
            # A cached response can outlive the request that started its refresh.
            task.add_done_callback(lambda finished: finished.exception() if not finished.cancelled() else None)
        return task

    async def _refresh(self, provider_id):
        registry = self.router.registry
        try:
            provider = self.router._providers[provider_id]
            endpoint = await asyncio.to_thread(registry.base_url, provider_id)
            discovered = await asyncio.wait_for(provider.list_models(), timeout=CATALOG_REFRESH_TIMEOUT_SECONDS)
            source = getattr(provider, "model_list_source", "provider")
            if source == "provider":
                def save():
                    if (registry.base_url(provider_id) or "").rstrip("/") != (endpoint or "").rstrip("/"):
                        return
                    registry.save_models(provider_id, discovered)
                    registry.storage.save_model_catalog_metadata(provider_id,
                        {"source": source, "checked_at": datetime.now(timezone.utc).isoformat(),
                         "endpoint": (endpoint or "").rstrip("/")})
                await asyncio.to_thread(save)
            self._errors.pop(provider_id, None)
            _, value = await asyncio.to_thread(self._view, provider_id)
            value["source"] = source
            return value
        except Exception as exc:
            reason = f"HTTP {exc.response.status_code}" if isinstance(exc, httpx.HTTPStatusError) else type(exc).__name__
            self._errors[provider_id] = f"Could not fetch model list. Showing cached or starter models. ({reason})"
            _, value = await asyncio.to_thread(self._view, provider_id)
            value["source"] = "fallback"
            return value
        finally:
            self._refreshed[provider_id] = time.monotonic()

    async def aclose(self):
        tasks = list(self._tasks.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
