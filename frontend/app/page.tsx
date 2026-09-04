import { IdeErrorBoundary } from "@/components/ide/ide-error-boundary";
import { ForgeXDialogProvider } from "@/components/ide/dialogs/forgex-dialog-provider";
import { UnhandledRejectionGuard } from "@/components/ide/unhandled-rejection-guard";
import { NexusShell } from "@/components/nexus/nexus-shell";

export default function HomePage() {
  return (
    <IdeErrorBoundary>
      <ForgeXDialogProvider>
        <UnhandledRejectionGuard />
        <span className="sr-only">ForgeX Embedded Engineering Environment</span>
        <NexusShell />
      </ForgeXDialogProvider>
    </IdeErrorBoundary>
  );
}
