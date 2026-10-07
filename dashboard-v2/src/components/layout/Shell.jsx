import { Outlet, NavLink, useLocation } from "react-router-dom";
import { Moon, Sun, Briefcase, Activity, BarChart2, BookOpen, ListChecks, Menu, X } from "lucide-react";
import { useState, useEffect } from "react";
import ErrorBoundary from "../ErrorBoundary";
import StatusBar from "../StatusBar";

/** A stored choice wins; otherwise follow the OS. Storage can be blocked -- never let that break the page. */
function initialTheme() {
  try {
    const stored = localStorage.getItem("theme");
    if (stored === "dark" || stored === "light") return stored;
  } catch {
    /* storage unavailable: fall through to the OS preference */
  }
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

const NAV_ITEMS = [
  { name: "The Desk", to: "/", icon: Briefcase },
  { name: "Portfolio & Risk", to: "/risk", icon: Activity },
  { name: "Market & Screening", to: "/market", icon: BarChart2 },
  { name: "Shariah Universe", to: "/securities", icon: ListChecks },
  { name: "The Ledger", to: "/ledger", icon: BookOpen },
];

export default function Shell() {
  const location = useLocation();
  const [theme, setTheme] = useState(initialTheme);
  // Below lg the sidebar is a drawer. It used to be a fixed 256px column at
  // every width, which left a phone about 120px for the actual content.
  const [navOpen, setNavOpen] = useState(false);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", theme === "dark" ? "#0b0f1a" : "#f4f6f9");
    try {
      localStorage.setItem("theme", theme);
    } catch {
      /* storage unavailable: the choice lasts for this visit only */
    }
  }, [theme]);

  // The drawer closes when a link is chosen (onClick below) and on Escape.
  useEffect(() => {
    if (!navOpen) return undefined;
    const onKey = (e) => {
      if (e.key === "Escape") setNavOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [navOpen]);

  const toggleTheme = () => setTheme(theme === "dark" ? "light" : "dark");

  return (
    <div className="flex h-screen w-full bg-[var(--color-bg)] text-[var(--color-text)]">
      {/* First focusable element: keyboard users skip the nav and status bar. */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:px-4 focus:py-2 focus:rounded focus:bg-[var(--color-panel-3)] focus:text-[var(--color-text)]"
      >
        Skip to main content
      </a>

      {navOpen ? (
        <div
          className="fixed inset-0 z-30 bg-black/50 lg:hidden"
          aria-hidden="true"
          onClick={() => setNavOpen(false)}
        />
      ) : null}

      <aside
        id="primary-nav"
        className={`fixed inset-y-0 left-0 z-40 w-64 bg-[var(--color-panel)] border-r border-[var(--color-border)] flex flex-col transition-transform lg:static lg:translate-x-0 ${
          navOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="h-16 flex items-center justify-between px-6 border-b border-[var(--color-border)]">
          {/* The brand, not a heading: each page owns its single h1. */}
          <span className="font-serif font-bold text-lg text-[var(--color-accent)]">Amanah Trader</span>
          <button
            type="button"
            onClick={() => setNavOpen(false)}
            aria-label="Close navigation"
            className="p-1.5 rounded-md text-[var(--color-muted)] hover:text-[var(--color-text)] lg:hidden"
          >
            <X className="w-5 h-5" aria-hidden="true" />
          </button>
        </div>
        <nav aria-label="Primary" className="flex-1 p-4 space-y-2">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.name}
              to={item.to}
              end={item.to === "/"}
              onClick={() => setNavOpen(false)}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2 rounded-md font-sans text-sm transition-colors ${
                  isActive
                    ? "bg-[var(--color-panel-2)] text-[var(--color-text)]"
                    : "text-[var(--color-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-panel-2)]"
                }`
              }
            >
              <item.icon className="w-5 h-5" aria-hidden="true" />
              {item.name}
            </NavLink>
          ))}
        </nav>
      </aside>

      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {/* Was a static "System Status" title with no status behind it. Now the
            live answer to "paper or live, is the broker up, is the market open,
            and how fresh is this?" -- visible on every page. */}
        <header className="min-h-16 py-2 flex items-center justify-between gap-3 px-4 lg:px-8 border-b border-[var(--color-border)] bg-[var(--color-bg)]">
          <button
            type="button"
            onClick={() => setNavOpen(true)}
            aria-label="Open navigation"
            aria-controls="primary-nav"
            aria-expanded={navOpen}
            className="p-2 -ml-2 rounded-md text-[var(--color-muted)] hover:text-[var(--color-text)] lg:hidden"
          >
            <Menu className="w-5 h-5" aria-hidden="true" />
          </button>
          <div className="flex-1 min-w-0">
            <StatusBar />
          </div>
          <button
            type="button"
            onClick={toggleTheme}
            aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
            className="shrink-0 p-2 rounded-md text-[var(--color-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-panel)]"
          >
            {theme === "dark" ? <Sun className="w-5 h-5" aria-hidden="true" /> : <Moon className="w-5 h-5" aria-hidden="true" />}
          </button>
        </header>

        <main id="main-content" tabIndex={-1} className="flex-1 overflow-auto p-4 sm:p-6 lg:p-8 focus:outline-none">
          <div className="max-w-6xl mx-auto space-y-6">
            <ErrorBoundary resetKey={location.pathname}>
              <Outlet />
            </ErrorBoundary>
          </div>
        </main>
      </div>
    </div>
  );
}
