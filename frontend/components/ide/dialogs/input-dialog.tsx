"use client";

import type { FormEvent } from "react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { ErrorNote } from "@/components/ui/error-note";
import { Input } from "@/components/ui/input";

export const FORGEX_INPUT_FIELD_ID = "forgex-input-field";

export interface InputDialogOptions {
  title?: string;
  description?: string;
  label: string;
  placeholder?: string;
  defaultValue?: string;
  confirmText?: string;
  cancelText?: string;
  validate?: (value: string) => string | null;
  onConfirm: (value: string) => void;
  onCancel: () => void;
}

export function InputDialog({
  label,
  placeholder,
  defaultValue = "",
  confirmText = "Create",
  cancelText = "Cancel",
  validate,
  onConfirm,
  onCancel,
}: InputDialogOptions) {
  const [value, setValue] = useState(defaultValue);
  const [error, setError] = useState<string | null>(null);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const nextError = validate?.(value) ?? null;
    setError(nextError);
    if (nextError) return;
    onConfirm(value.trim().replaceAll("\\", "/"));
  };

  return (
    <form onSubmit={submit}>
      <div className="grid gap-2.5 px-4 py-4">
        <label htmlFor={FORGEX_INPUT_FIELD_ID} className="grid gap-1.5 text-xs font-medium text-[var(--fx-text-muted)]">
          {label}
          <Input
            id={FORGEX_INPUT_FIELD_ID}
            value={value}
            placeholder={placeholder}
            invalid={error !== null}
            onChange={(event) => {
              setValue(event.target.value);
              if (error) setError(null);
            }}
          />
        </label>
        {error ? <ErrorNote tone="error" message={error} /> : null}
      </div>
      <div className="flex justify-end gap-2 border-t border-[var(--fx-border-soft)] px-4 py-3">
        <Button type="button" variant="secondary" size="md" onClick={onCancel}>
          {cancelText}
        </Button>
        <Button type="submit" variant="default" size="md">
          {confirmText}
        </Button>
      </div>
    </form>
  );
}
