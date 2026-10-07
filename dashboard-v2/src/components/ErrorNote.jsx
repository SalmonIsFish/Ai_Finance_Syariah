/**
 * A panel that could not load says so, where the data would have been.
 *
 * Failed sections used to go to console.error only, leaving an empty panel or a
 * row of zeros that looked like real, quiet data. An operator cannot act on a
 * failure they are never shown.
 */
export default function ErrorNote({ what, error }) {
  const message = error?.message || (typeof error === "string" ? error : null);
  return (
    <div
      role="status"
      className="px-3 py-2 rounded text-sm bg-[var(--color-bad-bg)] text-[var(--color-bad)] border border-[var(--color-bad)]"
    >
      Couldn’t load {what}
      {message ? ` — ${message}` : "."}
    </div>
  );
}
