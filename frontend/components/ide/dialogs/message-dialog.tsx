"use client";

import { Button } from "@/components/ui/button";

export interface MessageDialogOptions {
  title?: string;
  description?: string;
  confirmText?: string;
  onConfirm: () => void;
}

export function MessageDialog({ confirmText = "OK", onConfirm }: MessageDialogOptions) {
  return (
    <div className="flex justify-end border-t border-[var(--fx-border-soft)] px-4 py-3">
      <Button variant="default" size="md" onClick={onConfirm}>
        {confirmText}
      </Button>
    </div>
  );
}
