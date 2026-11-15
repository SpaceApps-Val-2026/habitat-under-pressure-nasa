// Keeps a floating layer mounted while it fades out (.fade-out in motion.css). The wait is the design
// system's --motion-fade: 0 ms with reduced motion or without the stylesheet, and then nothing lingers.
import { useEffect, useState } from "react";

function fadeMs(): number {
  const value = getComputedStyle(document.documentElement).getPropertyValue("--motion-fade").trim();
  const ms = value.endsWith("ms") ? parseFloat(value) : parseFloat(value) * 1000;
  return Number.isFinite(ms) ? ms : 0;
}

export function usePresence(open: boolean): { mounted: boolean; closing: boolean } {
  const [shown, setShown] = useState(open);
  if (open && !shown) setShown(true);
  const wait = !open && shown ? fadeMs() : 0;
  if (!open && shown && wait === 0) setShown(false);

  useEffect(() => {
    if (wait === 0) return;
    const timer = setTimeout(() => setShown(false), wait);
    return () => clearTimeout(timer);
  }, [wait]);

  return { mounted: open || wait > 0, closing: wait > 0 };
}
