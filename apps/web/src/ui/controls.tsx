// One control language for every form: the same height, border, radius, focus ring and hover,
// all from the theme tokens. Screens compose these instead of repeating class strings.
import { cloneElement, isValidElement, type ButtonHTMLAttributes, type ReactElement, type ReactNode } from "react";

import "./controls.css";

const BASE =
  "body min-h-11 rounded-md border bg-paper text-ink transition-colors " +
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus " +
  "disabled:opacity-50";

// Text fields, selects and number fields share this; only the typeface changes for numbers.
export const fieldClass = `${BASE} w-full border-line-strong px-10 py-8 hover:border-signal`;
export const numberFieldClass = `${fieldClass} data tabular-nums`;

const BUTTON = {
  primary:
    "body inline-flex min-h-11 items-center justify-center rounded-md bg-signal px-16 py-10 " +
    "font-semibold text-on-signal transition-colors hover:brightness-110 focus-visible:outline-2 " +
    "focus-visible:outline-offset-2 focus-visible:outline-focus disabled:opacity-50",
  secondary:
    "body inline-flex min-h-11 items-center justify-center rounded-md border border-line-strong " +
    "px-16 py-10 font-semibold text-ink transition-colors hover:border-signal focus-visible:outline-2 " +
    "focus-visible:outline-offset-2 focus-visible:outline-focus disabled:opacity-50",
} as const;

export function buttonClass(variant: keyof typeof BUTTON = "primary"): string {
  return BUTTON[variant];
}

export function Button({
  variant = "primary",
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: keyof typeof BUTTON }) {
  return <button {...props} className={`${buttonClass(variant)} ${className}`} />;
}

type Control = ReactElement<{ id?: string; "aria-describedby"?: string }>;

// A labelled control with its hint. The child keeps its own element (input, select...); the
// wrapper gives it the id and points the hint at it, so the label and the hint both read.
export function Field({
  id,
  label,
  hint,
  children,
}: {
  id: string;
  label: string;
  hint?: ReactNode;
  children: Control;
}) {
  const hintId = `${id}-hint`;
  const control = isValidElement(children)
    ? cloneElement(children, { id, "aria-describedby": hint ? hintId : undefined })
    : children;
  return (
    <div className="flex flex-col gap-4">
      <label className="label text-muted" htmlFor={id}>
        {label}
      </label>
      {control}
      {hint && (
        <p id={hintId} className="small text-muted">
          {hint}
        </p>
      )}
    </div>
  );
}

// A whole row is the target, not only the box: 40 px of height and the checked state in both
// border and background, never colour alone (the box keeps its mark).
export function CheckRow({
  id,
  checked,
  onChange,
  children,
}: {
  id: string;
  checked: boolean;
  onChange: () => void;
  children: ReactNode;
}) {
  return (
    <label
      htmlFor={id}
      className="body flex min-h-10 cursor-pointer items-center gap-10 rounded-md border border-line px-10 py-6 transition-colors hover:border-line-strong has-[:checked]:border-signal has-[:checked]:bg-signal-soft"
    >
      <input
        id={id}
        type="checkbox"
        checked={checked}
        onChange={onChange}
        className="size-5 shrink-0 accent-signal focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
      />
      <span>{children}</span>
    </label>
  );
}

// An on/off filter in the design system's style (`.hup-toggle`): a track and a knob, the label beside it.
export function Toggle({
  id,
  checked,
  onChange,
  children,
}: {
  id: string;
  checked: boolean;
  onChange: () => void;
  children: ReactNode;
}) {
  return (
    <label htmlFor={id} className="body inline-flex min-h-11 cursor-pointer items-center gap-10 text-ink">
      <input id={id} type="checkbox" role="switch" checked={checked} onChange={onChange} className="ui-toggle" />
      <span>{children}</span>
    </label>
  );
}

// A search field with its magnifier inside, on the left (`.hup-input` with an icon).
export function SearchField({
  id,
  value,
  onChange,
  placeholder,
}: {
  id: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  return (
    <div className="relative min-w-0">
      <svg
        viewBox="0 0 20 20"
        className="pointer-events-none absolute top-1/2 left-10 size-16 -translate-y-1/2 text-muted"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        aria-hidden="true"
      >
        <circle cx="8.5" cy="8.5" r="5.5" />
        <path d="M13 13l4.5 4.5" />
      </svg>
      <input
        id={id}
        type="search"
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        className={`${fieldClass} pl-40`}
      />
    </div>
  );
}
