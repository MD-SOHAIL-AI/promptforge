export type AgentSlashCommandScope = "server" | "local";

export interface AgentSlashCommandDefinition {
  name: string;
  usage: string;
  description: string;
  scope: AgentSlashCommandScope;
}

export const AGENT_SLASH_COMMANDS: readonly AgentSlashCommandDefinition[] = [
  { name: "help", usage: "/help", description: "Show available Agent commands", scope: "local" },
  { name: "agent", usage: "/agent <task>", description: "Run a normal Agent task", scope: "server" },
  { name: "plan", usage: "/plan <task>", description: "Create a read-only implementation plan", scope: "server" },
  { name: "subagent", usage: "/subagent <explore|review|verify> <task>", description: "Delegate a bounded specialist task", scope: "server" },
  { name: "explore", usage: "/explore <task>", description: "Delegate repository investigation", scope: "server" },
  { name: "review", usage: "/review [scope]", description: "Delegate a correctness and risk review", scope: "server" },
  { name: "verify", usage: "/verify [scope]", description: "Delegate read-only build verification", scope: "server" },
  { name: "build", usage: "/build [target]", description: "Build without flashing hardware", scope: "server" },
  { name: "repair", usage: "/repair [failure]", description: "Repair and rebuild the latest failure", scope: "server" },
  { name: "simulate", usage: "/simulate [behavior]", description: "Build and run virtual hardware validation", scope: "server" },
  { name: "flash", usage: "/flash", description: "Prepare a verified build for confirmation", scope: "server" },
  { name: "status", usage: "/status", description: "Show the current run and model state", scope: "local" },
  { name: "retry", usage: "/retry", description: "Retry a paused run on the same model", scope: "local" },
  { name: "cancel", usage: "/cancel", description: "Stop the active run", scope: "local" },
  { name: "model", usage: "/model", description: "Open the model picker", scope: "local" },
] as const;

export interface ParsedAgentSlashCommand {
  definition: AgentSlashCommandDefinition;
  arguments: string;
}

export function parseAgentSlashCommand(value: string): ParsedAgentSlashCommand | null {
  const text = value.trim();
  if (!text.startsWith("/")) return null;
  const match = /^\/([a-z][a-z0-9_-]*)(?:\s+([\s\S]*))?$/i.exec(text);
  if (!match) throw new Error("Slash command is malformed.");
  const definition = AGENT_SLASH_COMMANDS.find(
    (candidate) => candidate.name === match[1].toLowerCase(),
  );
  if (!definition) throw new Error(`Unknown command /${match[1]}. Use /help to see available commands.`);
  const argumentsValue = (match[2] ?? "").trim();
  if (["agent", "plan", "explore"].includes(definition.name) && !argumentsValue)
    throw new Error(`${definition.usage} requires a task.`);
  if (definition.name === "subagent") {
    const role = argumentsValue.split(/\s+/, 1)[0]?.toLowerCase();
    const task = argumentsValue.slice(role?.length ?? 0).trim();
    if (!role || !["explore", "review", "verify"].includes(role) || !task)
      throw new Error("Use /subagent <explore|review|verify> <task>.");
  }
  if (["help", "status", "retry", "cancel", "model", "flash"].includes(definition.name) && argumentsValue)
    throw new Error(`${definition.usage} does not accept arguments.`);
  return { definition, arguments: argumentsValue };
}

export function slashCommandSuggestions(value: string): readonly AgentSlashCommandDefinition[] {
  const text = value.trimStart();
  if (!text.startsWith("/") || /\s/.test(text)) return [];
  const query = text.slice(1).toLowerCase();
  return AGENT_SLASH_COMMANDS.filter((command) => command.name.startsWith(query));
}
