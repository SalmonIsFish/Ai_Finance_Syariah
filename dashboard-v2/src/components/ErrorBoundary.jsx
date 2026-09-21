import React from "react";
import { AlertTriangle } from "lucide-react";

/**
 * Keeps one bad page from taking down the whole dashboard.
 *
 * React unmounts the entire tree on an uncaught render error. With nothing
 * catching it, a single unexpected field blanked everything — nav, header and
 * all. That happened for real: `/news` returns `ai_summary` as an object rather
 * than a string, MarketScreening rendered it directly, and
 * `/dashboard/market` went completely black with no clue as to why.
 *
 * Wrapped around the routed outlet rather than the whole app, so the shell
 * survives and the other rooms stay reachable. The error text is shown rather
 * than hidden: this dashboard has one operator, and "something went wrong" would
 * cost them a trip to the console.
 */
export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error("Render error in routed page:", error, info?.componentStack);
  }

  componentDidUpdate(prevProps) {
    // Reset on navigation, otherwise a failure on one room would persist as the
    // operator moves to a working one.
    if (prevProps.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null });
    }
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      <div className="bg-[var(--color-panel)] border border-[var(--color-bad)] rounded-md p-6">
        <div className="flex items-start gap-3">
          <AlertTriangle className="w-5 h-5 text-[var(--color-bad)] shrink-0 mt-0.5" />
          <div className="min-w-0">
            <h2 className="font-serif font-bold text-lg text-[var(--color-text)]">
              This page failed to render
            </h2>
            <p className="text-sm text-[var(--color-muted)] mt-1">
              The rest of the dashboard still works — use the navigation to carry on.
            </p>
            <pre className="mt-3 text-xs text-[var(--color-bad)] whitespace-pre-wrap break-words font-mono">
              {String(error?.message || error)}
            </pre>
          </div>
        </div>
      </div>
    );
  }
}
