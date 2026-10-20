import assert from "node:assert/strict";
import test from "node:test";

import {
  activeComposerProviderId,
  activeComposerModelKey,
  filterComposerModelOptions,
  composerProviderOptions,
  composerModelOptions,
  parseComposerModelKey,
} from "./composer-model-options.ts";

const openrouter = {
  provider_id: "openrouter",
  display_name: "OpenRouter API",
  enabled: true,
  configured: true,
  auth_type: "api_key" as const,
  local: false,
  default_model: "openai/gpt-oss-20b:free",
};

test("saved retired models remain visible as unavailable options", () => {
  const models = [{provider_id: "openrouter", model_id: openrouter.default_model,
    display_name: "Retired model", local: false, availability: "retired" as const}];
  const options = composerModelOptions([openrouter], [], {openrouter: models});
  assert.equal(options[0].modelId, openrouter.default_model);
  assert.equal(options[0].agentCompatibility, "unavailable");
});

const openai = {
  provider_id: "openai",
  display_name: "OpenAI API",
  enabled: true,
  configured: true,
  auth_type: "api_key" as const,
  local: false,
  default_model: "gpt-4o-mini",
};

test("composer model selector follows the code generation route", () => {
  const routes = [{
    task_type: "code_generation",
    provider_id: "openrouter",
    model_id: "cohere/north-mini-code:free",
    fallback_enabled: true,
    fallback_provider_id: null,
    local_only: false,
  }];
  const options = composerModelOptions([openrouter, openai], routes);

  assert.equal(activeComposerProviderId([openrouter, openai], routes), "openrouter");
  assert.equal(activeComposerModelKey([openrouter, openai], routes), "openrouter::cohere/north-mini-code:free");
  assert.deepEqual(options.map((option) => option.key), [
    "openrouter::cohere/north-mini-code:free",
    "openrouter::openai/gpt-oss-20b:free",
  ]);
});

test("composer model selector groups fetched provider models by free and paid", () => {
  const options = composerModelOptions([openrouter], [], {
    openrouter: [
      { provider_id: "openrouter", model_id: "cohere/north-mini-code:free", display_name: "North Mini Code", free: true, local: false },
      { provider_id: "openrouter", model_id: "openai/gpt-4o-mini", display_name: "GPT-4o mini", free: false, local: false },
      { provider_id: "openrouter", model_id: "vendor/unknown", display_name: "Unknown price", local: false },
    ],
  }, "openrouter");

  assert.deepEqual(options.map((option) => [option.key, option.group]), [
    ["openrouter::openai/gpt-oss-20b:free", "Free"],
    ["openrouter::cohere/north-mini-code:free", "Free"],
    ["openrouter::openai/gpt-4o-mini", "Paid"],
    ["openrouter::vendor/unknown", "Other"],
  ]);
});

test("composer model selector does not duplicate the active route", () => {
  const options = composerModelOptions([openrouter], [{
    task_type: "code_generation",
    provider_id: "openrouter",
    model_id: "openai/gpt-oss-20b:free",
    fallback_enabled: true,
    fallback_provider_id: null,
    local_only: false,
  }]);

  assert.equal(options.length, 1);
  assert.equal(options[0].key, "openrouter::openai/gpt-oss-20b:free");
});

test("composer model selector ignores disabled or unconfigured providers", () => {
  const disabled = { ...openai, enabled: false };
  const unconfigured = { ...openrouter, configured: false };

  assert.deepEqual(composerProviderOptions([disabled, unconfigured]), []);
  assert.deepEqual(composerModelOptions([disabled, unconfigured], []), []);
  assert.deepEqual(composerModelOptions([disabled], [], { openai: [{ provider_id: "openai", model_id: "hidden", display_name: "Hidden", free: false, local: false }] }, "openai"), []);
});

test("composer model selector flags unsupported and unverified agent models", () => {
  const options = composerModelOptions([openrouter], [], {
    openrouter: [
      { provider_id: "openrouter", model_id: "vendor/chat", display_name: "Chat", local: false },
      { provider_id: "openrouter", model_id: "vendor/embed", display_name: "Embed", local: false, capabilities: { text_generation: false } },
    ],
  }, "openrouter");
  assert.equal(options.find((item) => item.modelId === "vendor/chat")?.agentCompatibility, "unverified");
  assert.equal(options.find((item) => item.modelId === "vendor/embed")?.agentCompatibility, "unsupported");
});

test("composer model search matches names, ids, pricing groups, and multiple terms", () => {
  const options = composerModelOptions([openrouter], [], {
    openrouter: [
      { provider_id: "openrouter", model_id: "cohere/north-mini-code:free", display_name: "North Mini Code", free: true, local: false },
      { provider_id: "openrouter", model_id: "openai/gpt-4o-mini", display_name: "GPT-4o mini", free: false, local: false },
    ],
  }, "openrouter");

  assert.deepEqual(filterComposerModelOptions(options, "north free").map((option) => option.modelId), ["cohere/north-mini-code:free"]);
  assert.deepEqual(filterComposerModelOptions(options, "openai/gpt paid").map((option) => option.modelId), ["openai/gpt-4o-mini"]);
  assert.equal(filterComposerModelOptions(options, "  ").length, options.length);
});

test("composer model key parser preserves model ids with separators", () => {
  assert.deepEqual(parseComposerModelKey("openrouter::vendor/model::variant"), {
    providerId: "openrouter",
    modelId: "vendor/model::variant",
  });
});
