import { Fragment, useMemo, useState } from "react";
import { ChevronDown, ChevronRight, ChevronUp, ChevronsUpDown } from "lucide-react";

/**
 * One table for the whole dashboard: sortable headers, numbers right-aligned in
 * tabular figures, stable row keys, optional expandable detail, paging.
 *
 * Six tables each hand-rolled their own markup, none sortable, numeric columns
 * left-aligned (so 1,234.50 and 99.00 did not line up), and rows keyed by array
 * index -- which mislabels expanded rows the moment the order changes.
 *
 * columns: [{ key, label, numeric?, value?(row), render?(row), title?, sortable?, align? }]
 *   sortable: false -> a plain header (e.g. an action column)
 *   align: "right"  -> right-align a non-numeric column
 *   value  -> what to sort on (defaults to row[key]); missing values sort last
 *   render -> what to show (defaults to the value)
 * footer:  optional { [key]: node } rendered as a totals row
 */
export default function DataTable({
  columns,
  rows,
  rowKey,
  defaultSort = null,
  expand = null,
  pageSize = null,
  empty = "Nothing to show.",
  footer = null,
  caption = null,
}) {
  const [sort, setSort] = useState(defaultSort);
  const [expanded, setExpanded] = useState(() => new Set());
  const [page, setPage] = useState(0);

  const valueOf = (col, row) => (col.value ? col.value(row) : row[col.key]);

  const sorted = useMemo(() => {
    if (!sort) return rows;
    const col = columns.find((c) => c.key === sort.key);
    if (!col) return rows;
    const dir = sort.dir === "desc" ? -1 : 1;
    const missing = (v) => v === null || v === undefined || v === "" || (typeof v === "number" && !Number.isFinite(v));
    return [...rows].sort((a, b) => {
      const va = valueOf(col, a);
      const vb = valueOf(col, b);
      // Missing values sort last in either direction: a gap is never "the smallest".
      if (missing(va) && missing(vb)) return 0;
      if (missing(va)) return 1;
      if (missing(vb)) return -1;
      if (col.numeric) return (Number(va) - Number(vb)) * dir;
      return String(va).localeCompare(String(vb), undefined, { numeric: true }) * dir;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, sort, columns]);

  const pageCount = pageSize ? Math.max(1, Math.ceil(sorted.length / pageSize)) : 1;
  const safePage = Math.min(page, pageCount - 1);
  const visible = pageSize ? sorted.slice(safePage * pageSize, (safePage + 1) * pageSize) : sorted;

  const toggleSort = (key) => {
    setPage(0);
    setSort((s) => (s?.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" }));
  };

  const toggleRow = (key) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const colCount = columns.length + (expand ? 1 : 0);
  const align = (col) => (col.numeric || col.align === "right" ? "text-right" : "text-left");

  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm whitespace-nowrap">
          {caption ? <caption className="sr-only">{caption}</caption> : null}
          <thead className="bg-[var(--color-panel-2)] border-b border-[var(--color-border)] text-[var(--color-subtle)] text-xs uppercase tracking-wider sticky top-0">
            <tr>
              {expand ? <th scope="col" className="w-8 px-2 py-3"><span className="sr-only">Details</span></th> : null}
              {columns.map((col) => {
                if (col.sortable === false) {
                  return (
                    <th key={col.key} scope="col" title={col.title} className={`px-4 py-3 font-bold ${col.align === "right" ? "text-right" : align(col)}`}>
                      {col.label}
                    </th>
                  );
                }
                const active = sort?.key === col.key;
                const Icon = !active ? ChevronsUpDown : sort.dir === "asc" ? ChevronUp : ChevronDown;
                return (
                  <th
                    key={col.key}
                    scope="col"
                    title={col.title}
                    aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}
                    className={`px-4 py-3 font-bold ${align(col)}`}
                  >
                    <button
                      type="button"
                      onClick={() => toggleSort(col.key)}
                      className={`inline-flex items-center gap-1 uppercase tracking-wider hover:text-[var(--color-text)] focus-visible:ring-2 focus-visible:ring-[var(--color-accent)] rounded ${col.numeric ? "flex-row-reverse" : ""} ${active ? "text-[var(--color-text)]" : ""}`}
                    >
                      {col.label}
                      <Icon className="w-3 h-3 shrink-0 opacity-70" aria-hidden="true" />
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--color-border)] text-[var(--color-text)]">
            {visible.length === 0 ? (
              <tr>
                <td colSpan={colCount} className="px-4 py-4 text-center text-[var(--color-muted)]">{empty}</td>
              </tr>
            ) : (
              visible.map((row) => {
                const key = rowKey(row);
                const open = expanded.has(key);
                return (
                  <Fragment key={key}>
                    <tr className="hover:bg-[var(--color-bg-soft)] transition-colors">
                      {expand ? (
                        <td className="px-2 py-2">
                          <button
                            type="button"
                            onClick={() => toggleRow(key)}
                            aria-expanded={open}
                            aria-label={open ? "Hide details" : "Show details"}
                            className="p-1 rounded text-[var(--color-muted)] hover:text-[var(--color-text)] focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]"
                          >
                            {open ? <ChevronDown className="w-4 h-4" aria-hidden="true" /> : <ChevronRight className="w-4 h-4" aria-hidden="true" />}
                          </button>
                        </td>
                      ) : null}
                      {columns.map((col) => (
                        <td
                          key={col.key}
                          className={`px-4 py-2.5 ${align(col)} ${col.numeric ? "font-mono tabular-nums" : ""} ${col.className || ""}`}
                        >
                          {col.render ? col.render(row) : (valueOf(col, row) ?? "—")}
                        </td>
                      ))}
                    </tr>
                    {expand && open ? (
                      <tr className="bg-[var(--color-bg)]">
                        <td colSpan={colCount} className="px-4 py-3 whitespace-normal">{expand(row)}</td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })
            )}
          </tbody>
          {footer && visible.length > 0 ? (
            <tfoot className="border-t-2 border-[var(--color-border-strong)] text-[var(--color-text)] font-bold">
              <tr>
                {expand ? <td /> : null}
                {columns.map((col) => (
                  <td key={col.key} className={`px-4 py-2.5 ${align(col)} ${col.numeric ? "font-mono tabular-nums" : ""}`}>
                    {footer[col.key] ?? ""}
                  </td>
                ))}
              </tr>
            </tfoot>
          ) : null}
        </table>
      </div>
      {pageSize && sorted.length > pageSize ? (
        <div className="flex items-center justify-between px-4 py-2 border-t border-[var(--color-border)] text-xs text-[var(--color-muted)]">
          <span>
            {safePage * pageSize + 1}–{Math.min((safePage + 1) * pageSize, sorted.length)} of {sorted.length}
          </span>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={safePage === 0}
              onClick={() => setPage(safePage - 1)}
              className="px-2 py-1 rounded border border-[var(--color-border)] disabled:opacity-40 hover:text-[var(--color-text)]"
            >
              Previous
            </button>
            <button
              type="button"
              disabled={safePage >= pageCount - 1}
              onClick={() => setPage(safePage + 1)}
              className="px-2 py-1 rounded border border-[var(--color-border)] disabled:opacity-40 hover:text-[var(--color-text)]"
            >
              Next
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
