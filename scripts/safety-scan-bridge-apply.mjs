import fs from "node:fs";
import path from "node:path";

const root = process.cwd();
const safetyRoute = fs.readFileSync(path.join(root, "backend", "api", "routes", "models.py"), "utf-8");
const packageJson = JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf-8"));
const packagedQa = fs.readFileSync(path.join(root, "scripts", "qa-packaged-desktop.mjs"), "utf-8");
const packagedHelpers = fs.readFileSync(path.join(root, "scripts", "qa-packaged-helpers.mjs"), "utf-8");
const genericContracts = fs.readFileSync(path.join(root, "backend", "bridges", "generic", "models.py"), "utf-8");
const genericPersistence = fs.readFileSync(path.join(root, "backend", "bridges", "generic", "persistence.py"), "utf-8");
const agyAdapter = fs.readFileSync(path.join(root, "backend", "bridges", "providers", "agy_generic.py"), "utf-8");
const appComposition = fs.readFileSync(path.join(root, "backend", "api", "app.py"), "utf-8");
const genericCoordinator = fs.readFileSync(path.join(root, "backend", "bridges", "generic", "coordinator.py"), "utf-8");
const internalEvents = fs.readFileSync(path.join(root, "backend", "bridges", "generic", "event_transport.py"), "utf-8");
const agyCompatibility = fs.readFileSync(path.join(root, "backend", "bridges", "agy_execution_router.py"), "utf-8");
const legacyAgyRunner = fs.readFileSync(path.join(root, "backend", "bridges", "providers", "antigravity_runner.py"), "utf-8");
const genericApi = fs.readFileSync(path.join(root, "backend", "api", "routes", "generic_runs.py"), "utf-8");
const genericApiSchemas = fs.readFileSync(path.join(root, "backend", "api", "schemas", "generic_runs.py"), "utf-8");
const genericApiPolicy = fs.readFileSync(path.join(root, "backend", "bridges", "generic", "api_policy.py"), "utf-8");
const genericAgentHook = fs.readFileSync(path.join(root, "frontend", "hooks", "use-generic-agent-run.ts"), "utf-8");
const genericAgentPanel = fs.readFileSync(path.join(root, "frontend", "components", "ide", "generic-agent-panel.tsx"), "utf-8");
const liveAgyQa = fs.readFileSync(path.join(root, "scripts", "qa-agy-generic-live.mjs"), "utf-8");
const liveAgyCore = fs.readFileSync(path.join(root, "scripts", "qa-agy-generic-live-core.mjs"), "utf-8");
const trustedAgyCore = fs.readFileSync(path.join(root, "scripts", "qa-agy-trusted-workspace-core.mjs"), "utf-8");
const trustedAgyCommand = fs.readFileSync(path.join(root, "scripts", "qa-agy-trust-workspace.mjs"), "utf-8");
const trustedAgyBackend = fs.readFileSync(path.join(root, "backend", "bridges", "agy_trusted_workspace.py"), "utf-8");
const nativeAgyCommand = fs.readFileSync(path.join(root, "scripts", "qa-agy-native-write.mjs"), "utf-8");
const nativeAgyCore = fs.readFileSync(path.join(root, "scripts", "qa-agy-native-write-core.mjs"), "utf-8");
const scratchAgyCommand = fs.readFileSync(path.join(root, "scripts", "qa-agy-scratch-import.mjs"), "utf-8");
const scratchAgyCore = fs.readFileSync(path.join(root, "scripts", "qa-agy-scratch-import-core.mjs"), "utf-8");
const agyProjectImport = fs.readFileSync(path.join(root, "backend", "bridges", "agy_scratch_project_import.py"), "utf-8");
const agyProjectImportCli = fs.readFileSync(path.join(root, "backend", "bridges", "agy_scratch_project_import_cli.py"), "utf-8");
const agyProjectImportQa = fs.readFileSync(path.join(root, "scripts", "qa-agy-scratch-project-import.mjs"), "utf-8");
const agyAssistedRunner = fs.readFileSync(path.join(root, "backend", "bridges", "agy_assisted_runner.py"), "utf-8");
const agyAssistedCli = fs.readFileSync(path.join(root, "backend", "bridges", "agy_assisted_runner_cli.py"), "utf-8");
const agyAssistedQa = fs.readFileSync(path.join(root, "scripts", "qa-agy-assisted-runner.mjs"), "utf-8");
const agyRunner = fs.readFileSync(path.join(root, "backend", "bridges", "providers", "antigravity_runner.py"), "utf-8");
const codexDetect = fs.readFileSync(path.join(root, "scripts", "qa-codex-detect.mjs"), "utf-8");
const codexLoginBackend = fs.readFileSync(path.join(root, "backend", "bridges", "codex_login.py"), "utf-8");
const codexLoginQa = fs.readFileSync(path.join(root, "scripts", "qa-codex-oauth-bridge.mjs"), "utf-8");
const codexOAuthSmokeCore = fs.readFileSync(path.join(root, "scripts", "qa-codex-oauth-smoke-core.mjs"), "utf-8");
const codexOAuthSmokeReview = fs.readFileSync(path.join(root, "backend", "bridges", "codex_oauth_smoke_review.py"), "utf-8");
const codexOAuthSmokeBackend = fs.readFileSync(path.join(root, "backend", "bridges", "codex_oauth_smoke.py"), "utf-8");
const codexStatusBackend = fs.readFileSync(path.join(root, "backend", "bridges", "codex_status.py"), "utf-8");
const codexSafeEnv = fs.readFileSync(path.join(root, "scripts", "codex-safe-user-env.mjs"), "utf-8");
const codexSessionParity = fs.readFileSync(path.join(root, "scripts", "codex-session-parity-core.mjs"), "utf-8");
const modelSettingsPanel = fs.readFileSync(path.join(root, "frontend", "components", "ide", "model-settings-panel.tsx"), "utf-8");
const nativeCodexCommand = fs.readFileSync(path.join(root, "scripts", "qa-codex-native-write.mjs"), "utf-8");
const nativeCodexCore = fs.readFileSync(path.join(root, "scripts", "qa-codex-native-write-core.mjs"), "utf-8");
const codexSubscriptionReview = fs.readFileSync(path.join(root, "backend", "bridges", "codex_subscription_review.py"), "utf-8");
const envExample = fs.readFileSync(path.join(root, ".env.example"), "utf-8");
const providerCapabilities = fs.readFileSync(path.join(root, "backend", "bridges", "generic", "provider_capabilities.py"), "utf-8");
const providerRouting = fs.readFileSync(path.join(root, "backend", "bridges", "generic", "routing.py"), "utf-8");
const toolContracts = fs.readFileSync(path.join(root, "backend", "agent_runtime", "tool_contracts.py"), "utf-8");
const toolPolicy = fs.readFileSync(path.join(root, "backend", "agent_runtime", "tool_policy.py"), "utf-8");
const toolExecutor = fs.readFileSync(path.join(root, "backend", "agent_runtime", "tool_executor.py"), "utf-8");
const toolRuntime = fs.readFileSync(path.join(root, "backend", "agent_runtime", "tool_runtime.py"), "utf-8");
const providerContracts = fs.readFileSync(path.join(root, "backend", "agent_runtime", "provider_contracts.py"), "utf-8");
const fakeApiProvider = fs.readFileSync(path.join(root, "backend", "agent_runtime", "fake_api_provider.py"), "utf-8");
const toolRuntimeQa = fs.readFileSync(path.join(root, "scripts", "qa-tool-runtime-smoke.mjs"), "utf-8");
const openAiApiProvider = fs.readFileSync(path.join(root, "backend", "agent_runtime", "openai_api_provider.py"), "utf-8");
const apiProviderSmoke = fs.readFileSync(path.join(root, "backend", "agent_runtime", "api_provider_smoke.py"), "utf-8");
const apiProviderQa = fs.readFileSync(path.join(root, "scripts", "qa-api-provider-smoke.mjs"), "utf-8");
const apiProviderTests = fs.readFileSync(path.join(root, "tests", "unit", "test_openai_api_provider.py"), "utf-8");
const productAgentRoute = fs.readFileSync(path.join(root, "backend", "api", "routes", "agent_runtime.py"), "utf-8");
const productAgentService = fs.readFileSync(path.join(root, "backend", "agent_runtime", "product_agent_service.py"), "utf-8");
const productProviderRegistry = fs.readFileSync(path.join(root, "backend", "agent_runtime", "product_provider_registry.py"), "utf-8");
const productProviders = fs.readFileSync(path.join(root, "backend", "agent_runtime", "product_providers.py"), "utf-8");
const productAgentQa = fs.readFileSync(path.join(root, "scripts", "qa-agent-runtime-fake-smoke.mjs"), "utf-8");
const apiPlannerProvider = fs.readFileSync(path.join(root, "backend", "agent_runtime", "api_planner_provider.py"), "utf-8");
const apiProductRuntimeSmoke = fs.readFileSync(path.join(root, "backend", "agent_runtime", "api_product_runtime_smoke.py"), "utf-8");
const apiProviderDetect = fs.readFileSync(path.join(root, "backend", "agent_runtime", "api_provider_detect.py"), "utf-8");
const apiProductRuntimeQa = fs.readFileSync(path.join(root, "scripts", "qa-api-product-runtime-smoke.mjs"), "utf-8");
const apiProvidersDetectQa = fs.readFileSync(path.join(root, "scripts", "qa-api-providers-detect.mjs"), "utf-8");
const apiPlannerTests = fs.readFileSync(path.join(root, "tests", "unit", "test_api_planner_provider.py"), "utf-8");
const productAgentPanel = fs.readFileSync(path.join(root, "frontend", "components", "ide", "product-agent-panel.tsx"), "utf-8");
const toolRuntimeSources = [toolContracts, toolPolicy, toolExecutor, toolRuntime, providerContracts, fakeApiProvider].join("\n");
const apiPlannerSources = [apiPlannerProvider, apiProductRuntimeSmoke, apiProviderDetect, apiProductRuntimeQa, apiProvidersDetectQa].join("\n");
const checks = [
  ["product Agent runtime route exists behind feature flag", productAgentRoute.includes('prefix="/agent-runtime"') && appComposition.includes('FORGEX_ENABLE_AGENT_RUNTIME')],
  ["product Agent route does not call QA scripts", !/qa_|scripts|spawn|subprocess/i.test(productAgentRoute)],
  ["product Agent path executes no local CLI provider", !/subprocess|Popen|codex exec|agy -p|opencode|claude/i.test(productAgentRoute + productAgentService + productProviders)],
  ["product provider registry blocks paused and reference providers", ["agy_local_cli_paused", "codex_local_cli_paused", "opencode_reference_only"].every((value) => productProviderRegistry.includes(value)) && productProviderRegistry.includes('PRODUCT_PROVIDER_NOT_ROUTEABLE')],
  ["product fake planner is explicit and network-free", productProviders.includes("class FakeProductPlanner") && !/requests|httpx|urllib|socket|subprocess/.test(productProviders)],
  ["product runtime uses ForgeXToolRuntime", productAgentService.includes("ForgeXToolRuntime(") && productAgentService.includes("runtime.run_product")],
  ["product runtime uses persistent review service", productAgentService.includes("review_service.store is None") && appComposition.includes("review_service=bridge_reviews")],
  ["product ToolPlan is versioned and bounded", toolContracts.includes('PRODUCT_TOOLPLAN_VERSION = "forgex.toolplan.v1"') && toolContracts.includes("max_calls: int = 5")],
  ["product runtime is turn and tool bounded", ["max_turns: int = 5", "max_tool_calls_per_turn: int = 5", "max_total_tool_calls: int = 20", "max_runtime_seconds: float = 120.0"].every((value) => toolRuntime.includes(value))],
  ["product permissions deny external authority", ["active_workspace_write\": \"deny", "external_read\": \"deny", "external_write\": \"deny", "shell\": \"deny", "network\": \"deny", "install_dependencies\": \"deny", "apply_patch\": \"deny", "build\": \"deny", "flash\": \"deny"].every((value) => toolPolicy.includes(value))],
  ["product events exclude raw prompt and response", productAgentService.includes('allowed = {"event_type"') && !/events\.append\([^\n]*(instruction|task|response)/i.test(productAgentService)],
  ["product records persist no raw prompt response or API key", productAgentService.includes('"raw_prompt_persisted": False') && productAgentService.includes('"raw_response_persisted": False') && !/(write_text|write_bytes|open\()[^\n]*(instruction|api_key|raw_response)/i.test(productAgentService)],
  ["product review requires validated sandbox diff", toolRuntime.indexOf("denied_seen or not active_unchanged") < toolRuntime.indexOf('artifact_source="forgex_product_agent_runtime"')],
  ["product runtime cannot auto apply build or flash", !/PatchApplyService|build_project|flash_device/.test(productAgentService + productAgentRoute) && productAgentService.includes('"apply_run": False')],
  ["product fake smoke command is internal only", packageJson.scripts?.["qa:agent-runtime-fake-smoke"] === "node scripts/qa-agent-runtime-fake-smoke.mjs" && productAgentQa.includes("backend.agent_runtime.product_qa_smoke") && !/agy|codex|claude|opencode|api-provider-smoke/i.test(productAgentQa)],
  ["multi API planner providers are registered", ["gemini", "groq", "openrouter", "openai", "nvidia_nim"].every((provider) => apiPlannerProvider.includes(`"${provider}"`) && productProviderRegistry.includes("API_PLANNER_CONFIGS"))],
  ["multi API planner providers are disabled by default", ["FORGEX_ENABLE_GEMINI_PROVIDER=0", "FORGEX_ENABLE_GROQ_PROVIDER=0", "FORGEX_ENABLE_OPENROUTER_PROVIDER=0", "FORGEX_ENABLE_OPENAI_PROVIDER=0", "FORGEX_ENABLE_NVIDIA_NIM_PROVIDER=0"].every((marker) => envExample.includes(marker)) && apiPlannerProvider.includes('"enabled_by_default": False')],
  ["multi API planner providers require provider-specific flags", ["FORGEX_ENABLE_GEMINI_PROVIDER", "FORGEX_ENABLE_GROQ_PROVIDER", "FORGEX_ENABLE_OPENROUTER_PROVIDER", "FORGEX_ENABLE_OPENAI_PROVIDER", "FORGEX_ENABLE_NVIDIA_NIM_PROVIDER"].every((flag) => apiPlannerProvider.includes(flag)) && apiPlannerProvider.includes("env.get(config.provider_flag")],
  ["multi API planner providers require key presence", ["GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY", "NVIDIA_API_KEY"].every((flag) => apiPlannerProvider.includes(flag)) && apiPlannerProvider.includes("API_PROVIDER_KEY_MISSING")],
  ["multi API planner providers require explicit confirmation", apiPlannerProvider.includes("CONFIRMATION_REQUIRED") && apiProductRuntimeSmoke.includes("--confirm-real-api") && apiProductRuntimeSmoke.includes("confirmed=args.confirm_real_api")],
  ["API product runtime smoke command is registered", packageJson.scripts?.["qa:api-product-runtime-smoke"] === "node scripts/qa-api-product-runtime-smoke.mjs" && apiProductRuntimeQa.includes("backend.agent_runtime.api_product_runtime_smoke")],
  ["API provider detection command is registered and non-networking", packageJson.scripts?.["qa:api-providers-detect"] === "node scripts/qa-api-providers-detect.mjs" && apiProvidersDetectQa.includes("backend.agent_runtime.api_provider_detect") && apiProviderDetect.includes('"network_attempted": False') && !/(urlopen|requests|httpx|fetch|socket)/.test(apiProviderDetect)],
  ["API planner transport is HTTPS only", apiPlannerProvider.includes('parsed.scheme != "https"') && apiPlannerProvider.includes('urlparse(url).scheme != "https"')],
  ["API planner sends ToolPlan instructions only", apiPlannerProvider.includes("You are a planner only") && apiPlannerProvider.includes("Return JSON only") && apiPlannerProvider.includes("API_PROVIDER_SMOKE.txt") && !apiPlannerProvider.includes("active_workspace_root")],
  ["API planner rejects raw prose and unsafe ToolPlans", apiPlannerProvider.includes("ProductToolPlan.parse_json(content, max_calls=5)") && apiPlannerProvider.includes("_validate_generated_plan") && apiPlannerProvider.includes("safe_relative_path") && apiPlannerTests.includes("test_invalid_model_output_fails_closed")],
  ["API planner writes only through product runtime", apiProductRuntimeSmoke.includes("ProductAgentService(") && apiProductRuntimeSmoke.includes("provider_id=provider_id") && !/ForgeXToolExecutor|write_text\([^\n]*API_PROVIDER_SMOKE/.test(apiProductRuntimeSmoke)],
  ["API planner smoke has one-request accounting and no retry", apiPlannerProvider.includes('"outbound_request_count"] = 1') && apiProductRuntimeSmoke.includes("outbound_request_count") && !/\b(?:retry|backoff)\b/i.test(apiPlannerProvider)],
  ["API planner does not print or persist secrets", !/(?:print|console\.log)\([^\n]*(?:api_key|Authorization)|(?:write_text|write_bytes)\([^\n]*(?:api_key|Authorization|OPENAI_API_KEY|GEMINI_API_KEY|GROQ_API_KEY|OPENROUTER_API_KEY|NVIDIA_API_KEY|NIM_API_KEY)/i.test(apiPlannerSources)],
  ["API planner does not persist raw prompt or response", !/(?:write_text|write_bytes)\([^\n]*(?:prompt|response|payload|body|content)/i.test(apiPlannerSources) && apiProductRuntimeSmoke.includes('"raw_prompt_persisted": False') && apiProductRuntimeSmoke.includes('"raw_response_persisted": False')],
  ["API planner tests use mocked transport and no live APIs", apiPlannerTests.includes("transport=lambda") && apiPlannerTests.includes("def transport(") && !apiPlannerTests.includes("post_chat_completion(")],
  ["product runtime route supports provider selection", productAgentRoute.includes("provider_id") && productAgentService.includes("provider_id") && productProviderRegistry.includes("resolve(self, provider_id")],
  ["Agent Runtime UI lists API planners without key values", productAgentPanel.includes("providers.map") && productAgentPanel.includes("item.routeable") && productAgentPanel.includes("generation_source") && !/(Authorization|OPENAI_API_KEY|GEMINI_API_KEY|GROQ_API_KEY|OPENROUTER_API_KEY|NVIDIA_API_KEY)/.test(productAgentPanel)],
  ["API planners cannot route paused local CLI providers", ["agy", "codex", "opencode"].every((provider) => productProviderRegistry.includes(`"${provider}"`)) && productProviderRegistry.includes('"reference_only"') && productProviderRegistry.includes('"paused"')],
  ["AGY is paused and non-routeable by default", /provider_id="agy"[\s\S]*?operational_state=ProviderOperationalState\.PAUSED[\s\S]*?execution_allowed=False[\s\S]*?routing_allowed=False/.test(providerCapabilities)],
  ["Codex is paused and non-routeable by default", /provider_id="codex_bridge"[\s\S]*?operational_state=ProviderOperationalState\.PAUSED[\s\S]*?execution_allowed=False[\s\S]*?routing_allowed=False/.test(providerCapabilities)],
  ["Claude CLI is disabled and non-routeable", /provider_id="claude_code_bridge"[\s\S]*?operational_state=ProviderOperationalState\.DISABLED[\s\S]*?execution_allowed=False[\s\S]*?routing_allowed=False/.test(providerCapabilities)],
  ["OpenCode remains reference-only and non-routeable", /provider_id="opencode_bridge"[\s\S]*?operational_state=ProviderOperationalState\.REFERENCE_ONLY[\s\S]*?execution_allowed=False[\s\S]*?routing_allowed=False/.test(providerCapabilities)],
  ["paused local CLI providers cannot route", providerRouting.includes("ProviderOperationalState.PAUSED") && providerRouting.includes("BridgeRouteDecision(False")],
  ["generic provider UI reports local CLI execution paused", genericApi.includes("execution_enabled = False") && genericApi.includes("Local CLI providers are paused")],
  ["ForgeX-owned tool runtime exists", toolRuntime.includes("class ForgeXToolRuntime") && toolExecutor.includes("class ForgeXToolExecutor")],
  ["fake API provider is in-process and network-free", fakeApiProvider.includes("class FakeApiProvider") && !/\b(?:requests|httpx|urllib|socket|subprocess)\b/.test(fakeApiProvider)],
  ["tool runtime has no local CLI execution path", !/\b(?:subprocess|Popen|spawn|agy|codex|claude|opencode)\b/i.test(toolRuntimeSources)],
  ["tool paths reject absolute and drive paths", toolPolicy.includes("windows.drive") && toolPolicy.includes("absolute_path_forbidden")],
  ["tool paths reject parent traversal", toolPolicy.includes('part in {"", ".."}')],
  ["tool paths reject symlink and reparse traversal", toolPolicy.includes("FILE_ATTRIBUTE_REPARSE_POINT") && toolPolicy.includes("link_or_reparse_forbidden")],
  ["tool writes target the managed sandbox", toolExecutor.includes("sandbox_root") && toolPolicy.includes("sandbox_escape")],
  ["tool runtime exposes no shell network or install tool", !/class ToolName[\s\S]*?(?:SHELL|NETWORK|INSTALL)/.test(toolContracts)],
  ["tool policy denies shell network and dependency install", ["\"shell\": \"deny\"", "\"network\": \"deny\"", "\"install_dependencies\": \"deny\""].every((marker) => toolPolicy.includes(marker))],
  ["tool runtime does not auto-apply build or flash", !/\.(?:apply|build|flash)\(/.test(toolRuntime) && ["apply_run: bool = False", "build_run: bool = False", "flash_run: bool = False"].every((marker) => toolRuntime.includes(marker))],
  ["raw prompt and provider output persistence is forbidden", toolRuntime.includes("raw_prompt_persisted: bool = False") && toolRuntime.includes("raw_provider_output_persisted: bool = False")],
  ["credential reads are forbidden", toolPolicy.includes("credential_read_forbidden") && toolPolicy.includes("is_sensitive_path")],
  ["review requires an exact validated diff", toolRuntime.includes("_validate_exact_diff") && toolRuntime.indexOf("_validate_exact_diff") < toolRuntime.indexOf("self._create_review")],
  ["non-OpenAI API providers remain disabled design placeholders", ["anthropic_api", "google_api", "local_model_api"].every((provider) => providerContracts.includes(`"${provider}"`)) && providerContracts.includes("enabled: bool = False")],
  ["OpenAI API provider is disabled by default", envExample.includes("FORGEX_ENABLE_API_PROVIDER_SPIKE=0") && envExample.includes("FORGEX_ENABLE_OPENAI_API_PROVIDER=0") && /provider_id="openai_api"[\s\S]*?operational_state=ProviderOperationalState\.DISABLED[\s\S]*?execution_allowed=False[\s\S]*?routing_allowed=False/.test(providerCapabilities)],
  ["OpenAI API provider requires both feature flags", openAiApiProvider.includes('SPIKE_FLAG = "FORGEX_ENABLE_API_PROVIDER_SPIKE"') && openAiApiProvider.includes('PROVIDER_FLAG = "FORGEX_ENABLE_OPENAI_API_PROVIDER"') && openAiApiProvider.includes("self._confirm_real_api")],
  ["real API QA requires explicit confirmation", apiProviderSmoke.includes('"--confirm-real-api"') && apiProviderSmoke.includes("confirmed=args.confirm_real_api")],
  ["real API QA command is registered", packageJson.scripts?.["qa:api-provider-smoke"] === "node scripts/qa-api-provider-smoke.mjs" && apiProviderQa.includes("backend.agent_runtime.api_provider_smoke")],
  ["OpenAI API key is not printed or persisted", !/print\([^\n]*API_KEY|write_text\([^\n]*API_KEY|write_bytes\([^\n]*API_KEY/.test(openAiApiProvider + apiProviderSmoke) && apiProviderTests.includes("test_api_key_is_not_printed_or_persisted")],
  ["OpenAI QA reports key presence as booleans only", apiProviderSmoke.includes("api_key_detected") && apiProviderSmoke.includes("api_key_printed") && apiProviderSmoke.includes("api_key_persisted")],
  ["OpenAI raw prompt and response are not persisted", !/(?:write_text|write_bytes)\([^\n]*(?:prompt|response)/i.test(openAiApiProvider + apiProviderSmoke) && apiProviderTests.includes("test_raw_prompt_and_response_are_not_in_safe_metadata")],
  ["OpenAI request and response bodies are not persisted", !/(?:write_text|write_bytes)\([^\n]*(?:payload|body|output_text)/i.test(openAiApiProvider + apiProviderSmoke)],
  ["OpenAI smoke has one-request accounting and no retry", openAiApiProvider.includes('self._last_metadata["outbound_request_count"] = 1') && apiProviderSmoke.includes("outbound_request_attempted") && !/\b(?:retry|backoff)\b/i.test(openAiApiProvider)],
  ["OpenAI provider tests use injected transport", apiProviderTests.includes("transport=lambda") && !apiProviderTests.includes("_post_json(")],
  ["invalid OpenAI model output fails closed", openAiApiProvider.includes("API_PROVIDER_INVALID_JSON") || (providerContracts.includes("API_PROVIDER_INVALID_JSON") && openAiApiProvider.includes("model_output_invalid_json"))],
  ["OpenAI provider has no shell process or install path", !/\b(?:subprocess|Popen|spawn|os\.system|shell=True)\b/.test(openAiApiProvider)],
  ["OpenAI provider cannot route through normal UI", providerCapabilities.includes('provider_id="openai_api"') && providerCapabilities.includes("routing_allowed=False") && !genericApiSchemas.includes('Literal["openai_api"]')],
  ["tool runtime smoke command is registered", packageJson.scripts?.["qa:tool-runtime-smoke"] === "node scripts/qa-tool-runtime-smoke.mjs"],
  ["tool runtime smoke launches only the internal fake QA module", toolRuntimeQa.includes("backend.agent_runtime.qa_smoke") && !/agy|codex|claude|opencode/i.test(toolRuntimeQa)],
  ["generic bridge contracts are available", /"generic_bridge_contracts_available": True/.test(safetyRoute)],
  ["generic coordinator is available", /"generic_bridge_coordinator_available": True/.test(safetyRoute) && appComposition.includes("GenericBridgeRunCoordinator(")],
  ["generic bridge routing defaults disabled", agyCompatibility.includes("generic_routing_enabled: bool = False")],
  ["generic API defaults disabled", genericApiPolicy.includes("api_enabled: bool = False")],
  ["generic run start is denied by default", genericApi.includes("_policy(request).require_execution") && genericApiPolicy.includes("Generic agent execution is disabled by local policy.")],
  ["AGY generic provider defaults disabled", agyCompatibility.includes("generic_provider_enabled: bool = False")],
  ["AGY generic cutover defaults disabled", agyCompatibility.includes("cutover_enabled: bool = False")],
  ["partial AGY cutover fails closed", agyCompatibility.includes("if self.generic_routing_enabled && self.generic_provider_enabled") || (agyCompatibility.includes("self.generic_routing_enabled and self.generic_provider_enabled") && agyCompatibility.includes("return AGYExecutionMode.BLOCKED"))],
  ["AGY compatibility router is available", /"agy_compatibility_router_available": True/.test(safetyRoute)],
  ["generic cutover reuses one sandbox", agyAdapter.includes("start_run_in_sandbox") && legacyAgyRunner.includes("def start_run_in_sandbox")],
  ["generic failure has no legacy fallback", !agyCompatibility.includes("fallback")],
  ["AGY generic adapter is registered", /"agy_generic_adapter_registered"/.test(safetyRoute) && appComposition.includes("bridge_provider_registry.register(selected_agy_provider)")],
  ["AGY generic adapter execution defaults disabled", /"agy_generic_adapter_enabled"/.test(safetyRoute) && appComposition.includes("execution_enabled=generic_cutover_enabled") && agyCompatibility.includes("cutover_enabled: bool = False")],
  ["AGY adapter delegates to the legacy runner", agyAdapter.includes("self._runner.start_run") && agyAdapter.includes("self._runner.cancel_run")],
  ["AGY adapter has no duplicate subprocess implementation", !/\b(?:subprocess|Popen|kill_process_tree|taskkill)\b/.test(agyAdapter)],
  ["bridge routing remains disabled", /"bridge_routing_enabled": False/.test(safetyRoute)],
  ["codex execution remains disabled", /"codex_execution_enabled": False/.test(safetyRoute)],
  ["claude execution remains disabled", /"claude_execution_enabled": False/.test(safetyRoute)],
  ["opencode execution remains disabled", /"opencode_execution_enabled": False/.test(safetyRoute)],
  ["generic sandbox remains required", /"generic_bridge_sandbox_required": True/.test(safetyRoute) && genericContracts.includes("sandbox_required: bool = True")],
  ["sanitized generic event transport is public", /"generic_bridge_event_transport_internal_only": False/.test(safetyRoute) && genericApi.includes("text/event-stream") && internalEvents.includes("class InternalBridgeEventTransport")],
  ["public generic run API is gated", safetyRoute.includes('"public_generic_run_api_enabled": bool(getattr(generic_api_flags, "api_enabled", False))')],
  ["generic execution requires all five gates", ["agy_bridge_enabled", "api_enabled", "routing_enabled", "agy_provider_enabled", "cutover_enabled"].every((flag) => genericApiPolicy.includes(flag))],
  ["only AGY is supported by the generic API", genericApiSchemas.includes('Literal["agy"]') && !genericApi.includes('provider_id="codex"') && !genericApi.includes('provider_id="claude"') && !genericApi.includes('provider_id="opencode"')],
  ["generic request accepts no command or environment fields", genericApiSchemas.includes("class GenericRunStartRequest") && !/\n\s+(?:command|executable|arguments|environment|working_directory|workspace_root|sandbox_root|shell|auto_apply|auto_build|auto_flash):/.test(genericApiSchemas)],
  ["SSE events use sanitized public event records", genericApi.includes("json.dumps(event.to_dict()") && !genericApi.includes("stdout") && !genericApi.includes("stderr")],
  ["SSE failure does not fail provider execution", !genericCoordinator.includes("_transport_failed_runs")],
  ["generic start is idempotent", genericApi.includes("_idempotent_record") && genericAgentHook.includes("submitLockRef.current")],
  ["generic artifacts grant no apply authority", !genericCoordinator.includes("PatchApplyService") && !genericCoordinator.includes("RollbackRestore")],
  ["auto-apply remains disabled", /"auto_apply_enabled": False/.test(safetyRoute)],
  ["auto-build after apply remains disabled", /"auto_build_after_apply": False/.test(safetyRoute)],
  ["auto-flash after apply remains disabled", /"auto_flash_after_apply": False/.test(safetyRoute)],
  ["apply requires patch apply flag", safetyRoute.includes("patch_apply_feature_flag")],
  ["apply requires rollback restore flag", safetyRoute.includes("rollback_restore_feature_flag")],
  ["raw instructions are excluded from persistence", /"raw_instructions_persisted": False/.test(safetyRoute) && !genericPersistence.includes("request.instruction,")],
  ["generic run routes exist", genericApi.includes('"/runs"') && genericApi.includes('"/generic/runs"') && genericApi.includes('"/runs/{run_id}/events"')],
  ["Agent panel has no Apply action", !/>\s*Apply\s*</.test(genericAgentPanel)],
  ["live AGY QA requires explicit confirmation", liveAgyQa.includes("--confirm-real-agy") && liveAgyQa.includes("BLOCKED_OPERATOR_DID_NOT_AUTHORIZE")],
  ["live AGY check-only mode is non-executing", liveAgyQa.includes("--check-only") && liveAgyCore.includes("where.exe") && !liveAgyCore.includes('spawnSync("agy"')],
  ["live AGY QA requires QA mode", liveAgyCore.includes('"FORGEX_QA_MODE"') && liveAgyQa.includes("missingLiveFlags(process.env)")],
  ["trusted AGY workspace defaults disabled", fs.readFileSync(path.join(root, ".env.example"), "utf-8").includes("FORGEX_ENABLE_AGY_TRUSTED_WORKSPACE=0")],
  ["trusted AGY workspace requires QA and explicit flag", trustedAgyBackend.includes('self.env.get("FORGEX_QA_MODE") == "1"') && trustedAgyBackend.includes("TRUSTED_WORKSPACE_FLAG")],
  ["trusted AGY workspace command is registered", packageJson.scripts?.["qa:agy-trust-workspace"] === "node scripts/qa-agy-trust-workspace.mjs"],
  ["trusted AGY workspace preparation executes no provider", trustedAgyCommand.includes("No AGY edit was executed.") && !trustedAgyCommand.includes('spawnSync("agy"')],
  ["trusted AGY workspace is marked and contained", trustedAgyCore.includes("TRUSTED_MARKER") && trustedAgyCore.includes("trusted_workspace_containment_rejected")],
  ["trusted AGY workspace rejects active and sensitive roots", trustedAgyCore.includes("trusted_workspace_active_overlap_rejected") && trustedAgyCore.includes("trusted_workspace_sensitive_root_rejected")],
  ["trusted AGY workspace rejects symlinks", trustedAgyCore.includes("rejectSymlinks") && trustedAgyBackend.includes("_reject_symlinks")],
  ["trusted AGY workspace reset is containment checked", trustedAgyCore.includes("trusted_workspace_delete_containment_rejected") && trustedAgyBackend.includes("Trusted workspace reset target escaped containment")],
  ["trusted AGY workspace locking is exclusive and stale-safe", trustedAgyBackend.includes("os.O_EXCL") && trustedAgyBackend.includes("_recover_stale_lock") && trustedAgyBackend.includes('value.get("token") != lease.token')],
  ["trusted AGY live run requires per-run attestation", liveAgyQa.includes("--trusted-workspace-attested") && liveAgyCore.includes("BLOCKED_TRUSTED_WORKSPACE_NOT_ATTESTED")],
  ["trusted AGY live cwd is reusable managed root", agyCompatibility.includes("acquire_and_reset") && agyCompatibility.includes("managed_sandbox_root = trusted.managed_root")],
  ["trusted AGY marker and prompt files are excluded from review", fs.readFileSync(path.join(root, "backend", "bridges", "diff_service.py"), "utf-8").includes("IGNORED_INTERNAL_FILES")],
  ["native AGY command is registered", packageJson.scripts?.["qa:agy-native-write"] === "node scripts/qa-agy-native-write.mjs"],
  ["native AGY requires explicit confirmation", nativeAgyCommand.includes("--confirm-native-agy") && nativeAgyCommand.includes("explicit_confirmation_required")],
  ["native AGY requires trusted workspace attestation", nativeAgyCommand.includes("--trusted-workspace-attested") && nativeAgyCommand.includes("trusted_workspace_attestation_required")],
  ["native AGY reuses guarded managed workspace", nativeAgyCommand.includes("guardTrustedWorkspace") && nativeAgyCommand.includes("prepareTrustedWorkspace")],
  ["native AGY refuses active and protected roots", trustedAgyCore.includes("trusted_workspace_active_overlap_rejected") && trustedAgyCore.includes("trusted_workspace_sensitive_root_rejected")],
  ["native AGY uses direct argv without shell", nativeAgyCommand.includes("spawnSync(executable, args") && nativeAgyCommand.includes("shell: false") && !nativeAgyCommand.includes("shell: true")],
  ["native AGY blocks dangerous permission bypass", nativeAgyCore.includes("dangerous_permission_flag_rejected") && nativeAgyCore.includes("--dangerously-skip-permissions")],
  ["native AGY persists sanitized classification only", nativeAgyCommand.includes("sanitizedNativeResult") && !nativeAgyCommand.includes("writeFileSync") && !/console\.(?:log|error)\([^\n]*(?:stdout|stderr)/.test(nativeAgyCommand)],
  ["native AGY does not auto-apply build or flash", !/auto[_-]?(?:apply|build|flash)/i.test(nativeAgyCommand)],
  ["native AGY auth check is registered and non-writing", nativeAgyCommand.includes("--auth-check-only") && nativeAgyCommand.includes("write_execution_count: 0") && nativeAgyCommand.includes('["--version"]')],
  ["native AGY auth check requires trusted attestation", nativeAgyCommand.indexOf("trusted_workspace_attestation_required") < nativeAgyCommand.indexOf("runAuthCheckOnly(executable")],
  ["native AGY auth check uses no credential files", !/(?:credential|cookie|oauth|token)[_-]?(?:file|path)/i.test(nativeAgyCommand)],
  ["native AGY auth check uses guarded roots", nativeAgyCommand.includes("guardActiveWorkspace") && nativeAgyCommand.includes("guardTrustedWorkspace")],
  ["native AGY auth check changes no workspace files", !nativeAgyCommand.includes("writeFileSync") && !nativeAgyCommand.includes("appendFileSync")],
  ["native AGY invocation matrix is sanitized", nativeAgyCore.includes("sanitizedInvocationMatrix") && !nativeAgyCore.includes("credential_path")],
  ["AGY scratch import command is registered", packageJson.scripts?.["qa:agy-scratch-import"] === "node scripts/qa-agy-scratch-import.mjs"],
  ["AGY scratch import requires real execution confirmation", scratchAgyCommand.includes("--confirm-real-agy") && scratchAgyCommand.includes("explicit_confirmation_required")],
  ["AGY scratch import requires trusted workspace attestation", scratchAgyCommand.includes("--trusted-workspace-attested") && scratchAgyCommand.includes("trusted_workspace_attestation_required")],
  ["AGY scratch import uses exact nonce filename", scratchAgyCore.includes("FORGEX_AGY_SCRATCH_SMOKE_${runId}_${nonce}.txt") && scratchAgyCore.includes("expectedArtifactPath")],
  ["AGY scratch import does not recursively scan scratch", !/readdirSync\([^\n]*scratch/i.test(scratchAgyCore) && !scratchAgyCore.includes("globSync")],
  ["AGY scratch import does not select newest files", !/(?:newest|last[_ -]?modified|mtime)/i.test(scratchAgyCore)],
  ["AGY scratch import does not persist raw instruction or output", !/writeFileSync\([^\n]*(?:instruction|stdout|stderr)|appendFileSync\([^\n]*(?:instruction|stdout|stderr)/i.test(scratchAgyCommand)],
  ["AGY scratch import blocks symlink and reparse escapes", scratchAgyCore.includes("isSymbolicLink") && scratchAgyCore.includes("realpathSync.native") && scratchAgyCore.includes("samePath(candidate, real)")],
  ["AGY scratch import enforces root containment", scratchAgyCore.includes("isInside(root, real)") && scratchAgyCore.includes("isInside(scratchRoot, candidate)")],
  ["AGY scratch import enforces smoke size limit", scratchAgyCore.includes("16 * 1024") && scratchAgyCore.includes("SCRATCH_ARTIFACT_TOO_LARGE")],
  ["AGY scratch import enforces nonce match", scratchAgyCore.includes("parsed.nonce !== model.nonce") && scratchAgyCore.includes("SCRATCH_ARTIFACT_NONCE_MISMATCH")],
  ["AGY scratch import keeps active workspace unchanged", scratchAgyCommand.includes("activeUnchanged") && scratchAgyCommand.includes("workspace_integrity_changed")],
  ["AGY scratch import keeps managed marker unchanged", scratchAgyCommand.includes("markerUnchanged") && scratchAgyCommand.includes("nativeBaseline")],
  ["AGY scratch import uses direct argv without shell", scratchAgyCommand.includes("spawn(executable, args") && scratchAgyCommand.includes("shell: false") && !scratchAgyCommand.includes("shell: true")],
  ["AGY scratch review has no workspace diff or apply authority", scratchAgyCore.includes("changed_files: []") && scratchAgyCore.includes("no apply authority")],
  ["AGY scratch import does not auto-apply build or flash", ["automatic_apply: false", "automatic_build: false", "automatic_flash: false"].every((marker) => scratchAgyCore.includes(marker))],
  ["AGY scratch import blocks dangerous permission bypass", scratchAgyCore.includes("--dangerously-skip-permissions") && scratchAgyCore.includes("dangerous_permission_flag_rejected")],
  ["AGY scratch project import command is registered", packageJson.scripts?.["qa:agy-scratch-project-import"] === "node scripts/qa-agy-scratch-project-import.mjs"],
  ["AGY scratch project import defaults disabled", envExample.includes("FORGEX_ENABLE_AGY_SCRATCH_IMPORT=0") && productAgentRoute.includes("AGY_SCRATCH_IMPORT_DISABLED")],
  ["AGY scratch project import does not execute AGY", !/\b(?:subprocess|Popen|spawn)\b/.test(agyProjectImport) && agyProjectImportQa.includes("backend.bridges.agy_scratch_project_import_cli") && !agyProjectImportCli.includes("subprocess")],
  ["AGY scratch project import requires explicit source", agyProjectImportQa.includes('indexOf("--source")') && agyProjectImportCli.includes('add_argument("--source", required=True)')],
  ["AGY scratch project import does not enumerate scratch root", agyProjectImport.includes("os.scandir(current)") && !agyProjectImport.includes("os.scandir(self.scratch_root)") && !agyProjectImport.includes("iterdir(self.scratch_root)")],
  ["AGY scratch project import does not select newest folder", !/(?:newest|last[_ -]?modified|mtime)/i.test(agyProjectImport + agyProjectImportCli + agyProjectImportQa)],
  ["AGY scratch project import enforces scratch containment and root rejection", agyProjectImport.includes("SOURCE_OUTSIDE_ROOT") && agyProjectImport.includes("SOURCE_IS_ROOT") && agyProjectImport.includes("is_strict_child")],
  ["AGY scratch project import blocks brain and sensitive roots", agyProjectImport.includes("_is_brain_path") && agyProjectImport.includes("_is_sensitive_exact")],
  ["AGY scratch project import blocks symlink and reparse", agyProjectImport.includes("FILE_ATTRIBUTE_REPARSE_POINT") && agyProjectImport.includes("SYMLINK_BLOCKED") && agyProjectImport.includes("is_link_or_reparse")],
  ["AGY scratch project import blocks secrets", agyProjectImport.includes("SECRET_PATTERNS") && ["auth.json", "credentials.json", "token.json", "*.pem"].every((value) => agyProjectImport.includes(value))],
  ["AGY scratch project import enforces file count and size limits", ["MAX_FILES = 200", "MAX_TOTAL_BYTES = 5 * 1024 * 1024", "MAX_FILE_BYTES = 512 * 1024", "MAX_DEPTH = 8"].every((value) => agyProjectImport.includes(value))],
  ["AGY scratch project import accepts text files only", agyProjectImport.includes("is_binary(content)") && agyProjectImport.includes("BINARY_FILE_BLOCKED")],
  ["AGY scratch project import copies only to managed sandbox", agyProjectImport.includes('repository_root / ".promptforge" / "agy-import-sandboxes"') && agyProjectImport.includes("safe_target(sandbox")],
  ["AGY scratch project import checks active workspace integrity", agyProjectImport.includes("snapshot_integrity(self.active_workspace_root)") && agyProjectImport.includes("ACTIVE_WORKSPACE_UNSAFE")],
  ["AGY scratch project import creates review only after exact validation", agyProjectImport.indexOf("changed = self.review_service.diff_snapshot") < agyProjectImport.indexOf("review = self.review_service.create_review") && agyProjectImport.includes('provider_id: str = "agy_scratch_import"') && agyProjectImport.includes("provider_id=context.provider_id")],
  ["AGY scratch project import does not auto apply build or flash", ["\"auto_apply\": False", "\"auto_build\": False", "\"auto_flash\": False"].every((value) => agyProjectImport.includes(value))],
  ["AGY scratch project import executes no competing providers", !/(?:agy\s+-p|codex\s+exec|\bopencode\b|\bclaude\b|api-provider-smoke)/i.test(agyProjectImport + agyProjectImportCli + agyProjectImportQa)],
  ["AGY scratch project import UI uses one pasted exact path", productAgentPanel.includes("AGY Scratch Import") && productAgentPanel.includes("importAGYScratchProject") && !/(?:newest|scratch folder list|scratch folders)/i.test(productAgentPanel)],
  ["AGY assisted runner command is registered", packageJson.scripts?.["qa:agy-assisted-runner"] === "node scripts/qa-agy-assisted-runner.mjs"],
  ["AGY assisted runner defaults disabled and requires both feature flags", envExample.includes("FORGEX_ENABLE_AGY_ASSISTED_RUNNER=0") && agyAssistedQa.includes("FORGEX_ENABLE_AGY_ASSISTED_RUNNER") && agyAssistedQa.includes("FORGEX_ENABLE_AGY_SCRATCH_IMPORT") && productAgentRoute.includes("AGY_ASSISTED_RUNNER_DISABLED")],
  ["AGY assisted runner requires explicit real confirmation", agyAssistedQa.includes("--confirm-real-agy") && agyAssistedQa.includes("AGY_ASSISTED_UNSAFE_ABORTED")],
  ["AGY assisted runner uses external invocation workspace", agyAssistedRunner.includes("forgex-agy-runs") && agyAssistedRunner.includes("AGY_INVOCATION_ROOT") && agyAssistedRunner.includes("AGY_INVOCATION_MARKER")],
  ["AGY assisted runner rejects repo and active cwd", agyAssistedRunner.includes("paths_overlap(root, self.repository_root)") && agyAssistedRunner.includes("paths_overlap(root, self.active_workspace_root)")],
  ["AGY assisted runner rejects sensitive cwd roots", agyAssistedRunner.includes("self.home_root / \"Desktop\"") && agyAssistedRunner.includes("OneDriveCommercial") && agyAssistedRunner.includes("agy_invocation_root_sensitive")],
  ["AGY assisted runner uses direct argv without shell", agyAssistedRunner.includes("shell=False") && agyAssistedQa.includes("shell: false") && !agyAssistedRunner.includes("shell=True") && !agyAssistedQa.includes("shell: true")],
  ["AGY assisted runner does not persist raw prompt or output", agyAssistedRunner.includes("result.to_safe_dict()") && !/write_text\([^\n]*(?:instruction|stdout|stderr)/i.test(agyAssistedRunner) && !/console\.(?:log|error)\([^\n]*(?:stdout|stderr|instruction)/i.test(agyAssistedQa)],
  ["AGY assisted runner does not trust stdout links", agyAssistedRunner.includes("version_result.stdout") && !agyAssistedRunner.includes("process.stdout") && !agyAssistedRunner.includes("process.stderr")],
  ["AGY assisted runner checks only exact expected folder", agyAssistedRunner.includes("expected_path = self.import_service.scratch_root / expected_name") && agyAssistedRunner.includes("if not expected_path.exists()")],
  ["AGY assisted runner does not scan scratch root or choose newest", !/(?:iterdir|glob\(|rglob\(|newest|last[_ -]?modified|mtime)/i.test(agyAssistedRunner + agyAssistedCli + agyAssistedQa)],
  ["AGY assisted runner reuses scratch import validation", agyAssistedRunner.includes("self.import_service.import_project") && agyAssistedRunner.includes("ImportReviewContext")],
  ["AGY assisted runner keeps active workspace unchanged", agyAssistedRunner.includes("active_before = snapshot_integrity(self.active_workspace_root)") && agyAssistedRunner.includes("active_workspace_unchanged=False")],
  ["AGY assisted runner review follows validation", agyAssistedRunner.includes('provider_id="agy_scratch_runner"') && agyProjectImport.indexOf("diff_snapshot") < agyProjectImport.indexOf("create_review")],
  ["AGY assisted runner provides manual fallback on exact-folder miss", agyAssistedRunner.includes("EXPECTED_FOLDER_MISSING") && agyAssistedRunner.includes("manual_import_fallback_available=True") && productAgentPanel.includes("Paste/select the generated AGY scratch folder")],
  ["AGY assisted runner does not auto apply build or flash", ["\"auto_apply\": False", "\"auto_build\": False", "\"auto_flash\": False"].every((value) => agyAssistedRunner.includes(value))],
  ["AGY assisted runner executes no competing providers", !/(?:codex\s+exec|\bopencode\b|\bclaude\b|api-provider-smoke)/i.test(agyAssistedRunner + agyAssistedCli + agyAssistedQa)],
  ["AGY assisted product route permits one safe template", productAgentRoute.includes('Literal["esp32-platformio-blink"]') && productAgentRoute.includes('Literal["Create an ESP32 blink project"]')],
  ["AGY assisted UI requires explicit click and exposes review", productAgentPanel.includes("Generate with AGY") && productAgentPanel.includes("Run AGY and Create Review") && productAgentPanel.includes("Open created review")],
  ["live AGY QA requires exact throwaway marker", liveAgyCore.includes('workspace/forgex-apply-test') && liveAgyCore.includes('QA_NOT_REAL_PROJECT.txt')],
  ["live AGY QA records active workspace baselines", liveAgyQa.includes("snapshotWorkspace") && liveAgyQa.includes("active_workspace_unchanged")],
  ["live AGY output validation rejects unsafe fields", liveAgyCore.includes("FORBIDDEN_PUBLIC_KEYS") && liveAgyQa.includes("assertSanitizedPublicPayload")],
  ["live AGY has no shell execution", !liveAgyQa.includes("shell: true") && !liveAgyCore.includes("shell: true") && agyRunner.includes('"shell": False')],
  ["AGY instructions are not written to sandbox files", !agyRunner.includes("prompt_path") && agyRunner.includes('args = [executable, "-p", prompt]')],
  ["AGY cwd is the managed sandbox", agyRunner.includes('cwd=sandbox_root') && agyRunner.includes('working_directory_identity = "sandbox"')],
  ["AGY zero-change runs fail safely", agyRunner.includes('run.status = "completed_no_changes"') && agyAdapter.includes("NO_CHANGES_PRODUCED")],
  ["AGY provider output is not persisted", agyRunner.includes("run.stdout_preview = None") && agyRunner.includes("run.stderr_preview = None")],
  ["live AGY never auto-applies builds or flashes", ["automatic_apply: false", "automatic_build: false", "automatic_flash: false"].every((marker) => liveAgyQa.includes(marker))],
  ["live AGY cancellation and timeout remain separately confirmed", liveAgyQa.includes("--cancel-smoke") && liveAgyQa.includes("--timeout-smoke") && liveAgyQa.includes("--confirm-real-agy")],
  ["live AGY QA is registered", packageJson.scripts?.["qa:agy-generic-live"] === "node scripts/qa-agy-generic-live.mjs"],
  ["Codex detection command is registered", packageJson.scripts?.["qa:codex-detect"] === "node scripts/qa-codex-detect.mjs"],
  ["Codex OAuth login QA command is registered", packageJson.scripts?.["qa:codex-oauth-bridge"] === "node scripts/qa-codex-oauth-bridge.mjs"],
  ["Codex login launcher requires explicit confirmation", codexLoginBackend.includes("confirm_launch_codex_login is not True") && codexLoginQa.includes("--confirm-launch-codex-login") && productAgentRoute.includes("confirm_launch_codex_login")],
  ["Codex login launcher uses official direct argv only", codexLoginBackend.includes('[executable.path, *executable.prefix_args, "login"]') && codexLoginBackend.includes('"shell": False') && codexLoginQa.includes('[...status.launcher.prefixArgs, "login"]') && codexLoginQa.includes("shell: false")],
  ["Codex login launcher uses a neutral temporary cwd", codexLoginBackend.includes('prefix="forgex-codex-login-"') && codexLoginQa.includes('"forgex-codex-login-"') && !codexLoginBackend.includes("active_workspace")],
  ["Codex login launcher discards raw process output", codexLoginBackend.includes('"stdout": subprocess.DEVNULL') && codexLoginBackend.includes('"stderr": subprocess.DEVNULL') && codexLoginQa.includes('stdio: "ignore"')],
  ["Codex login launcher reads no auth or token files", !/(?:auth\.json|[~\\/]\.codex|refresh_token|access_token|token_(?:path|file)|credential_(?:path|file))/i.test(codexLoginBackend + codexLoginQa)],
  ["Codex login launcher implements no OAuth callback or cookie handling", !/(?:oauth[_ -]?callback|callback[_ -]?url|browser[_ -]?callback|cookie)/i.test(codexLoginBackend + codexLoginQa)],
  ["Codex login launcher never asks for pasted auth material", !/(?:paste|input)[^\n]*(?:oauth|token|cookie)/i.test(codexLoginBackend + codexLoginQa)],
  ["Codex login launcher persists no auth material or raw output", !/(?:write_text|write_bytes|writeFile|appendFile)[^\n]*(?:token|stdout|stderr)/i.test(codexLoginBackend + codexLoginQa)],
  ["Codex OAuth routes keep production routing disabled", productAgentRoute.includes('/providers/codex-oauth/status') && productAgentRoute.includes('/providers/codex-oauth/login/launch') && codexLoginBackend.includes('"production_routing_enabled": False')],
  ["Codex status uses one shared aligned service", codexLoginBackend.includes("CodexStatusService") && codexLoginQa.includes("getSharedAlignedStatus") && productProviderRegistry.includes("_codex_status_service.status()") && codexOAuthSmokeBackend.includes("login_service.status()")],
  ["Codex UI status route uses the selected shared runner", productAgentRoute.includes("_codex_login_service(request).status()") && modelSettingsPanel.includes("status_runner") && modelSettingsPanel.includes("resolved_executable_runner")],
  ["Codex safe environment preserves required Windows user variables", ["USERPROFILE", "APPDATA", "LOCALAPPDATA", "PATH", "Path", "SystemRoot", "TEMP", "TMP"].every((name) => codexStatusBackend.includes(`\"${name}\"`) && codexSafeEnv.includes(`\"${name}\"`))],
  ["Codex safe environment excludes secret-like variable names", ["KEY", "TOKEN", "SECRET", "PASSWORD", "COOKIE", "CREDENTIAL", "AUTH"].every((name) => codexStatusBackend.includes(`\"${name}\"`) && codexSafeEnv.includes(name)) && !codexStatusBackend.includes("OPENAI_API_KEY") && !codexSafeEnv.includes("OPENAI_API_KEY")],
  ["Codex status uses shell false and a neutral temporary cwd", codexStatusBackend.includes("shell=False") && codexStatusBackend.includes('prefix=\"forgex-codex-status-\"') && codexStatusBackend.includes('"cwd_kind": "neutral_temp"')],
  ["Codex status persists no raw output", codexStatusBackend.includes('"raw_output_persisted": False') && !/(?:write_text|write_bytes|open\()[^\n]*(?:stdout|stderr)/i.test(codexStatusBackend)],
  ["Codex status diagnostics are sanitized", codexLoginQa.includes("--status-diagnostics") && codexLoginQa.includes("raw_output_persisted = false") && !codexLoginQa.slice(codexLoginQa.indexOf("function printDiagnostics"), codexLoginQa.indexOf("function printStatusParity")).includes("process.env")],
  ["Codex status parity cannot launch login or smoke", codexLoginQa.includes("--status-parity") && codexLoginQa.includes("smoke_executed = false") && codexLoginQa.includes("login_launched = false")],
  ["Codex session parity command is registered and status-only", codexLoginQa.includes("--session-parity") && codexSessionParity.includes('["login", "status"]') && !codexSessionParity.includes('"exec"')],
  ["Codex session parity never launches login", !/\[\s*"login"\s*\](?!\s*,\s*"status")/.test(codexSessionParity) && !codexSessionParity.includes("launchLogin")],
  ["Codex session parity persists no raw process output", codexSessionParity.includes("raw_output_persisted: false") && !/(?:writeFileSync|appendFileSync)[^\n]*(?:stdout|stderr)/i.test(codexSessionParity)],
  ["Codex session parity prints no full environment or launcher path", !codexLoginQa.includes("console.log(process.env") && !codexLoginQa.includes("candidate}") && codexSessionParity.includes("primary_launcher_path_hash")],
  ["Codex status parser recognizes ChatGPT and checks negatives first", codexSessionParity.indexOf("NEGATIVE.test") < codexSessionParity.indexOf("flags.contains_logged_in_phrase") && codexSessionParity.includes("CHATGPT.test")],
  ["Codex session parity reads no CLI session storage", !/readFileSync[^\n]*(?:codex|session|credential)/i.test(codexSessionParity)],
  ["Codex session parity uses a neutral temporary cwd", codexSessionParity.includes("forgex-codex-parity-") && codexSessionParity.includes('cwd_kind: "neutral_temp"')],
  ["Codex OAuth smoke requires explicit confirmation", codexLoginQa.includes('--standalone-smoke') && codexLoginQa.includes('--confirm-real-codex') && codexLoginQa.includes('CODEX_OAUTH_SMOKE_CONFIRMATION_REQUIRED')],
  ["Codex OAuth smoke refuses unless official status is ready", codexLoginQa.includes('if (!status.ready)') && codexLoginQa.includes('CODEX_OAUTH_SMOKE_LOGIN_REQUIRED') && codexLoginQa.includes('CODEX_OAUTH_SMOKE_STATUS_UNKNOWN')],
  ["Codex OAuth smoke uses the external disposable root", codexOAuthSmokeCore.includes('forgex-codex-oauth-smoke') && codexOAuthSmokeCore.includes('external_disposable_oauth_smoke')],
  ["Codex OAuth smoke blocks repo active home Desktop OneDrive and root", ["sandbox_repository_root_rejected", "sandbox_active_workspace_rejected", "sandbox_sensitive_root_rejected"].every((value) => codexOAuthSmokeCore.includes(value)) && ["Desktop", "OneDrive", "path.parse(candidate).root"].every((value) => codexOAuthSmokeCore.includes(value))],
  ["Codex OAuth smoke rejects symlink and reparse escapes", codexOAuthSmokeCore.includes("rejectLinksAndSpecialEntries") && codexOAuthSmokeCore.includes("sandbox_reparse_rejected")],
  ["Codex OAuth smoke uses direct argv and shell false", codexLoginQa.includes("spawnSync(status.launcher.command") && codexLoginQa.includes("shell: false") && !codexLoginQa.includes("shell: true")],
  ["Codex OAuth smoke uses global approval before exec", codexOAuthSmokeCore.indexOf('"--ask-for-approval", "never"') < codexOAuthSmokeCore.indexOf('"exec"')],
  ["Codex OAuth smoke uses workspace-write and sandbox cd", codexOAuthSmokeCore.includes('"--sandbox", "workspace-write", "--cd", sandboxPath')],
  ["Codex OAuth smoke blocks dangerous flags", ["--dangerously-bypass-approvals-and-sandbox", "--yolo", "--full-auto", "danger-full-access"].every((value) => codexOAuthSmokeCore.includes(value))],
  ["Codex OAuth smoke persists no raw prompt stdout or stderr", !/writeFileSync\([^\n]*(?:smokePrompt|stdout|stderr)|appendFileSync\([^\n]*(?:smokePrompt|stdout|stderr)/i.test(codexLoginQa + codexOAuthSmokeCore) && codexOAuthSmokeCore.includes("raw_prompt_persisted: false") && codexOAuthSmokeCore.includes("raw_output_persisted: false")],
  ["Codex OAuth content diagnostics expose hashes and no raw content", codexOAuthSmokeCore.includes("actual_content_hash") && codexOAuthSmokeCore.includes("raw_content_printed: false") && !/console\.(?:log|error)\([^\n]*(?:text|normalizedForMatch|actualBytes)/i.test(codexLoginQa + codexOAuthSmokeCore)],
  ["Codex OAuth content validator only normalizes BOM CRLF and EOF LF", codexOAuthSmokeCore.includes('raw.startswith') === false && codexOAuthSmokeCore.includes("0xef") && codexOAuthSmokeCore.includes('replace(/\\r\\n/g, "\\n")') && codexOAuthSmokeCore.includes('replace(/\\n+$/, "")') && !codexOAuthSmokeCore.includes(".trim()")],
  ["Codex OAuth content validator keeps text markdown and quotes strict", codexOAuthSmokeCore.includes("normalizedForMatch === OAUTH_SMOKE_CONTENT") && !/replace\([^\n]*(?:```|markdown|quotes?)/i.test(codexOAuthSmokeCore)],
  ["Codex OAuth inspect is metadata-only and executes no Codex", codexLoginQa.includes("--inspect-last-smoke-content") && codexOAuthSmokeCore.includes("inspectKnownOAuthSmokeContent") && codexOAuthSmokeCore.includes("codex_executed: false")],
  ["Codex OAuth inspect is contained and rejects links", codexOAuthSmokeCore.includes("path.join(managedRoot, metadata.run_id)") && codexOAuthSmokeCore.includes("guardOAuthSandbox") && codexOAuthSmokeCore.includes("isSymbolicLink()")],
  ["Codex OAuth strict prompt variant v2 is active", codexOAuthSmokeCore.includes('strict_single_line_v2') && codexOAuthSmokeCore.includes("Do not add a second line.") && codexOAuthSmokeCore.includes("no quotes and no markdown")],
  ["Codex OAuth final pass predicate accepts normalized valid content", codexOAuthSmokeCore.includes("isSafeOAuthSmokePass") && codexOAuthSmokeCore.includes("payload.expected_content_valid === true") && codexOAuthSmokeCore.includes("payload.normalized_content_matches === true")],
  ["Codex OAuth final pass predicate ignores raw byte count", !codexOAuthSmokeCore.slice(codexOAuthSmokeCore.indexOf("export function isSafeOAuthSmokePass"), codexOAuthSmokeCore.indexOf("export function sanitizedSmokeResult")).includes("byte_count")],
  ["Codex OAuth known process failures precede artifact pass", codexOAuthSmokeCore.indexOf("CODEX_OAUTH_SMOKE_USAGE_LIMIT_REACHED") < codexOAuthSmokeCore.indexOf('if (exact && expectedExists && expectedValid') && codexOAuthSmokeCore.indexOf('if (exact && expectedExists && expectedValid') < codexOAuthSmokeCore.indexOf('if (status !== 0)')],
  ["Codex OAuth backend independently verifies pass metadata", codexOAuthSmokeBackend.includes("_is_safe_pass") && codexOAuthSmokeBackend.includes('payload["classification"] = "CODEX_OAUTH_SMOKE_UNKNOWN_SAFE_FAILURE"')],
  ["Codex OAuth review gate runs only after validated pass", codexLoginQa.includes('if (classification === "CODEX_OAUTH_SMOKE_PASS")') && codexLoginQa.includes("isSafeOAuthSmokePass(passCandidate)")],
  ["Codex OAuth smoke reads no auth file or browser callback", !/auth\.json|browser[_ -]?callback|callback[_ -]?url/i.test(codexLoginQa + codexOAuthSmokeCore + codexOAuthSmokeBackend + codexOAuthSmokeReview)],
  ["Codex OAuth smoke persists no tokens", !/(?:writeFileSync|write_text|open\()[^\n]*(?:access_token|refresh_token|id_token|oauth code)/i.test(codexLoginQa + codexOAuthSmokeCore + codexOAuthSmokeBackend + codexOAuthSmokeReview)],
  ["Codex OAuth smoke creates review only after exact pass", codexLoginQa.includes('classification === "CODEX_OAUTH_SMOKE_PASS"') && codexOAuthSmokeReview.includes('entries != [SMOKE_NAME, MARKER_NAME]') && codexOAuthSmokeReview.includes('classification": "CODEX_OAUTH_SMOKE_PASS')],
  ["Codex OAuth smoke does not auto apply build or flash", ["\"auto_apply\": False", "\"auto_build\": False", "\"auto_flash\": False"].every((value) => codexOAuthSmokeReview.includes(value))],
  ["Codex OAuth production routing remains disabled", codexOAuthSmokeReview.includes('"production_routing_enabled": False') && codexOAuthSmokeCore.includes("production_routing_enabled: false")],
  ["Codex OAuth UI gates smoke and requires confirmation", modelSettingsPanel.includes("Run Sandboxed Smoke") && modelSettingsPanel.includes("codexOAuthStatus.auth_status !== \"signed_in\"") && modelSettingsPanel.includes("dialogs.confirmAction")],
  ["Codex OAuth UI opens review only after pass", modelSettingsPanel.includes('codexOAuthSmoke.classification === "CODEX_OAUTH_SMOKE_PASS"') && modelSettingsPanel.includes("Open Review")],
  ["Codex OAuth smoke executes no AGY Claude or OpenCode", !/\bagy\b|\bclaude\b|\bopencode\b/i.test(codexLoginQa + codexOAuthSmokeCore + codexOAuthSmokeReview)],
  ["Codex OAuth smoke executes no API provider", !/qa-api-provider|api_product_runtime|ApiPlannerProvider|openai_api_provider/i.test(codexLoginQa + codexOAuthSmokeCore + codexOAuthSmokeReview)],
  ["native Codex command is registered", packageJson.scripts?.["qa:codex-native-write"] === "node scripts/qa-codex-native-write.mjs"],
  ["native Codex requires explicit confirmation", nativeCodexCommand.includes("--confirm-real-codex") && nativeCodexCommand.includes("explicit_confirmation_required")],
  ["native Codex requires subscription bridge retry gate", nativeCodexCommand.includes("--subscription-bridge-retry") && nativeCodexCommand.includes("subscription_bridge_retry_required")],
  ["native Codex requires manual standalone attestation", nativeCodexCommand.includes("manualStandaloneAttested") && nativeCodexCommand.includes("manual_standalone_attestation_required")],
  ["Codex provider flags default disabled", envExample.includes("FORGEX_ENABLE_CODEX_BRIDGE=0") && envExample.includes("FORGEX_ENABLE_CODEX_GENERIC_PROVIDER=0")],
  ["Codex generic adapter was not enabled after failed native smoke", !appComposition.includes("register(codex_adapter)") && genericApiSchemas.includes('Literal["agy"]')],
  ["native Codex uses external managed sandbox only", nativeCodexCore.includes('C:\\\\forgex-codex-sandboxes') && nativeCodexCommand.includes("CODEX_EXTERNAL_SANDBOX_ROOT") && nativeCodexCommand.includes("cwd: guarded.sandboxRoot")],
  ["native Codex rejects repo active and sensitive roots", ["sandbox_repository_root_rejected", "sandbox_active_workspace_rejected", "sandbox_sensitive_root_rejected"].every((value) => nativeCodexCore.includes(value))],
  ["native Codex rejects link and reparse escapes", nativeCodexCore.includes("rejectLinksAndSpecialEntries") && nativeCodexCore.includes("sandbox_link_rejected") && nativeCodexCore.includes("sandbox_reparse_rejected")],
  ["native Codex uses global approval before exec", nativeCodexCore.indexOf('"--ask-for-approval", "never"') < nativeCodexCore.indexOf('"exec"')],
  ["native Codex uses exec before workspace sandbox and cd", nativeCodexCore.indexOf('"exec"') < nativeCodexCore.indexOf('"--sandbox", "workspace-write"') && nativeCodexCore.indexOf('"--sandbox", "workspace-write"') < nativeCodexCore.indexOf('"--cd", sandboxPath')],
  ["native Codex keeps runtime instruction final positional", nativeCodexCore.includes("args.at(-1)") && nativeCodexCore.includes("instruction,")],
  ["native Codex omits retry extras", ["--ignore-user-config", "--ephemeral", "--skip-git-repo-check"].every((flag) => nativeCodexCore.includes(flag)) && nativeCodexCore.includes("subscription_retry_extra_flag_rejected")],
  ["native Codex uses direct argv without shell", nativeCodexCommand.includes("spawnSync(launcher.command") && nativeCodexCommand.includes("shell: false") && !nativeCodexCommand.includes("shell: true")],
  ["native Codex blocks dangerous bypasses", ["--dangerously-bypass-approvals-and-sandbox", "--yolo", "--full-auto", "danger-full-access"].every((flag) => nativeCodexCore.includes(flag)) && nativeCodexCore.includes("dangerous_codex_flag_rejected")],
  ["native Codex keeps prompt and output runtime-only", !/writeFileSync\([^\n]*(?:instruction|stdout|stderr)|appendFileSync\([^\n]*(?:instruction|stdout|stderr)/i.test(nativeCodexCommand) && !/console\.(?:log|error)\([^\n]*(?:stdout|stderr)/.test(nativeCodexCommand)],
  ["native Codex does not inspect credential files", !/(?:credential|cookie|oauth|token)[_-]?(?:file|path)/i.test(nativeCodexCommand + nativeCodexCore + codexDetect)],
  ["native Codex classifies usage quota auth and permission separately", ["CODEX_NATIVE_USAGE_LIMIT_REACHED", "CODEX_NATIVE_QUOTA_EXCEEDED", "CODEX_NATIVE_AUTH_BLOCKED", "CODEX_NATIVE_PERMISSION_BLOCKED"].every((value) => nativeCodexCore.includes(value))],
  ["Codex review is exact-pass only", nativeCodexCommand.includes('classification === "CODEX_SUBSCRIPTION_BRIDGE_PASS"') && codexSubscriptionReview.includes('provider_id="codex_cli_subscription"') && codexSubscriptionReview.includes('entries != [SMOKE_NAME, MARKER_NAME]')],
  ["Codex review metadata disables apply build flash", ["\"auto_apply\": False", "\"auto_build\": False", "\"auto_flash\": False"].every((value) => codexSubscriptionReview.includes(value))],
  ["Codex product entry remains experimental QA only and non-routeable", productProviderRegistry.includes('"codex_cli_subscription"') && productProviderRegistry.includes('experimental=True') && productProviderRegistry.includes('production_eligible=False') && productProviderRegistry.includes('product_routing_enabled=False') && productProviderRegistry.includes('qa_only=True')],
  ["native Codex never auto-applies builds or flashes", !/auto[_-]?(?:apply|build|flash)/i.test(nativeCodexCommand)],
  ["AGY remains paused for Codex phase", !nativeCodexCommand.includes("agy") && !nativeCodexCore.includes("agy")],
  ["Codex phase does not execute Claude or OpenCode", !/(?:claude|opencode)/i.test(nativeCodexCommand + nativeCodexCore)],
  ["QA launcher script is registered", packageJson.scripts?.["dev:desktop:qa"] === "node scripts/dev-desktop-qa.mjs"],
  ["QA workspace script is registered", packageJson.scripts?.["qa:create-apply-workspace"] === "node scripts/create-apply-qa-workspace.mjs"],
  ["packaged QA script is registered", packageJson.scripts?.["qa:desktop:packaged"] === "node scripts/qa-packaged-desktop.mjs"],
  ["packaged QA overrides managed state root", packagedQa.includes("PROMPTFORGE_ROOT: isolatedRoot")],
  ["packaged QA cleanup is containment checked", packagedQa.includes("assertContained") && packagedHelpers.includes("path.relative")],
  ["packaged QA fixtures require QA mode", packagedQa.includes("qaFixturesAllowed(process.env)")],
  ["packaged generic API flags stay disabled", ["FORGEX_ENABLE_GENERIC_BRIDGE_API", "FORGEX_ENABLE_GENERIC_BRIDGE_ROUTING", "FORGEX_ENABLE_AGY_GENERIC_PROVIDER", "FORGEX_ENABLE_AGY_GENERIC_CUTOVER"].every((flag) => packagedQa.includes(`${flag}: \"0\"`))],
  ["packaged readiness evidence is phase scoped", packagedQa.includes("phase-2-5-8-5-1-packaged-readiness.json")],
  ["packaged diagnostics sanitize paths and secrets", packagedQa.includes("sanitizeDiagnostic") && packagedQa.includes("last_backend_stderr_preview") && packagedQa.includes("<qa-package>")],
  ["Agent visual fixtures require QA mode", genericApi.includes('os.getenv("FORGEX_QA_MODE"') && genericApi.includes("QA_FIXTURE_STATES")],
  ["Agent visual fixtures cannot submit providers", !genericApi.slice(genericApi.indexOf("generic_agent_qa_fixture"), genericApi.indexOf("def _policy")).includes("start_run(")],
];

let failed = false;
for (const [label, passed] of checks) {
  console.log(`${passed ? "PASS" : "FAIL"} ${label}`);
  failed ||= !passed;
}

const forbiddenTrue = [
  /"generic_bridge_routing_enabled": True/,
  /"agy_generic_provider_enabled": True/,
  /"agy_generic_adapter_enabled": True/,
  /"public_generic_run_api_enabled": True/,
  /"bridge_routing_enabled": True/,
  /"codex_execution_enabled": True/,
  /"claude_execution_enabled": True/,
  /"opencode_execution_enabled": True/,
  /"auto_apply_enabled": True/,
  /"auto_build_after_apply": True/,
  /"auto_flash_after_apply": True/,
];
for (const pattern of forbiddenTrue) {
  if (pattern.test(safetyRoute)) {
    console.log(`FAIL forbidden enabled safety flag matched: ${pattern}`);
    failed = true;
  }
}

if (failed) process.exit(1);
console.log("Safety scan passed. This helper does not replace automated tests.");
