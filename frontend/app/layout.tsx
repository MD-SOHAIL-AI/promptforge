import type { Metadata } from "next";
import Script from "next/script";
import { Toaster } from "sonner";

import { ThemeProvider } from "@/lib/theme-context";
import { TooltipProvider } from "@/components/ui/tooltip";

import "highlight.js/styles/github-dark-dimmed.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "ForgeX Nexus - AI-Native Embedded Engineering",
  description: "AI-native embedded engineering workspace for autonomous planning, coding, builds, hardware verification, flashing, and live device debugging.",
};

const forgexModeBootstrap = `
(function () {
  try {
    var root = document.documentElement;
    root.classList.add("dark");
    root.classList.remove("light");
  } catch (_) {}
})();`;

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="dark" suppressHydrationWarning>
      <body>
        <Script
          id="forgex-mode-bootstrap"
          strategy="beforeInteractive"
          dangerouslySetInnerHTML={{ __html: forgexModeBootstrap }}
        />
        <Script
          id="forgex-unhandled-rejection-guard"
          strategy="beforeInteractive"
          dangerouslySetInnerHTML={{
            __html: `
(function () {
  if (window.__forgexUnhandledRejectionGuardInstalled) return;
  window.__forgexUnhandledRejectionGuardInstalled = true;
  window.addEventListener("unhandledrejection", function (event) {
    var reason = event && event.reason;
    var suppress = false;
    if (reason instanceof Error) suppress = reason.name === "PromptForgeApiError";
    else suppress = !!reason && typeof reason === "object";
    if (!suppress) return;
    // Use console.debug; console.error is intercepted by Next.js dev overlay
    // and would show a spurious "Console Error" for intentionally-handled rejections.
    try {
      if (window.__FORGEX_DEBUG) {
        console.debug("ForgeX ignored handled promise rejection", {
          message: reason instanceof Error ? reason.message : String(reason),
          reason: reason
        });
      }
    } catch (_) {}
    // Next.js dev overlay registers its own unhandledrejection listener that
    // ignores defaultPrevented, so stopImmediatePropagation is required to keep
    // intentionally-handled rejections out of the "[object Object]" overlay.
    event.stopImmediatePropagation();
    event.preventDefault();
  }, true);
})();`,
          }}
        />
        <ThemeProvider>
          <TooltipProvider delayDuration={300} skipDelayDuration={150}>
            {children}
          </TooltipProvider>
        </ThemeProvider>
        <Toaster
          position="bottom-right"
          richColors={false}
          toastOptions={{
            unstyled: false,
            style: {
              background: "var(--fx-panel-elevated)",
              border: "1px solid var(--fx-border-soft)",
              color: "var(--fx-text)",
            },
          }}
        />
      </body>
    </html>
  );
}
