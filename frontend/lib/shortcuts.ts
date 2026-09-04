"use client";

import { useEffect } from "react";

export type ShortcutHandler = (event: KeyboardEvent) => void;

export interface ShortcutDefinition {
  id: string;
  combo: string;
  handler: ShortcutHandler;
  allowInInput?: boolean;
  description?: string;
}

interface ParsedCombo {
  mod: boolean;
  ctrl: boolean;
  alt: boolean;
  shift: boolean;
  meta: boolean;
  key: string;
}

interface RegistryEntry {
  parsed: ParsedCombo;
  definition: ShortcutDefinition;
}

const KEY_ALIASES: Record<string, string> = {
  esc: "escape",
  del: "delete",
  ins: "insert",
  return: "enter",
  spacebar: " ",
  space: " ",
  plus: "+",
  comma: ",",
};

const isMac = typeof navigator !== "undefined" && /mac|iphone|ipad|ipod/i.test(navigator.userAgent);

const registry = new Map<string, RegistryEntry>();
let listening = false;

function parseCombo(combo: string): ParsedCombo | null {
  const tokens = combo.toLowerCase().split("+").map((token) => token.trim()).filter(Boolean);
  if (tokens.length === 0) return null;
  const parsed: ParsedCombo = { mod: false, ctrl: false, alt: false, shift: false, meta: false, key: "" };
  for (const token of tokens) {
    if (token === "mod") parsed.mod = true;
    else if (token === "ctrl" || token === "control") parsed.ctrl = true;
    else if (token === "alt" || token === "opt" || token === "option") parsed.alt = true;
    else if (token === "shift") parsed.shift = true;
    else if (token === "meta" || token === "cmd" || token === "command" || token === "win") parsed.meta = true;
    else if (parsed.key) return null;
    else parsed.key = KEY_ALIASES[token] ?? token;
  }
  return parsed.key ? parsed : null;
}

function comboId(parsed: ParsedCombo) {
  return `${Number(parsed.mod)}${Number(parsed.ctrl)}${Number(parsed.alt)}${Number(parsed.shift)}${Number(parsed.meta)}:${parsed.key}`;
}

function eventKey(event: KeyboardEvent) {
  return event.key.toLowerCase();
}

function matchesCombo(parsed: ParsedCombo, event: KeyboardEvent) {
  if (event.defaultPrevented) return false;
  const wantCtrl = parsed.ctrl || (parsed.mod && !isMac);
  const wantMeta = parsed.meta || (parsed.mod && isMac);
  if (event.ctrlKey !== wantCtrl || event.metaKey !== wantMeta) return false;
  if (event.altKey !== parsed.alt || event.shiftKey !== parsed.shift) return false;
  const key = eventKey(event);
  if (parsed.key === " ") return key === " " || event.code === "Space";
  return key === parsed.key;
}

function isEditableTarget(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
}

function onWindowKeyDown(event: KeyboardEvent) {
  for (const { parsed, definition } of registry.values()) {
    if (!definition.allowInInput && isEditableTarget(event.target)) continue;
    if (!matchesCombo(parsed, event)) continue;
    event.preventDefault();
    try {
      definition.handler(event);
    } catch (error) {
      console.error(`[shortcuts] handler "${definition.id}" failed`, error);
    }
    return;
  }
}

function ensureListener() {
  if (listening || typeof window === "undefined") return;
  window.addEventListener("keydown", onWindowKeyDown);
  listening = true;
}

function releaseListener() {
  if (!listening || typeof window === "undefined") return;
  window.removeEventListener("keydown", onWindowKeyDown);
  listening = false;
}

export function registerShortcut(definition: ShortcutDefinition): () => void {
  const parsed = parseCombo(definition.combo);
  if (!parsed) {
    console.warn(`[shortcuts] ignored "${definition.id}": invalid combo "${definition.combo}"`);
    return () => undefined;
  }
  const id = comboId(parsed);
  const existing = registry.get(id);
  if (existing && existing.definition.id !== definition.id) {
    console.warn(`[shortcuts] conflict: "${definition.id}" (${definition.combo}) overrides "${existing.definition.id}"`);
  }
  registry.set(id, { parsed, definition });
  ensureListener();
  return () => unregisterShortcut(definition.id);
}

export function unregisterShortcut(id: string) {
  for (const [key, entry] of registry.entries()) {
    if (entry.definition.id !== id) continue;
    registry.delete(key);
    break;
  }
  if (registry.size === 0) releaseListener();
}

export function getAllShortcuts(): ShortcutDefinition[] {
  return [...registry.values()].map((entry) => entry.definition);
}

function formatKey(key: string) {
  if (key === " ") return "Space";
  if (key === "escape") return "Esc";
  if (key.length === 1) return key.toUpperCase();
  return key.charAt(0).toUpperCase() + key.slice(1);
}

export function formatCombo(combo: string): string {
  const parsed = parseCombo(combo);
  if (!parsed) return combo;
  const parts: string[] = [];
  if (parsed.mod) parts.push(isMac ? "⌘" : "Ctrl");
  if (parsed.ctrl && !parsed.mod) parts.push(isMac ? "⌃" : "Ctrl");
  if (parsed.meta) parts.push(isMac ? "⌘" : "Win");
  if (parsed.alt) parts.push(isMac ? "⌥" : "Alt");
  if (parsed.shift) parts.push(isMac ? "⇧" : "Shift");
  parts.push(formatKey(parsed.key));
  return parts.join(isMac ? "" : " ");
}

export function useShortcuts(shortcuts: readonly ShortcutDefinition[]): void {
  useEffect(() => {
    const dispose = shortcuts.map((definition) => registerShortcut(definition));
    return () => dispose.forEach((disposeShortcut) => disposeShortcut());
  }, [shortcuts]);
}
