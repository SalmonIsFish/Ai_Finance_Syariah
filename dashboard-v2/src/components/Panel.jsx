/** The bordered surface every section sits on -- one definition, not fifteen copies. */
export default function Panel({ children, className = "", padded = true }) {
  return (
    <div
      className={`bg-[var(--color-panel)] border border-[var(--color-border)] rounded-md shadow-sm ${padded ? "p-6" : ""} ${className}`}
    >
      {children}
    </div>
  );
}
