import test from "node:test";
import assert from "node:assert/strict";
import { providerPresentationStatus, providerStatusLabel } from "./model-provider-status.ts";

const base = { provider_id:"p", display_name:"P", enabled:true, configured:true, auth_type:"api_key", local:false, default_model:"m" };
test("provider presentation separates temporary runtime state from credentials", () => {
  assert.equal(providerPresentationStatus({ ...base, configured:false }), "needs_key");
  assert.equal(providerPresentationStatus({ ...base, health_status:"rate_limited" }), "rate_limited");
  assert.equal(providerPresentationStatus({ ...base, health_status:"quota_exhausted" }), "quota_exhausted");
  assert.equal(providerPresentationStatus({ ...base, health_status:"healthy" }), "ready");
  assert.equal(providerStatusLabel("rate_limited"), "Rate limited");
});
