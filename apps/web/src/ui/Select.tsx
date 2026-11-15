// A drop-down in the design system's style (`.hup-listbox`): a button that opens a list of options, in
// place of the native <select> whose open list the browser draws. Keyboard as in the ARIA listbox
// pattern: arrows, Home, End, Enter or Space to choose, Escape or Tab to close, a letter jumps to the next
// option that starts with it. A click outside closes it; focus goes back to the button.
import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";

export type SelectOption = { value: string; label: string };

export function Select({
  id,
  label,
  value,
  options,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  options: SelectOption[];
  onChange: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const labelId = `${id}-label`;
  const listId = `${id}-list`;
  const optionId = useId();
  const selected = Math.max(
    0,
    options.findIndex((option) => option.value === value),
  );

  useEffect(() => {
    if (!open) return;
    list.current?.focus();
    const away = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    list.current?.querySelector(`[data-index="${active}"]`)?.scrollIntoView?.({ block: "nearest" });
  }, [open, active]);

  function show(at = selected) {
    setActive(at);
    setOpen(true);
  }

  function close() {
    setOpen(false);
    button.current?.focus();
  }

  function choose(index: number) {
    const option = options[index];
    if (option) onChange(option.value);
    close();
  }

  function onButtonKey(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      show();
    }
  }

  function onListKey(event: KeyboardEvent<HTMLUListElement>) {
    const last = options.length - 1;
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        setActive((index) => Math.min(last, index + 1));
        break;
      case "ArrowUp":
        event.preventDefault();
        setActive((index) => Math.max(0, index - 1));
        break;
      case "Home":
        event.preventDefault();
        setActive(0);
        break;
      case "End":
        event.preventDefault();
        setActive(last);
        break;
      case "Enter":
      case " ":
        event.preventDefault();
        choose(active);
        break;
      case "Escape":
        event.preventDefault();
        close();
        break;
      case "Tab":
        setOpen(false);
        break;
      default:
        if (event.key.length === 1 && /\S/.test(event.key)) {
          const letter = event.key.toLowerCase();
          const order = [...options.keys()].map((step) => (active + 1 + step) % options.length);
          const found = order.find((index) => options[index].label.toLowerCase().startsWith(letter));
          if (found !== undefined) setActive(found);
        }
    }
  }

  return (
    <div ref={root} className="flex min-w-0 flex-col gap-4">
      <span id={labelId} className="label text-muted">
        {label}
      </span>
      <div className="relative min-w-0">
        <button
          ref={button}
          id={id}
          type="button"
          aria-haspopup="listbox"
          aria-expanded={open}
          aria-controls={listId}
          aria-labelledby={`${labelId} ${id}`}
          onClick={() => (open ? close() : show())}
          onKeyDown={onButtonKey}
          className="body flex min-h-11 w-full items-center gap-8 rounded-md border border-line-strong bg-paper px-10 py-8 text-left text-ink transition-colors hover:border-signal focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
        >
          <span className="min-w-0 flex-1 truncate">{options[selected]?.label ?? ""}</span>
          <svg
            viewBox="0 0 12 12"
            className={`size-12 shrink-0 text-muted transition-transform ${open ? "rotate-180" : ""}`}
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M2.5 4.5L6 8l3.5-3.5" />
          </svg>
        </button>
        {open && (
          <ul
            ref={list}
            id={listId}
            role="listbox"
            tabIndex={-1}
            aria-labelledby={labelId}
            aria-activedescendant={`${optionId}-${active}`}
            onKeyDown={onListKey}
            className="fade-in absolute inset-x-0 top-[calc(100%+4px)] z-30 m-0 flex max-h-[288px] list-none flex-col overflow-y-auto rounded-md border border-line-strong bg-paper p-4 scroll-thin shadow-overlay outline-none"
          >
            {options.map((option, index) => (
              <li
                key={option.value}
                id={`${optionId}-${index}`}
                data-index={index}
                role="option"
                aria-selected={index === selected}
                onMouseEnter={() => setActive(index)}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => choose(index)}
                className={`body grid cursor-pointer grid-cols-[16px_1fr] items-center gap-6 rounded-sm py-8 pr-10 pl-6 ${
                  index === active ? "bg-soft" : ""
                } ${index === selected ? "font-semibold text-signal" : "text-ink"}`}
              >
                <span aria-hidden="true">
                  {index === selected && (
                    <svg viewBox="0 0 12 12" className="size-12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M2 6.5l2.5 2.5L10 3.5" />
                    </svg>
                  )}
                </span>
                <span>{option.label}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
