import { IdeErrorBoundary } from "@/components/ide/ide-error-boundary";
import { ForgeXShell } from "@/components/ide/forgex-shell";
import { ForgeXDialogProvider } from "@/components/ide/dialogs/forgex-dialog-provider";

export default function HomePage() {
  return (
    <IdeErrorBoundary>
      <ForgeXDialogProvider>
        <ForgeXShell />
      </ForgeXDialogProvider>
    </IdeErrorBoundary>
  );
}
