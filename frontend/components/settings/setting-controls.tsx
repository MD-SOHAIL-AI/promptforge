"use client";

import { ChevronDown, Minus, Plus } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";

import type { ForgeXSettingValue } from "@/types";

interface ControlProps {
  label: string;
  description?: string;
  value: ForgeXSettingValue;
  disabled?: boolean;
  onChange: (value: ForgeXSettingValue) => void;
  onPreview?: (value: ForgeXSettingValue) => void;
}

function SettingRow({ label, description, control }: { label: string; description?: string; control: ReactNode }) {
  return (
    <div className="fx-setting-row group flex min-w-0 items-center justify-between gap-6 border-b border-[var(--fx-border-soft)] py-3.5 last:border-b-0" data-setting-label={label}>
      <div className="min-w-0">
        <div className="text-[13px] font-medium text-[var(--fx-text)]">{label}</div>
        {description ? <div className="mt-1 max-w-xl text-[11px] leading-4 text-[var(--fx-text-muted)]">{description}</div> : null}
      </div>
      <div className="shrink-0">{control}</div>
    </div>
  );
}

export function SettingToggle({ label, description, value, disabled, onChange }: ControlProps) {
  const checked = Boolean(value);
  return (
    <SettingRow label={label} description={description} control={
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={`relative h-6 w-11 rounded-full border p-0.5 disabled:opacity-50 ${checked ? "border-[var(--fx-accent)] bg-[var(--fx-accent)]" : "border-[var(--fx-border)] bg-[var(--fx-input)]"}`}
      >
        <span className={`block h-4.5 w-4.5 rounded-full bg-white shadow-sm transition-transform ${checked ? "translate-x-5" : "translate-x-0"}`} style={{ width: 18, height: 18 }} />
      </button>
    } />
  );
}

export function SettingSelect({ label, description, value, options, optionLabels, disabled, onChange }: ControlProps & { options: string[]; optionLabels?: Record<string, string> }) {
  return (
    <SettingRow label={label} description={description} control={
      <div className="fx-control relative min-w-44">
        <select className="h-9 w-full appearance-none bg-transparent pl-3 pr-9 text-xs outline-none" value={String(value ?? "")} disabled={disabled} onChange={(event) => onChange(event.target.value)} aria-label={label}>
          {options.map((option) => <option key={option} value={option}>{optionLabels?.[option] ?? option.replaceAll("_", " ")}</option>)}
        </select>
        <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--fx-text-muted)]" />
      </div>
    } />
  );
}

export function SettingNumber({ label, description, value, min, max, disabled, onChange }: ControlProps & { min?: number; max?: number }) {
  const number = Number(value ?? 0);
  const [draft, setDraft] = useState(String(number));
  const dirty = useRef(false);
  useEffect(() => { setDraft(String(number)); }, [number]);
  const update = (next: number) => {
    if (!Number.isFinite(next)) { setDraft(String(number)); return; }
    const clamped = Math.min(max ?? next, Math.max(min ?? next, next));
    setDraft(String(clamped)); onChange(clamped);
  };
  const commit = () => {
    if (!dirty.current) return;
    dirty.current = false;
    if (!draft.trim()) setDraft(String(number));
    else update(Number(draft));
  };
  return (
    <SettingRow label={label} description={description} control={
      <div className="fx-control flex h-9 items-center overflow-hidden">
        <button type="button" className="fx-icon-button h-full w-8 rounded-none" disabled={disabled || number <= (min ?? -Infinity)} onClick={() => update(number - 1)} aria-label={`Decrease ${label}`}><Minus className="h-3 w-3" /></button>
        <input type="number" min={min} max={max} className="h-full w-20 border-x border-[var(--fx-border)] bg-transparent text-center text-xs outline-none" value={draft} disabled={disabled} onChange={(event) => {dirty.current = true; setDraft(event.target.value);}} onBlur={commit} onKeyDown={event => {if (event.key === "Enter") commit();}} aria-label={label} />
        <button type="button" className="fx-icon-button h-full w-8 rounded-none" disabled={disabled || number >= (max ?? Infinity)} onClick={() => update(number + 1)} aria-label={`Increase ${label}`}><Plus className="h-3 w-3" /></button>
      </div>
    } />
  );
}

export function SettingSlider({ label, description, value, min, max, suffix = "", disabled, onChange, onPreview }: ControlProps & { min: number; max: number; suffix?: string }) {
  const number = Number(value ?? min);
  const [draft, setDraft] = useState(number);
  useEffect(() => setDraft(number), [number]);
  const dirty = useRef(false);
  const commit = () => {
    if (dirty.current) { dirty.current = false; onChange(draft); }
  };
  return (
    <SettingRow label={label} description={description} control={
      <div className="flex w-52 items-center gap-3">
        <input
          type="range"
          min={min}
          max={max}
          value={draft}
          disabled={disabled}
          onChange={(event) => { const next = Number(event.target.value); dirty.current = true; setDraft(next); onPreview?.(next); }}
          onPointerUp={commit}
          onKeyUp={commit}
          onBlur={commit}
          aria-label={label}
          className="fx-range min-w-0 flex-1"
        />
        <span className="w-10 text-right font-mono text-[10px] text-[var(--fx-text-muted)]">{draft}{suffix}</span>
      </div>
    } />
  );
}

function DraftText({ label, description, value, disabled, onChange, list = false }: ControlProps & {list?: boolean}) {
  const text = Array.isArray(value) ? value.join(", ") : String(value ?? "");
  const [draft, setDraft] = useState(text);
  const dirty = useRef(false);
  useEffect(() => { setDraft(text); }, [text]);
  const commit = () => {
    if (!dirty.current) return;
    dirty.current = false;
    onChange(list ? draft.split(",").map(item => item.trim()).filter(Boolean) : draft);
  };
  return <SettingRow label={label} description={description} control={<input className="fx-control h-9 w-64 max-w-full px-3 text-xs outline-none" value={draft} disabled={disabled} onChange={event => {dirty.current = true; setDraft(event.target.value);}} onBlur={commit} onKeyDown={event => {if (event.key === "Enter") commit();}} aria-label={label} />} />;
}
export function SettingText(props: ControlProps) { return <DraftText {...props} />; }
export function SettingList(props: ControlProps) { return <DraftText {...props} list />; }

export function SettingsCard({ title, description, icon, children }: { title: string; description?: string; icon?: ReactNode; children: ReactNode }) {
  return (
    <section className="fx-card min-w-0 overflow-hidden">
      <header className="flex items-start gap-3 border-b border-[var(--fx-border-soft)] px-4 py-3.5">
        {icon ? <span className="mt-0.5 text-[var(--fx-accent)]">{icon}</span> : null}
        <div><h3 className="text-[13px] font-semibold text-[var(--fx-text)]">{title}</h3>{description ? <p className="mt-1 text-[11px] leading-4 text-[var(--fx-text-muted)]">{description}</p> : null}</div>
      </header>
      <div className="px-4">{children}</div>
    </section>
  );
}
