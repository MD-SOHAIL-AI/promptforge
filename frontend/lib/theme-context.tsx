"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { FORGEX_THEMES } from "@/lib/theme";

const ForgeXThemeVersionContext = createContext(0);

function resolveDocumentMode(): "dark" | "light" {
  if (typeof document === "undefined") return "dark";
  const root = document.documentElement;
  const theme = FORGEX_THEMES.find((item) => item.id === root.dataset.theme);
  if (theme) return theme.mode;
  return root.classList.contains("light") ? "light" : "dark";
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [version, setVersion] = useState(0);
  const bump = useCallback(() => setVersion((current) => current + 1), []);

  useEffect(() => {
    window.addEventListener("forgex-theme-change", bump);
    const observer = new MutationObserver(bump);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme", "style", "class"],
    });
    return () => {
      window.removeEventListener("forgex-theme-change", bump);
      observer.disconnect();
    };
  }, [bump]);

  useEffect(() => {
    const mode = resolveDocumentMode();
    document.documentElement.classList.toggle("dark", mode === "dark");
    document.documentElement.classList.toggle("light", mode === "light");
  }, [version]);

  return <ForgeXThemeVersionContext.Provider value={version}>{children}</ForgeXThemeVersionContext.Provider>;
}

export function useForgeXThemeVersion(): number {
  return useContext(ForgeXThemeVersionContext);
}

export function useResolvedMode(): "dark" | "light" {
  const version = useForgeXThemeVersion();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  return useMemo(() => {
    void version;
    return mounted ? resolveDocumentMode() : "dark";
  }, [mounted, version]);
}
