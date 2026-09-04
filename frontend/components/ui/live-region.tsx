"use client";

import { useEffect, useRef, useState } from "react";

const ANNOUNCE_EVENT = "forgex-announce";
const DEBOUNCE_MS = 500;
const MAX_LENGTH = 240;

export function announce(message: string) {
  if (typeof window === "undefined" || !message.trim()) return;
  window.dispatchEvent(new CustomEvent<string>(ANNOUNCE_EVENT, { detail: message }));
}

export function LiveRegion() {
  const [message, setMessage] = useState("");
  const timer = useRef<number | null>(null);

  useEffect(() => {
    const onAnnounce = (event: Event) => {
      const detail = (event as CustomEvent<string>).detail;
      const text = typeof detail === "string" ? detail.trim().slice(0, MAX_LENGTH) : "";
      if (!text) return;
      if (timer.current !== null) window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => setMessage(text), DEBOUNCE_MS);
    };
    window.addEventListener(ANNOUNCE_EVENT, onAnnounce);
    return () => {
      window.removeEventListener(ANNOUNCE_EVENT, onAnnounce);
      if (timer.current !== null) window.clearTimeout(timer.current);
    };
  }, []);

  return <div role="status" aria-live="polite" className="sr-only">{message}</div>;
}
