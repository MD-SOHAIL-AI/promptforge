"use client";

import { Minus, Plus } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
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
    <div className="group flex min-w-0 flex-col gap-3 border-b border-[var(--fx-border-soft)] py-3.5 last:border-b-0 sm:flex-row sm:items-center sm:justify-between sm:gap-6">
      <div className="min-w-0">
        <div className="text-[13px] font-medium text-[var(--fx-text)]">{label}</div>
        {description ? <div className="mt-1 max-w-xl text-[11px] leading-4 text-[var(--fx-text-muted)]">{description}</div> : null}
      </div>
      <div className="w-full min-w-0 sm:w-auto sm:shrink-0">{control}</div>
    </div>
  );
}

export function SettingToggle({ label, description, value, disabled, onChange }: ControlProps) {
  const checked = Boolean(value);
  return (
    <SettingRow label={label} description={description} control={
      <Switch
        checked={checked}
        disabled={disabled}
        aria-label={label}
        onCheckedChange={(next) => onChange(next)}
      />
    } />
  );
}

export function SettingSelect({ label, description, value, options, optionLabels, disabled, onChange }: ControlProps & { options: string[]; optionLabels?: Record<string, string> }) {
  return (
    <SettingRow label={label} description={description} control={
      <Select value={String(value ?? "")} onValueChange={(next) => onChange(next)} disabled={disabled}>
        <SelectTrigger className="h-9 w-full text-xs sm:w-44" aria-label={label}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((option) => <SelectItem key={option} value={option}>{optionLabels?.[option] ?? option.replaceAll("_", " ")}</SelectItem>)}
        </SelectContent>
      </Select>
    } />
  );
}

export function SettingNumber({ label, description, value, min, max, disabled, onChange }: ControlProps & { min?: number; max?: number }) {
  const number = Number(value ?? 0);
  const update = (next: number) => onChange(Math.min(max ?? next, Math.max(min ?? next, next)));
  return (
    <SettingRow label={label} description={description} control={
      <div className="fx-control flex h-9 w-full items-center overflow-hidden sm:w-auto">
        <button type="button" className="fx-icon-button h-full w-8 rounded-none" disabled={disabled || number <= (min ?? -Infinity)} onClick={() => update(number - 1)} aria-label={`Decrease ${label}`}><Minus className="h-3 w-3" /></button>
        <input type="number" min={min} max={max} className="h-full min-w-0 flex-1 border-x border-[var(--fx-border)] bg-transparent text-center text-xs outline-none sm:w-16 sm:flex-none" value={number} disabled={disabled} onChange={(event) => update(Number(event.target.value))} aria-label={label} />
        <button type="button" className="fx-icon-button h-full w-8 rounded-none" disabled={disabled || number >= (max ?? Infinity)} onClick={() => update(number + 1)} aria-label={`Increase ${label}`}><Plus className="h-3 w-3" /></button>
      </div>
    } />
  );
}

export function SettingSlider({ label, description, value, min, max, suffix = "", disabled, onChange }: ControlProps & { min: number; max: number; suffix?: string }) {
  const number = Number(value ?? min);
  const [draft, setDraft] = useState(number);
  useEffect(() => setDraft(number), [number]);
  const commit = () => {
    if (draft !== number) onChange(draft);
  };
  return (
    <SettingRow label={label} description={description} control={
      <div className="flex w-full items-center gap-3 sm:w-52">
        <input
          type="range"
          min={min}
          max={max}
          value={draft}
          disabled={disabled}
          onChange={(event) => setDraft(Number(event.target.value))}
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

export function SettingText({ label, description, value, disabled, onChange }: ControlProps) {
  return <SettingRow label={label} description={description} control={<input className="fx-control h-9 w-full px-3 text-xs outline-none sm:w-56" value={String(value ?? "")} disabled={disabled} onChange={(event) => onChange(event.target.value)} aria-label={label} />} />;
}

export function SettingList({ label, description, value, disabled, onChange }: ControlProps) {
  const text = Array.isArray(value) ? value.join(", ") : String(value ?? "");
  return <SettingRow label={label} description={description} control={<input className="fx-control h-9 w-full px-3 text-xs outline-none sm:w-72" value={text} disabled={disabled} onChange={(event) => onChange(event.target.value.split(",").map((item) => item.trim()).filter(Boolean))} aria-label={label} />} />;
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
