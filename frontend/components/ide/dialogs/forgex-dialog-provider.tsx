"use client";

import { createContext, type ReactNode, useCallback, useContext, useMemo, useState } from "react";

import { ConfirmDialog, type ConfirmDialogVariant } from "@/components/ide/dialogs/confirm-dialog";
import { FORGEX_INPUT_FIELD_ID, InputDialog } from "@/components/ide/dialogs/input-dialog";
import { MessageDialog } from "@/components/ide/dialogs/message-dialog";
import { FxOverlay } from "@/components/ui/fx-overlay";

export interface ForgeXInputDialogRequest {
  title: string;
  description?: string;
  label: string;
  placeholder?: string;
  defaultValue?: string;
  confirmText?: string;
  cancelText?: string;
  validate?: (value: string) => string | null;
}

export interface ForgeXConfirmDialogRequest {
  title: string;
  description?: string;
  confirmText?: string;
  cancelText?: string;
  variant?: ConfirmDialogVariant;
}

export interface ForgeXMessageDialogRequest {
  title: string;
  description?: string;
  confirmText?: string;
}

export interface ForgeXDialogs {
  input: (options: ForgeXInputDialogRequest) => Promise<string | null>;
  confirmAction: (options: ForgeXConfirmDialogRequest) => Promise<boolean>;
  message: (options: ForgeXMessageDialogRequest) => Promise<void>;
}

type ActiveDialog =
  | ({ type: "input"; resolve: (value: string | null) => void } & ForgeXInputDialogRequest)
  | ({ type: "confirm"; resolve: (value: boolean) => void } & ForgeXConfirmDialogRequest)
  | ({ type: "message"; resolve: () => void } & ForgeXMessageDialogRequest);

const DialogContext = createContext<ForgeXDialogs | null>(null);

export function ForgeXDialogProvider({ children }: { children: ReactNode }) {
  const [dialog, setDialog] = useState<ActiveDialog | null>(null);

  const input = useCallback((options: ForgeXInputDialogRequest) => {
    return new Promise<string | null>((resolve) => {
      setDialog({ type: "input", resolve, ...options });
    });
  }, []);

  const confirmAction = useCallback((options: ForgeXConfirmDialogRequest) => {
    return new Promise<boolean>((resolve) => {
      setDialog({ type: "confirm", resolve, ...options });
    });
  }, []);

  const message = useCallback((options: ForgeXMessageDialogRequest) => {
    return new Promise<void>((resolve) => {
      setDialog({ type: "message", resolve, ...options });
    });
  }, []);

  const api = useMemo(() => ({ input, confirmAction, message }), [confirmAction, input, message]);

  const finishInput = useCallback(
    (value: string | null) => {
      if (!dialog || dialog.type !== "input") return;
      dialog.resolve(value);
      setDialog(null);
    },
    [dialog],
  );

  const finishConfirm = useCallback(
    (value: boolean) => {
      if (!dialog || dialog.type !== "confirm") return;
      dialog.resolve(value);
      setDialog(null);
    },
    [dialog],
  );

  const finishMessage = useCallback(() => {
    if (!dialog || dialog.type !== "message") return;
    dialog.resolve();
    setDialog(null);
  }, [dialog]);

  const dismiss = useCallback(() => {
    switch (dialog?.type) {
      case "input":
        finishInput(null);
        break;
      case "confirm":
        finishConfirm(false);
        break;
      case "message":
        finishMessage();
        break;
    }
  }, [dialog, finishConfirm, finishInput, finishMessage]);

  return (
    <DialogContext.Provider value={api}>
      {children}
      <FxOverlay
        open={dialog !== null}
        onOpenChange={(next) => {
          if (!next) dismiss();
        }}
        title={dialog?.title ?? ""}
        description={dialog?.description}
        size="sm"
        initialFocusId={dialog?.type === "input" ? FORGEX_INPUT_FIELD_ID : undefined}
      >
        {dialog?.type === "input" && dialog.label !== undefined ? (
          <InputDialog
            label={dialog.label}
            placeholder={dialog.placeholder}
            defaultValue={dialog.defaultValue}
            confirmText={dialog.confirmText}
            cancelText={dialog.cancelText}
            validate={dialog.validate}
            onConfirm={(value) => finishInput(value)}
            onCancel={() => finishInput(null)}
          />
        ) : null}
        {dialog?.type === "confirm" ? (
          <ConfirmDialog
            confirmText={dialog.confirmText}
            cancelText={dialog.cancelText}
            variant={dialog.variant}
            onConfirm={() => finishConfirm(true)}
            onCancel={() => finishConfirm(false)}
          />
        ) : null}
        {dialog?.type === "message" ? (
          <MessageDialog
            confirmText={dialog.confirmText}
            onConfirm={finishMessage}
          />
        ) : null}
      </FxOverlay>
    </DialogContext.Provider>
  );
}

export function useForgeXDialogs() {
  const dialogs = useContext(DialogContext);
  if (!dialogs) {
    throw new Error("useForgeXDialogs must be used inside ForgeXDialogProvider");
  }
  return dialogs;
}
