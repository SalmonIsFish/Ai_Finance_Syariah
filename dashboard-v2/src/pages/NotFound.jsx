import { Link, useLocation } from "react-router-dom";

/** An unknown path says so, rather than rendering an empty shell. */
export default function NotFound() {
  const { pathname } = useLocation();
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-serif text-[var(--color-text)]">Page not found</h1>
      <p className="text-sm text-[var(--color-muted)]">
        There is no page at <span className="font-mono text-[var(--color-text)]">{pathname}</span>.
      </p>
      <Link to="/" className="text-sm font-bold text-[var(--color-accent)] hover:underline">
        Go to The Desk
      </Link>
    </div>
  );
}
