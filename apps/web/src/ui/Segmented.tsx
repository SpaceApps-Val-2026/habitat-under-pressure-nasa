// A segmented control in the design system's style (`.hup-seg.on-paper`): as wide as its options, the
// chosen one in the signal colour with its weight, never colour alone (aria-pressed and a bold label).
import type { ReactNode } from "react";

export type Segment<T extends string> = { value: T; label: string; icon?: ReactNode };

export function Segmented<T extends string>({
  labelledBy,
  value,
  options,
  onChange,
}: {
  labelledBy: string;
  value: T;
  options: Segment<T>[];
  onChange: (value: T) => void;
}) {
  return (
    <div
      role="group"
      aria-labelledby={labelledBy}
      className="inline-flex w-fit max-w-full flex-wrap gap-2 rounded-md border border-line-strong bg-paper p-2"
    >
      {options.map((option) => {
        const chosen = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={chosen}
            onClick={() => onChange(option.value)}
            className={`control inline-flex min-h-9 items-center gap-6 rounded-sm px-12 py-4 transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus ${
              chosen ? "bg-signal-soft font-semibold text-signal" : "text-muted hover:bg-soft hover:text-ink"
            }`}
          >
            {option.icon && (
              <span aria-hidden="true" className="inline-flex size-16 items-center justify-center">
                {option.icon}
              </span>
            )}
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
