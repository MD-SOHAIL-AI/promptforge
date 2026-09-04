"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type { SerialMonitorEvent } from "@/types";

const MAX_EVENTS = 2_000;

export function useSerialMonitor() {
  const [events, setEvents] = useState<SerialMonitorEvent[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cursorRef = useRef(0);

  useEffect(() => {
    const source = new EventSource(
      `/api/promptforge/monitor/stream?after=${encodeURIComponent(String(cursorRef.current))}`,
    );
    const onObservation = (message: MessageEvent<string>) => {
      try {
        const event = JSON.parse(message.data) as SerialMonitorEvent;
        if (!Number.isFinite(event.sequence) || event.sequence <= cursorRef.current) return;
        const gap = event.sequence - cursorRef.current - 1;
        cursorRef.current = event.sequence;
        setEvents((current) => {
          const next = gap > 0 && current.length > 0
            ? [...current, {
                sequence: event.sequence - 0.5,
                timestamp: event.timestamp,
                line: `[OVERFLOW: ${gap} serial observations dropped by the live stream]`,
                source: "OVERFLOW",
                port: event.port,
                metadata: { dropped: gap },
              }, event]
            : [...current, event];
          return next.slice(-MAX_EVENTS);
        });
        setError(null);
      } catch {
        setError("Serial stream returned invalid data.");
      }
    };
    source.addEventListener("open", () => setStreaming(true));
    source.addEventListener("observation", onObservation as EventListener);
    source.addEventListener("error", () => {
      setStreaming(false);
      setError("Serial stream disconnected; reconnecting.");
    });
    return () => {
      source.removeEventListener("observation", onObservation as EventListener);
      source.close();
      setStreaming(false);
    };
  }, []);

  const clear = useCallback(() => setEvents([]), []);
  return { events, streaming, error, clear };
}
