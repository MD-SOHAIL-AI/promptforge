import type { Metadata } from "next";

import "highlight.js/styles/github-dark-dimmed.css";
import "./globals.css";
import "./design-system.css";
import { RuntimeDiagnostics } from "./runtime-diagnostics";

export const metadata: Metadata = {
  title: "ForgeX Nexus — AI-Native Embedded Engineering",
  description: "AI-native embedded engineering workspace for autonomous planning, coding, builds, hardware verification, flashing, and live device debugging.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="dark">
      <body>
        <RuntimeDiagnostics />
        {children}
      </body>
    </html>
  );
}
