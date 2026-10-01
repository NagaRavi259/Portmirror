import { useEffect, useRef, useState } from "react";
import type { Snapshot } from "./api";

export type LinkState = "connecting" | "live" | "offline";

/** Subscribes to /api/ws; reconnects with backoff. */
export function useLive(enabled: boolean) {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [link, setLink] = useState<LinkState>("connecting");
  const retry = useRef(0);

  useEffect(() => {
    if (!enabled) return;
    let ws: WebSocket | null = null;
    let timer: number | undefined;
    let closed = false;

    const connect = () => {
      setLink("connecting");
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/api/ws`);
      ws.onopen = () => {
        retry.current = 0;
        setLink("live");
      };
      ws.onmessage = (e) => {
        const d = JSON.parse(e.data);
        if (d.type === "snapshot") setSnap(d as Snapshot);
      };
      ws.onclose = () => {
        if (closed) return;
        setLink("offline");
        const delay = Math.min(10_000, 500 * 2 ** retry.current++);
        timer = window.setTimeout(connect, delay);
      };
    };
    connect();
    return () => {
      closed = true;
      window.clearTimeout(timer);
      ws?.close();
    };
  }, [enabled]);

  return { snap, link };
}

/** Keeps a rolling client-side series (for charts that start before history loads). */
export function useSeries<T>(value: T | undefined, max = 300) {
  const ref = useRef<T[]>([]);
  const [, force] = useState(0);
  useEffect(() => {
    if (value === undefined) return;
    ref.current = [...ref.current.slice(-(max - 1)), value];
    force((x) => x + 1);
  }, [value, max]);
  return ref.current;
}
