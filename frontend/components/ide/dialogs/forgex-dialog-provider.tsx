"use client";

import { createContext, ReactNode, useCallback, useContext, useMemo, useState } from "react";

import { ConfirmDialog, type ConfirmDialogVariant } from "@/components/ide/dialogs/confirm-dialog";
import { InputDialog } from "@/components/ide/dialogs/input-dialog";
import { MessageDialog } from "@/components/ide/dialogs/message-dialog";

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

  const close = useCallback(() => setDialog(null), []);

  return (
    <DialogContext.Provider value={api}>
      {children}
      {dialog ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-4 backdrop-blur-sm">
          {dialog.type === "input" ? (
            <InputDialog
              {...dialog}
              onCancel={() => {
                dialog.resolve(null);
                close();
              }}
              onConfirm={(value) => {
                dialog.resolve(value);
                close();
              }}
            />
          ) : null}
          {dialog.type === "confirm" ? (
            <ConfirmDialog
              {...dialog}
              onCancel={() => {
                dialog.resolve(false);
                close();
              }}
              onConfirm={() => {
                dialog.resolve(true);
                close();
              }}
            />
          ) : null}
          {dialog.type === "message" ? (
            <MessageDialog
              {...dialog}
              onConfirm={() => {
                dialog.resolve();
                close();
              }}
            />
          ) : null}
        </div>
      ) : null}
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
