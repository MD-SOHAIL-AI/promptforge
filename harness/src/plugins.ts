import type { JsonObject } from "./contracts";

export interface PluginManifest {
  id: string;
  version: string;
  apiVersion: 1;
  entrypoint: string;
  capabilities: string[];
  settings?: JsonObject;
  bundled?: boolean;
}

export interface PluginStatus {
  manifest: Readonly<PluginManifest>;
  trusted: boolean;
  enabled: boolean;
  reason: string | null;
}

export class PluginRegistry {
  private readonly plugins = new Map<string, PluginStatus>();

  install(raw: PluginManifest, trustedCapabilities: ReadonlySet<string>): Readonly<PluginStatus> {
    const manifest = validate(raw);
    if (this.plugins.has(manifest.id)) throw new Error("PLUGIN_DUPLICATE");
    const bundled = manifest.bundled === true;
    const trusted = bundled || manifest.capabilities.every((item) => trustedCapabilities.has(item));
    const status = Object.freeze({ manifest, trusted, enabled: trusted, reason: trusted ? null : "permissions_required" });
    this.plugins.set(manifest.id, status);
    return status;
  }

  list(): ReadonlyArray<Readonly<PluginStatus>> {
    return [...this.plugins.values()].sort((left, right) => left.manifest.id.localeCompare(right.manifest.id));
  }

  remove(id: string): boolean {
    return this.plugins.delete(id);
  }
}

function validate(raw: PluginManifest): Readonly<PluginManifest> {
  if (!/^[a-z][a-z0-9.-]{1,127}$/.test(raw.id)) throw new Error("PLUGIN_ID_INVALID");
  if (!/^\d+\.\d+\.\d+(?:-[a-z0-9.-]+)?$/i.test(raw.version)) throw new Error("PLUGIN_VERSION_INVALID");
  if (raw.apiVersion !== 1 || !raw.entrypoint || raw.entrypoint.includes("..") || /^[\\/]|^[a-z]:/i.test(raw.entrypoint)) throw new Error("PLUGIN_ENTRYPOINT_INVALID");
  if (!Array.isArray(raw.capabilities) || new Set(raw.capabilities).size !== raw.capabilities.length || raw.capabilities.some((item) => !/^[a-z][a-z0-9._-]{0,63}$/.test(item))) throw new Error("PLUGIN_CAPABILITIES_INVALID");
  return Object.freeze({ ...structuredClone(raw), capabilities: Object.freeze([...raw.capabilities]) as unknown as string[] });
}
