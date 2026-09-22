import { Outlet, NavLink, useLocation } from "react-router-dom";
import { Moon, Sun, Briefcase, Activity, BarChart2, BookOpen, ListChecks } from "lucide-react";
import { useState, useEffect } from "react";
import ErrorBoundary from "../ErrorBoundary";

export default function Shell() {
  const location = useLocation();
  const [theme, setTheme] = useState(
    () => localStorage.getItem("theme") || "dark"
  );

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("theme", theme);
  }, [theme]);

  const toggleTheme = () => setTheme(theme === "dark" ? "light" : "dark");

  const navItems = [
    { name: "The Desk", to: "/", icon: Briefcase },
    { name: "Portfolio & Risk", to: "/risk", icon: Activity },
    { name: "Market & Screening", to: "/market", icon: BarChart2 },
    { name: "Shariah Universe", to: "/securities", icon: ListChecks },
    { name: "The Ledger", to: "/ledger", icon: BookOpen },
  ];


  return (
    <div className="flex h-screen w-full bg-[var(--color-bg)] text-[var(--color-text)]">
      {/* Sidebar */}
      <aside className="w-64 bg-[var(--color-panel)] border-r border-[var(--color-border)] flex flex-col">
        <div className="h-16 flex items-center px-6 border-b border-[var(--color-border)]">
          <h1 className="font-serif font-bold text-lg text-[var(--color-accent)]">Amanah Trader</h1>
        </div>
        <nav className="flex-1 p-4 space-y-2">
          {navItems.map((item) => (
            <NavLink
              key={item.name}
              to={item.to}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2 rounded-md font-sans text-sm transition-colors ${
                  isActive
                    ? "bg-[var(--color-panel-2)] text-[var(--color-text)]"
                    : "text-[var(--color-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-panel-2)]"
                }`
              }
            >
              <item.icon className="w-5 h-5" />
              {item.name}
            </NavLink>
          ))}
        </nav>
      </aside>

      {/* Main Content */}
      <main className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {/* Header */}
        <header className="h-16 flex items-center justify-between px-8 border-b border-[var(--color-border)] bg-[var(--color-bg)]">
          <h2 className="font-sans font-medium text-lg">System Status</h2>
          <div className="flex items-center gap-4">
            <button
              onClick={toggleTheme}
              className="p-2 rounded-md text-[var(--color-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-panel)]"
            >
              {theme === "dark" ? <Sun className="w-5 h-5" /> : <Moon className="w-5 h-5" />}
            </button>
          </div>
        </header>

        {/* Page Content */}
        <div className="flex-1 overflow-auto p-8">
          <div className="max-w-6xl mx-auto space-y-6">
            <ErrorBoundary resetKey={location.pathname}>
              <Outlet />
            </ErrorBoundary>
          </div>
        </div>
      </main>
    </div>
  );
}
