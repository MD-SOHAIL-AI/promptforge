"""Explicit bounded diagnostic export."""
from __future__ import annotations
from fastapi import APIRouter,Request
from fastapi.responses import JSONResponse
from backend.operations.resilience import RateLimitExceeded
from ..errors import APIError
router=APIRouter(prefix="/diagnostics",tags=["diagnostics"])
@router.get("/export")
async def export_diagnostics(request:Request):
 limiter=request.app.state.diagnostic_export_limiter
 scope=request.client.host if request.client else "local"
 try:limiter.acquire(scope)
 except RateLimitExceeded as exc:raise APIError(429,"DIAGNOSTIC_EXPORT_RATE_LIMITED","Diagnostic export rate limit exceeded.",{}) from exc
 exporter=request.app.state.diagnostic_exporter
 try:payload=exporter.export();request.app.state.operational_metrics.safe_increment("diagnostic_exports")
 except Exception as exc:raise APIError(503,"DIAGNOSTIC_EXPORT_UNAVAILABLE","Safe diagnostics could not be exported.",{}) from exc
 return JSONResponse(payload,headers={"Content-Disposition":'attachment; filename="forgex-diagnostics.json"',"Cache-Control":"no-store"})
