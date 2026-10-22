import assert from "node:assert/strict";
import test from "node:test";

import { roleBindingSource, splitModelReference } from "./model-role-bindings.ts";

const providers = [
  { provider_id: "open", display_name: "Open", default_model: "default", enabled: true, configured: true, auth_type: "api_key", local: false },
  { provider_id: "openrouter", display_name: "OpenRouter", default_model: "default", enabled: true, configured: true, auth_type: "api_key", local: false },
];

test("role model references preserve routed model ids containing slashes", () => {
  assert.deepEqual(
    splitModelReference("openrouter/openai/gpt-oss-120b:free", providers, "open", "fallback"),
    { providerId: "openrouter", modelId: "openai/gpt-oss-120b:free" },
  );
});

test("unknown or absent role references use the explicit fallback", () => {
  assert.deepEqual(
    splitModelReference("missing/vendor/model", providers, "open", "fallback"),
    { providerId: "open", modelId: "fallback" },
  );
  assert.equal(roleBindingSource("role"), "Role override");
  assert.equal(roleBindingSource("task_route"), "Inherits task route");
});
