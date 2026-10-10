import { useEffect, useRef, useState } from "react";

const pad = (n) => String(n).padStart(2, "0");

export function computeTimeLeft(endsAt) {
  if (!endsAt) return null;
  const diff = new Date(endsAt) - Date.now();
  if (diff <= 0) return { ended: true, urgent: false, label: "Avslutad", diff: 0 };
  const h = Math.floor(diff / 3.6e6);
  const m = Math.floor((diff % 3.6e6) / 6e4);
  const s = Math.floor((diff % 6e4) / 1000);
  const d = Math.floor(h / 24);
  let label;
  if (d >= 1) label = `${d}d ${h % 24}h`;
  else if (h >= 1) label = `${h}h ${m}m`;
  else label = `${m}m ${pad(s)}s`;
  return { ended: false, urgent: diff <= 5 * 6e4, label, diff };
}

// Live countdown: ticks every second in the last hour, every minute otherwise.
export function useTimeLeft(endsAt, onEnd) {
  const [tl, setTl] = useState(() => computeTimeLeft(endsAt));
  const endRef = useRef(onEnd);
  endRef.current = onEnd;
  useEffect(() => {
    let timer;
    const tick = () => {
      const next = computeTimeLeft(endsAt);
      setTl(next);
      if (!next || next.ended) return;
      const delay = next.diff <= 3.6e6 ? (next.diff % 1000) || 1000 : (next.diff % 6e4) || 6e4;
      timer = setTimeout(() => {
        const after = computeTimeLeft(endsAt);
        if (after?.ended) { setTl(after); endRef.current?.(); } else tick();
      }, delay + 20);
    };
    tick();
    return () => clearTimeout(timer);
  }, [endsAt]);
  return tl;
}

export const countdownClass = (tl, base = "bg-amber-500/90 text-slate-950") =>
  tl.ended ? "bg-slate-900/80 text-slate-400" : tl.urgent ? "bg-red-600/95 text-white animate-pulse" : base;
