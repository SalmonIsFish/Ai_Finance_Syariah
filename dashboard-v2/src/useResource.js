import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Fetch something, optionally keep it fresh, and say how old it is.
 *
 * Every page used to fetch once on mount and never again, with no time shown,
 * so a balance loaded at 09:00 looked identical at 15:00. This hook returns
 * `asOf` (when the data currently on screen was fetched) and `stale` (true when
 * a refresh is overdue, e.g. the last few polls failed). The UI shows both.
 *
 * A failed refresh keeps the last good data and sets `error` alongside it,
 * rather than blanking the panel: the operator sees the old figure, its time,
 * and that it could not be updated -- three true statements.
 *
 * Polling pauses while the tab is hidden and resumes with an immediate fetch
 * when it is shown again.
 */
export default function useResource(fetcher, { intervalMs = null } = {}) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [asOf, setAsOf] = useState(null);
  const [now, setNow] = useState(() => Date.now());
  const fetcherRef = useRef(fetcher);
  useEffect(() => {
    fetcherRef.current = fetcher;
  }, [fetcher]);
  const inFlight = useRef(false);
  const mounted = useRef(true);

  const refresh = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      const result = await fetcherRef.current();
      if (!mounted.current) return;
      setData(result);
      setError(null);
      setAsOf(new Date());
    } catch (err) {
      if (mounted.current) setError(err);
    } finally {
      inFlight.current = false;
      if (mounted.current) {
        setLoading(false);
        setNow(Date.now());
      }
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    refresh();
    return () => {
      mounted.current = false;
    };
  }, [refresh]);

  useEffect(() => {
    if (!intervalMs) return undefined;
    const tick = () => {
      setNow(Date.now());
      if (document.visibilityState === "visible") refresh();
    };
    const id = setInterval(tick, intervalMs);
    const onVisible = () => {
      if (document.visibilityState === "visible") refresh();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [intervalMs, refresh]);

  // Overdue by more than two intervals: at least two refreshes have been missed.
  const stale = Boolean(intervalMs && asOf && now - asOf.getTime() > intervalMs * 2.5);

  return { data, error, loading, asOf, stale, refresh };
}
