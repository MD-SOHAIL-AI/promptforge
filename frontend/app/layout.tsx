import type { Metadata } from "next";

import "highlight.js/styles/github-dark-dimmed.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "PromptForge Embedded Engineering Environment",
  description: "AI-powered workspace for embedded firmware planning, generation, builds, flashing, and monitoring.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="dark">
      <body>{children}</body>
    </html>
  );
}
