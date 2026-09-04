import { toast } from "sonner";

export interface NotifyAction {
  label: string;
  onClick: () => void;
}

export interface NotifyOptions {
  description?: string;
  action?: NotifyAction;
}

function emit(method: "error" | "success" | "warning", message: string, options?: NotifyOptions) {
  toast[method](message, {
    description: options?.description,
    action: options?.action,
  });
}

export const notify = {
  error(message: string, options?: NotifyOptions) {
    emit("error", message, options);
  },
  success(message: string, options?: NotifyOptions) {
    emit("success", message, options);
  },
  warning(message: string, options?: NotifyOptions) {
    emit("warning", message, options);
  },
};
