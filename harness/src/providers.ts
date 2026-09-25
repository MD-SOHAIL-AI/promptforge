import type { ProviderDescriptor } from "./contracts";

export const PROVIDERS: readonly ProviderDescriptor[] = Object.freeze([
  { id: "deepseek", displayName: "DeepSeek", protocol: "deepseek", baseUrl: "https://api.deepseek.com", local: false, credentialRequired: true, modelDiscovery: true },
  { id: "openai", displayName: "OpenAI", protocol: "openai-responses", baseUrl: "https://api.openai.com/v1", local: false, credentialRequired: true, modelDiscovery: true },
  { id: "anthropic", displayName: "Anthropic", protocol: "anthropic-messages", baseUrl: "https://api.anthropic.com", local: false, credentialRequired: true, modelDiscovery: false },
  { id: "openrouter", displayName: "OpenRouter", protocol: "openai-completions", baseUrl: "https://openrouter.ai/api/v1", local: false, credentialRequired: true, modelDiscovery: true },
  { id: "groq", displayName: "Groq", protocol: "openai-completions", baseUrl: "https://api.groq.com/openai/v1", local: false, credentialRequired: true, modelDiscovery: true },
  { id: "nvidia_nim", displayName: "NVIDIA NIM", protocol: "openai-completions", baseUrl: "https://integrate.api.nvidia.com/v1", local: false, credentialRequired: true, modelDiscovery: true },
  { id: "gemini", displayName: "Google Gemini", protocol: "openai-completions", baseUrl: "https://generativelanguage.googleapis.com/v1beta/openai", local: false, credentialRequired: true, modelDiscovery: true },
  { id: "ollama", displayName: "Ollama", protocol: "openai-completions", baseUrl: "http://127.0.0.1:11434/v1", local: true, credentialRequired: false, modelDiscovery: true },
  { id: "lmstudio", displayName: "LM Studio", protocol: "openai-completions", baseUrl: "http://127.0.0.1:1234/v1", local: true, credentialRequired: false, modelDiscovery: true },
]);
