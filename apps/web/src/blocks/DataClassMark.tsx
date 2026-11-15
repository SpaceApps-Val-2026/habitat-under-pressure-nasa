import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";

import { useI18n } from "../i18n";
import type { MessageKey } from "../i18n/messages";
import { DATA_CLASS, MARKED_CLASSES } from "./shared";

import { usePresence } from "../ui/presence";

import "./data-class-mark.css";

// Data that is not a direct measurement (modelled, extrapolated, approximated, illustrative) carries a
// large asterisk after its value instead of a tag; the asterisk opens what the class means. A measured
// value keeps its solid tag and gets no asterisk.

const NOTE: Record<string, MessageKey> = {
  reanalysis: "dataClassNote.reanalysis",
  extrapolated: "dataClassNote.extrapolated",
  approximated: "dataClassNote.approximated",
  illustrative: "dataClassNote.illustrative",
};

const EDGE = 8;

export function DataClassMark({ value }: { value: string | undefined }) {
  const { t } = useI18n();
  const id = useId();
  const [open, setOpen] = useState(false);
  const [place, setPlace] = useState<{ shift: number; above: boolean }>({ shift: 0, above: false });
  const root = useRef<HTMLSpanElement>(null);
  const tip = useRef<HTMLSpanElement>(null);
  const { mounted, closing } = usePresence(open);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    const onPointer = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointer);
    };
  }, [open]);

  // Never clipped: shifted back inside the viewport sideways, and flipped above when there is no room below.
  useLayoutEffect(() => {
    if (!open || !tip.current || !root.current) return;
    const box = tip.current.getBoundingClientRect();
    const anchor = root.current.getBoundingClientRect();
    const width = window.innerWidth;
    let shift = 0;
    if (box.right > width - EDGE) shift = width - EDGE - box.right;
    if (box.left + shift < EDGE) shift = EDGE - box.left;
    const above = anchor.bottom + box.height + EDGE > window.innerHeight && anchor.top > box.height + EDGE;
    setPlace((current) => (current.shift === shift && current.above === above ? current : { shift, above }));
  }, [open]);

  if (!value || !MARKED_CLASSES.has(value)) return null;
  const name = value === "illustrative" ? t("blocks.illustrativeValue") : DATA_CLASS[value] ? t(DATA_CLASS[value]) : value;

  return (
    <span
      ref={root}
      className="data-mark"
      data-class={value}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        className="data-mark-button"
        aria-label={name}
        aria-describedby={id}
        aria-expanded={open}
        onClick={() => setOpen(true)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
      >
        *
      </button>
      <span
        ref={tip}
        id={id}
        role="tooltip"
        className={`data-mark-tip fade-in${place.above ? " above" : ""}${closing ? " fade-out" : ""}`}
        style={{ transform: `translateX(${place.shift}px)` }}
        hidden={!mounted}
      >
        <b>{name}</b>
        <span>{NOTE[value] ? t(NOTE[value]) : ""}</span>
      </span>
      {/* On paper a tooltip cannot open: the class is written instead. */}
      <span className="data-mark-print">{name}</span>
    </span>
  );
}

// The group a class reads as in the legend line, and the colour its asterisk takes.
const GROUP: Record<string, "modelled" | "approximated" | "illustrative"> = {
  reanalysis: "modelled",
  extrapolated: "modelled",
  approximated: "approximated",
  illustrative: "illustrative",
};

// One short line under the figures: the same asterisk, one per colour present, and the names of only the
// classes this report shows. On paper the invitation to tap is left out.
export function DataClassLegend({ classes }: { classes: (string | undefined)[] }) {
  const { t } = useI18n();
  const groups = [...new Set(classes.flatMap((value) => (value && GROUP[value] ? [GROUP[value]] : [])))];
  if (groups.length === 0) return null;
  const order = ["modelled", "approximated", "illustrative"] as const;
  const present = order.filter((group) => groups.includes(group));
  const glyphs = [
    ...(present.some((group) => group !== "illustrative") ? ["reanalysis"] : []),
    ...(present.includes("illustrative") ? ["illustrative"] : []),
  ];
  const names = present.map((group) => t(`report.markNames.${group}` as MessageKey));
  const text =
    names.length > 1 ? `${names.slice(0, -1).join(", ")}${t("report.markOr")}${names[names.length - 1]}` : names[0];
  return (
    <p className="data-mark-legend">
      {glyphs.map((value) => (
        <span key={value} className="data-mark-glyph" data-class={value} aria-hidden="true">
          *
        </span>
      ))}{" "}
      {text}
      <span className="data-mark-hint">{t("report.markHint")}</span>
    </p>
  );
}
