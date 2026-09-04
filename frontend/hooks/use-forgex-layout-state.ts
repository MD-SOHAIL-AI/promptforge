"use client";

import { useCallback, useEffect, useRef, useState, type SetStateAction } from "react";

import {
  FORGEX_LAYOUT_STORAGE_KEY,
  clampForgeXLayout,
  defaultForgeXLayout,
  loadForgeXLayout,
  saveForgeXLayout,
  type ForgeWindowLayout,
  type ForgeXLayoutState,
  type LayoutBounds,
  type StorageWriter,
} from "@/lib/layout-state";

export interface UseForgeXLayoutStateOptions {
  storageKey?: string;
  storage?: StorageWriter;
  getBounds?: () => LayoutBounds;
}

export interface ForgeXLayoutController {
  layout: ForgeXLayoutState;
  ready: boolean;
  setLayout: (value: SetStateAction<ForgeXLayoutState>) => void;
  setForgeLayout: (value: SetStateAction<ForgeWindowLayout>) => void;
  resetLayout: () => void;
}

const FALLBACK_BOUNDS: LayoutBounds = { x: 0, y: 0, width: 1440, height: 920 };

export function useForgeXLayoutState(
  options: UseForgeXLayoutStateOptions = {},
): ForgeXLayoutController {
  const storageKey = options.storageKey ?? FORGEX_LAYOUT_STORAGE_KEY;
  const getBoundsRef = useRef(options.getBounds);
  const storageRef = useRef(options.storage);
  const [layout, setLayoutState] = useState(() => defaultForgeXLayout(FALLBACK_BOUNDS));
  const [ready, setReady] = useState(false);

  getBoundsRef.current = options.getBounds;
  storageRef.current = options.storage;

  const currentBounds = useCallback((): LayoutBounds => {
    const supplied = getBoundsRef.current?.();
    if (supplied) return supplied;
    if (typeof window === "undefined") return FALLBACK_BOUNDS;
    return { x: 0, y: 0, width: window.innerWidth, height: window.innerHeight };
  }, []);

  const currentStorage = useCallback((): StorageWriter | null => {
    if (storageRef.current) return storageRef.current;
    if (typeof window === "undefined") return null;
    try {
      return window.localStorage;
    } catch {
      return null;
    }
  }, []);

  const persist = useCallback((next: ForgeXLayoutState) => {
    const bounds = currentBounds();
    const storage = currentStorage();
    if (!storage) return clampForgeXLayout(next, bounds);
    try {
      return saveForgeXLayout(storage, next, bounds, storageKey);
    } catch {
      return clampForgeXLayout(next, bounds);
    }
  }, [currentBounds, currentStorage, storageKey]);

  useEffect(() => {
    const storage = currentStorage();
    const bounds = currentBounds();
    let next = defaultForgeXLayout(bounds);
    if (storage) {
      try {
        next = loadForgeXLayout(storage, bounds, storageKey);
        saveForgeXLayout(storage, next, bounds, storageKey);
      } catch {
        next = defaultForgeXLayout(bounds);
      }
    }
    setLayoutState(next);
    setReady(true);
  }, [currentBounds, currentStorage, storageKey]);

  const setLayout = useCallback((value: SetStateAction<ForgeXLayoutState>) => {
    setLayoutState((current) => persist(typeof value === "function" ? value(current) : value));
  }, [persist]);

  const setForgeLayout = useCallback((value: SetStateAction<ForgeWindowLayout>) => {
    setLayoutState((current) => {
      const forge = typeof value === "function" ? value(current.forge) : value;
      return persist({ ...current, forge });
    });
  }, [persist]);

  const resetLayout = useCallback(() => {
    setLayoutState(persist(defaultForgeXLayout(currentBounds())));
  }, [currentBounds, persist]);

  useEffect(() => {
    if (!ready || typeof window === "undefined") return;
    const handleResize = () => {
      setLayoutState((current) => persist(current));
    };
    const handleStorage = (event: StorageEvent) => {
      if (event.key !== storageKey) return;
      const storage = currentStorage();
      if (!storage) return;
      try {
        setLayoutState(loadForgeXLayout(storage, currentBounds(), storageKey));
      } catch {
        // Keep the in-memory layout when another tab writes malformed data.
      }
    };
    window.addEventListener("resize", handleResize);
    window.addEventListener("storage", handleStorage);
    return () => {
      window.removeEventListener("resize", handleResize);
      window.removeEventListener("storage", handleStorage);
    };
  }, [currentBounds, currentStorage, persist, ready, storageKey]);

  return { layout, ready, setLayout, setForgeLayout, resetLayout };
}
