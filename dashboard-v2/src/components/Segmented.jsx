/**
 * A row of mutually exclusive buttons -- a filter, or a side selector.
 *
 * Four pages each hand-rolled one, with different active styles (one painted
 * the active filter brass, which the design reserves for actions) and none
 * exposed which option was selected to assistive tech. This one sets
 * aria-pressed and takes its options as [value, label] pairs.
 */
export default function Segmented({ options, value, onChange, label, size = "sm" }) {
  const pad = size === "md" ? "px-4 py-2 text-sm" : "px-3 py-1.5 text-xs";
  return (
    <div role="group" aria-label={label} className="inline-flex rounded border border-[var(--color-border-strong)] overflow-hidden">
      {options.map(([key, text]) => (
        <button
          key={key}
          type="button"
          aria-pressed={value === key}
          onClick={() => onChange(key)}
          className={`${pad} font-bold border-r last:border-r-0 border-[var(--color-border)] focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--color-accent)] ${
            value === key
              ? "bg-[var(--color-panel-3)] text-[var(--color-text)]"
              : "bg-[var(--color-bg)] text-[var(--color-muted)] hover:text-[var(--color-text)]"
          }`}
        >
          {text}
        </button>
      ))}
    </div>
  );
}
