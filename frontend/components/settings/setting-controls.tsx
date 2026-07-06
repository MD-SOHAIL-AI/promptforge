"use client";

import { ChevronDown, Minus, Plus } from "lucide-react";
import type { ReactNode } from "react";

import type { ForgeXSettingValue } from "@/types";

interface ControlProps {
  label: string;
  description?: string;
  value: ForgeXSettingValue;
  disabled?: boolean;
  onChange: (value: ForgeXSettingValue) => void;
}

function SettingRow({ label, description, control }: { label: string; description?: string; control: ReactNode }) {
  return (
    <div className="group flex min-w-0 items-center justify-between gap-6 border-b border-[var(--fx-border-soft)] py-3.5 last:border-b-0">
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
  const update = (next: number) => onChange(Math.min(max ?? next, Math.max(min ?? next, next)));
  return (
    <SettingRow label={label} description={description} control={
      <div className="fx-control flex h-9 items-center overflow-hidden">
        <button type="button" className="fx-icon-button h-full w-8 rounded-none" disabled={disabled || number <= (min ?? -Infinity)} onClick={() => update(number - 1)} aria-label={`Decrease ${label}`}><Minus className="h-3 w-3" /></button>
        <input type="number" min={min} max={max} className="h-full w-16 border-x border-[var(--fx-border)] bg-transparent text-center text-xs outline-none" value={number} disabled={disabled} onChange={(event) => update(Number(event.target.value))} aria-label={label} />
        <button type="button" className="fx-icon-button h-full w-8 rounded-none" disabled={disabled || number >= (max ?? Infinity)} onClick={() => update(number + 1)} aria-label={`Increase ${label}`}><Plus className="h-3 w-3" /></button>
      </div>
    } />
  );
}

export function SettingText({ label, description, value, disabled, onChange }: ControlProps) {
  return <SettingRow label={label} description={description} control={<input className="fx-control h-9 w-56 px-3 text-xs outline-none" value={String(value ?? "")} disabled={disabled} onChange={(event) => onChange(event.target.value)} aria-label={label} />} />;
}

export function SettingList({ label, description, value, disabled, onChange }: ControlProps) {
  const text = Array.isArray(value) ? value.join(", ") : String(value ?? "");
  return <SettingRow label={label} description={description} control={<input className="fx-control h-9 w-72 px-3 text-xs outline-none" value={text} disabled={disabled} onChange={(event) => onChange(event.target.value.split(",").map((item) => item.trim()).filter(Boolean))} aria-label={label} />} />;
}

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
