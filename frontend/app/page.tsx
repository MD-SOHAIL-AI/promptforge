import { IdeErrorBoundary } from "@/components/ide/ide-error-boundary";
import { ForgeXDialogProvider } from "@/components/ide/dialogs/forgex-dialog-provider";
import { NexusShell } from "@/components/nexus/nexus-shell";

export default function HomePage() {
  return (
    <IdeErrorBoundary>
      <ForgeXDialogProvider>
        <NexusShell />
      </ForgeXDialogProvider>
    </IdeErrorBoundary>
  );
}
