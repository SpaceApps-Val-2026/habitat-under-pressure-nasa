// The full text of something cut with an ellipsis, in the same box as the data-class tooltip of the report:
// on hover and on keyboard focus, closed by Escape or by leaving. It opens only when the text is really cut.
import { cloneElement, isValidElement, useEffect, useId, useLayoutEffect, useRef, useState, type ReactElement } from "react";

import { usePresence } from "./presence";

import "./tooltip.css";

type Trigger = ReactElement<{ "aria-describedby"?: string; title?: string }>;

// `inCard`: the anchor does not become the positioning box, so a link stretched over its card keeps covering
// the whole card; the tooltip then opens under the card.
export function Tooltip({
  text,
  children,
  block = false,
  inCard = false,
}: {
  text: string;
  children: Trigger;
  block?: boolean;
  inCard?: boolean;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLSpanElement>(null);
  const tip = useRef<HTMLSpanElement>(null);
  const [shift, setShift] = useState(0);
  const { mounted, closing } = usePresence(open);

  // Never clipped by the window's right or left edge.
  useLayoutEffect(() => {
    if (!open || !tip.current) return;
    const box = tip.current.getBoundingClientRect();
    setShift((current) => {
      // The box as it would sit without any shift.
      const left = box.left - current;
      const right = box.right - current;
      let next = 0;
      if (right > window.innerWidth - 8) next = window.innerWidth - 8 - right;
      if (left + next < 8) next = 8 - left;
      return next;
    });
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  // Only a text that does not fit gets a tooltip; without layout (a test, a print) it is assumed cut.
  function show() {
    const cut = root.current?.querySelector<HTMLElement>("[data-truncate]") ?? root.current;
    if (!cut || cut.clientWidth === 0 || cut.scrollWidth > cut.clientWidth) setOpen(true);
  }

  const trigger = isValidElement(children)
    ? cloneElement(children, { "aria-describedby": open ? id : undefined, title: text })
    : children;

  return (
    <span
      ref={root}
      className={`ui-tip-anchor${block ? " is-block" : ""}${inCard ? " in-card" : ""}`}
      onMouseEnter={show}
      onMouseLeave={() => setOpen(false)}
      onFocus={show}
      onBlur={() => setOpen(false)}
    >
      {trigger}
      {mounted && (
        <span ref={tip} id={id} role="tooltip" className={`ui-tip fade-in${closing ? " fade-out" : ""}`} style={{ transform: `translateX(${shift}px)` }}>
          {text}
        </span>
      )}
    </span>
  );
}
