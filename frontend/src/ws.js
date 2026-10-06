import { useEffect, useRef, useState } from "react";

// Live event stream from the lab (WebSocket, same-origin relative path).
export function useLabEvents(max = 250) {
  const [events, setEvents] = useState([]);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef(null);

  useEffect(() => {
    let alive = true;
    let retry = 0;
    let timer = null;

    const connect = () => {
      if (!alive) return;
      const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
      const ws = new WebSocket(`${proto}//${window.location.host}/ws`);
      wsRef.current = ws;
      ws.onopen = () => { retry = 0; setConnected(true); };
      ws.onmessage = (m) => {
        try {
          const ev = JSON.parse(m.data);
          setEvents((prev) => {
            const next = [ev, ...prev];
            return next.length > max ? next.slice(0, max) : next;
          });
        } catch {}
      };
      ws.onclose = () => {
        setConnected(false);
        if (alive) {
          retry = Math.min(retry + 1, 6);
          timer = setTimeout(connect, 500 * retry);
        }
      };
      ws.onerror = () => ws.close();
    };
    connect();
    return () => { alive = false; clearTimeout(timer); wsRef.current?.close(); };
  }, [max]);

  return { events, connected };
}
