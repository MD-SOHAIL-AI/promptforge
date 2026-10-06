import { build } from "esbuild";
import path from "node:path";
import { readFile, readdir, writeFile } from "node:fs/promises";

const catalog = JSON.parse(await readFile("backend/model_router/data/models-dev.json", "utf8"));
const descriptors = Object.values(catalog).map(provider => ({
  id: provider.id, displayName: provider.name,
  protocol: ({ "@ai-sdk/openai": "openai-responses", "@ai-sdk/anthropic": "anthropic-messages", "@ai-sdk/google": "gemini", "@ai-sdk/azure": "azure", "@ai-sdk/amazon-bedrock": "bedrock-converse" })[provider.npm] ?? (["@ai-sdk/openai-compatible", "@openrouter/ai-sdk-provider", "@ai-sdk/deepinfra", "@ai-sdk/groq", "@ai-sdk/cerebras", "@ai-sdk/togetherai", "@ai-sdk/xai"].includes(provider.npm) ? "openai-completions" : "sdk"),
  baseUrl: provider.api ?? "", local: provider.id === "lmstudio", credentialRequired: provider.id !== "lmstudio", modelDiscovery: true,
}));
await writeFile("harness/src/opencode-providers.generated.ts", '// Generated from the pinned OpenCode Models.dev catalog by build-model-router.mjs.\nimport type { ProviderDescriptor } from "./contracts";\nexport const OPENCODE_PROVIDERS: readonly ProviderDescriptor[] = ' + JSON.stringify(descriptors, null, 2) + ';\n');

// A standalone bundle lets the Python backend use the same engine in source
// checkouts and packaged Electron applications without runtime npm installs.
const result = await build({
  entryPoints: ["model-router/src/worker.mjs", "model-router/src/engine.mjs"],
  outdir: "dist/model-router",
  outExtension: { ".js": ".cjs" },
  bundle: true,
  platform: "node",
  target: "node22",
  format: "cjs",
  legalComments: "linked",
  metafile: true,
  alias: { "@opencode-ai/schema/llm": path.resolve("third_party/opencode/packages/schema/src/llm.ts") },
});

// Preserve licenses for every npm package actually included in the bundle.
const packages = new Set();
for (const input of Object.keys(result.metafile.inputs)) {
  const absolute = path.resolve(input).replaceAll("\\", "/");
  const matches = [...absolute.matchAll(/\/node_modules\/(?:@[^/]+\/)?[^/]+\//g)];
  const match = matches.at(-1);
  if (match) packages.add(absolute.slice(0, match.index + match[0].length - 1));
}
const notices = await Promise.all([...packages].sort().map(async directory => {
  const manifest = JSON.parse(await readFile(path.join(directory, "package.json"), "utf8"));
  const entries = await readdir(directory, { withFileTypes: true });
  const files = entries.filter(entry => entry.isFile() && /^(?:licen[cs]e|notice|copying)(?:\.|$)/i.test(entry.name));
  const texts = await Promise.all(files.map(entry => readFile(path.join(directory, entry.name), "utf8")));
  return `\n## ${manifest.name}@${manifest.version}\n\nLicense: ${manifest.license ?? "See package notice"}\n\n${texts.join("\n\n")}`;
}));
await writeFile("dist/model-router/THIRD_PARTY_NOTICES.md", "# Bundled model engine notices\n\nOpenCode's MIT license is preserved in third_party/opencode/LICENSE.\n" + notices.join("\n"));
