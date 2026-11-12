/**
 * Elapsed-seconds ticker for the run header. Starts counting from
 * `startIso` and updates every second while the hook is mounted.
 *
 * `freeze` stops the tick (the run has finished; the number should
 * stay put). `startIso: null` means the run hasn't begun yet —
 * returns 0 and doesn't schedule a timer.
 */
import { useEffect, useState } from "react";

export function useElapsed(startIso: string | null, freeze = false): number {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!startIso || freeze) return undefined;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [startIso, freeze]);

  if (!startIso) return 0;
  const start = new Date(startIso).getTime();
  if (!Number.isFinite(start)) return 0;
  return Math.max(0, Math.floor((now - start) / 1000));
}

/** "0:03", "1:24", "12:05" — mm:ss, grows past 60 min as "73:12". */
export function formatElapsed(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}
