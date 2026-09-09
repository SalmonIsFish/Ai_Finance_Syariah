"""Deterministic extractor for the SC Malaysia "List of Shariah-Compliant Securities" PDF.

Document structure (verified by direct inspection of the May 2026 publication,
41 pages, page size ~420x595pt):

  pages 1-16   cover / English methodology / Malay methodology -- no security data
  pages 17-18  Table 1: securities newly classified as Shariah-compliant since the
               prior publication (a change-highlight list, NOT the master roster;
               these grid-ruled pages are extractable via pdfplumber.find_tables())
  page 19      Table 2: securities newly reclassified as Shariah non-compliant
               (also a change-highlight list, grid-ruled, find_tables() works)
  page 20      Table 3: sector-level count summary (compliant / total / pct per
               sector, across Main+ACE+LEAP combined). This is a STATISTICS table,
               not a list of individual securities. Grid-ruled, find_tables() works.
               Table 3's per-sector counts are the reconciliation target: they sum
               to the publication's stated total (886 for May 2026).
  pages 21-39  the master list of individual Shariah-compliant securities, organized
               MAIN MARKET -> ACE MARKET -> LEAP MARKET, each market subdivided into
               sectors in the same order as Table 3. These pages have NO grid lines
               (find_tables() returns nothing useful) and are laid out in two visual
               columns per page with independent line-wrapping per column -- this is
               the layout that defeated the prior regex-on-linear-text parser and
               produced only 688 of 886 records. Reading order is: left column top
               to bottom, then right column top to bottom, continuing across pages;
               a sector's numbering (1, 2, 3, ...) can span a column or page break
               and only resets to 1 at a genuine sector boundary.
  page 40      "Additional list: other Shariah-compliant capital market instruments"
               -- currently one Islamic business trust. Not part of the Main/ACE/LEAP
               sector breakdown in Table 3; tracked as its own category.
  page 41      SC Malaysia contact information -- no data.

Two-column extraction strategy for pages 21-39 (no ruled grid to lean on):
  1. Pull words with position (x0, top) via pdfplumber.extract_words().
  2. Split into left/right columns by a fixed x threshold (empirically ~204pt on
     this page geometry -- the gap between the two "No./Bil." sub-tables never has
     a word straddling it).
  3. Within each column, walk the word stream in (top, x0) order looking for the
     bigram "<digits>.? <3-6 digit ticker>" -- this is the row-start marker. Every
     word between one row-start and the next belongs to the issuer name, regardless
     of how many lines the name wraps across. This sidesteps line-based regexing
     entirely, which is what previously broke on wrapped names.
  4. A sector boundary is inferred from the numbering resetting to 1, matched
     against Table 3's fixed sector order; the actual header text (when it renders
     cleanly enough after undoing a font-doubling artifact seen from page ~31
     onward) is kept alongside as `raw_header_text` for audit, but is NOT the
     authoritative sector assignment -- order-based inference is, because the font
     artifact is inconsistent and must not silently corrupt classification.

This module only extracts and classifies. It does not decide Shariah eligibility,
does not write to the SC Malaysia store, and does not activate anything. Its output
feeds sc_malaysia_import.py, which stages results in the same 'pending' /
'needs_reconciliation' workflow as any other publication ingestion.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

PARSER_VERSION = "pdfplumber-two-column-v1"

COLUMN_SPLIT_X = 204.0

# Sector order as it appears in Table 3 and is walked, per market, on pages 21-39.
# "Closed-end fund" is deliberately excluded: Table 3 states Nil/Nil for it in the
# May 2026 publication, so it produces no rows and would never be observed via the
# reset-to-1 heuristic.
SECTOR_ORDER = [
    "Industrial products and services",
    "Consumer products and services",
    "Technology",
    "Property",
    "Construction",
    "Energy",
    "Plantation",
    "Transportation and logistics",
    "Healthcare",
    "Telecommunications and media",
    "Utilities",
    "Financial services",
]

MARKET_ORDER = ["MAIN MARKET", "ACE MARKET", "LEAP MARKET"]

ROW_NO_RE = re.compile(r"^(\d+)\.?$")
TICKER_RE = re.compile(r"^\d{3,6}$")


@dataclass
class RawWord:
    text: str
    x0: float
    top: float


@dataclass
class MasterRecord:
    ticker: str
    issuer_name: str
    market: str
    sector: str | None
    sector_source: str  # "order_inference" | "header_text_match"
    entry_no: int
    source_page: int


@dataclass
class ChangeRecord:
    ticker: str
    issuer_name: str
    source_page: int


@dataclass
class SectorSummaryRow:
    sector: str
    compliant_count: int
    total_count: int
    percent: float | None


@dataclass
class NumberingAnomaly:
    source_page: int
    market: str
    expected_next: int
    observed: int
    context: str


@dataclass
class ParseResult:
    main_list: list[MasterRecord] = field(default_factory=list)
    table1_newly_compliant: list[ChangeRecord] = field(default_factory=list)
    table2_newly_non_compliant: list[ChangeRecord] = field(default_factory=list)
    table3_summary: list[SectorSummaryRow] = field(default_factory=list)
    table3_official_total: int | None = None
    additional_instruments: list[ChangeRecord] = field(default_factory=list)
    numbering_anomalies: list[NumberingAnomaly] = field(default_factory=list)
    page_ranges: dict = field(default_factory=dict)
    source_document_hash: str = ""
    parser_version: str = PARSER_VERSION


def _collapse_doubled(word: str) -> str:
    """Undo the character-doubling font artifact seen in some sector headers
    from roughly page 31 onward (e.g. 'TTEECCHHNNOOLLOOGGYY' -> 'TECHNOLOGY').

    Only collapses when EVERY adjacent pair is identical -- a partially-doubled
    or naturally-repeated-letter word (seen at least once in this document,
    'PPRRODUUCCTTSS') is left untouched rather than mangled, and is surfaced as
    raw text for human review instead of being silently "fixed".
    """
    if len(word) < 2 or len(word) % 2 != 0:
        return word
    if all(word[i] == word[i + 1] for i in range(0, len(word), 2)):
        return word[0::2]
    return word


def _collapse_runs(text: str) -> str:
    """Collapse any run of a repeated character to one instance.

    More aggressive than _collapse_doubled and only safe for matching against
    the fixed SECTOR_ORDER names (none of which contain adjacent repeated
    letters), where it also fixes the one observed inconsistent-doubling
    artifact ('PPRRODUUCCTTSS' -> 'PRODUCTS') that _collapse_doubled correctly
    refuses to touch. Never applied to ticker or issuer-name data.
    """
    out: list[str] = []
    for ch in text:
        if not out or out[-1] != ch:
            out.append(ch)
    return "".join(out)


def _match_sector_header(fragment: str) -> str | None:
    """Identify which of the 12 known sectors a captured header fragment names.

    A substring search rather than a strict prefix match, because a fragment
    captured at a market's first section (e.g. "MAIN MARKET PASARAN UTAMA
    INDUSTRIAL PRODUCTS AND SERVICES ...") has the market name ahead of the
    sector name. When more than one sector name appears in the fragment, the
    leftmost is preferred (closest to how the header actually reads). Returns
    None (not a guess) when no known sector name appears at all -- callers
    fall back to order-based inference rather than have this function invent
    a match.
    """
    if not fragment:
        return None
    collapsed = _collapse_runs(fragment.upper())
    best_sector = None
    best_index = None
    for sector in SECTOR_ORDER:
        idx = collapsed.find(_collapse_runs(sector.upper()))
        if idx != -1 and (best_index is None or idx < best_index):
            best_index = idx
            best_sector = sector
    return best_sector


def _source_document_hash(pdf_path: Path) -> str:
    return hashlib.sha256(pdf_path.read_bytes()).hexdigest()


def _classify_pages(pdf) -> dict:
    """Single forward pass over page text, splitting the document into the
    zones described in the module docstring. Marker-driven rather than
    hardcoded page numbers, so a differently-paginated future publication
    (more/fewer securities, different cover length) still classifies correctly
    as long as the same section headings are present.
    """
    zones = {
        "table1": [],
        "table2": [],
        "table3": [],
        "main_list": [],
        "additional": [],
    }
    current = None
    for i, page in enumerate(pdf.pages):
        page_num = i + 1
        text = page.extract_text() or ""
        if "Table 1:" in text:
            current = "table1"
        if "Table 2:" in text:
            current = "table2"
        if re.search(r"\bTable 3\b", text) and "Table 3:" not in text:
            # Table 3 has no trailing colon in this publication ("Table 3" / "Jadual 3").
            current = "table3"
        if "MAIN MARKET" in text:
            current = "main_list"
        if "ADDITIONAL LIST" in text:
            current = "additional"
        if current is not None:
            zones[current].append(page_num)
    return zones


def _extract_table1_or_2(pdf, pages: list[int]) -> list[ChangeRecord]:
    """Extract from the grid-ruled Table 1 / Table 2 pages.

    Each ruled table on these pages has 6 columns: No, Code, Name, No, Code, Name
    (two side-by-side sub-tables sharing one grid). We keep only the ticker +
    name from each half whose code cell looks like a Bursa ticker.
    """
    records: list[ChangeRecord] = []
    for page_num in pages:
        page = pdf.pages[page_num - 1]
        for table in page.find_tables():
            if table.bbox[2] - table.bbox[0] < 100:
                continue  # the tiny page-number stub "table" in the margin
            rows = table.extract()
            for row in rows:
                if row and row[0] and row[0].strip() in {"No.\nBil.", "No.", "Bil."}:
                    continue
                halves = [(0, 1, 2), (3, 4, 5)] if len(row) >= 6 else [(0, 1, 2)]
                for no_idx, code_idx, name_idx in halves:
                    if code_idx >= len(row):
                        continue
                    code = (row[code_idx] or "").strip()
                    name = (row[name_idx] or "").strip().replace("\n", " ")
                    name = re.sub(r"\s+", " ", name)
                    if TICKER_RE.match(code) and name:
                        records.append(
                            ChangeRecord(ticker=code, issuer_name=name, source_page=page_num)
                        )
    return records


def _extract_table3(pdf, pages: list[int]) -> tuple[list[SectorSummaryRow], int | None]:
    rows_out: list[SectorSummaryRow] = []
    total: int | None = None
    for page_num in pages:
        page = pdf.pages[page_num - 1]
        for table in page.find_tables():
            if table.bbox[2] - table.bbox[0] < 100:
                continue
            rows = table.extract()
            for row in rows:
                if not row or len(row) < 4:
                    continue
                label = (row[0] or "").replace("\n", " ").strip()
                label = re.sub(r"\s+", " ", label)
                if not label or label.lower().startswith(("main / ace", "pasaran utama")):
                    continue
                compliant_raw = (row[1] or "").replace(",", "").strip()
                total_raw = (row[2] or "").replace(",", "").strip()
                pct_raw = (row[3] or "").strip()
                # The cell mixes English then Malay on wrapped lines with no
                # reliable delimiter once newlines collapse to spaces, so match
                # against the known fixed sector list rather than guess a split
                # point. A row whose text doesn't start with a known sector name
                # (e.g. "Closed-end fund", which has Nil/Nil and no ticker data
                # to reconcile against) is skipped, not force-matched.
                english_label = next((s for s in SECTOR_ORDER if label.startswith(s)), None)
                if label.lower().startswith("total") or label.lower().startswith("jumlah"):
                    if compliant_raw.isdigit():
                        total = int(compliant_raw)
                    continue
                if not compliant_raw.isdigit() or not total_raw.isdigit() or english_label is None:
                    continue
                pct = float(pct_raw) if pct_raw.replace(".", "", 1).isdigit() else None
                rows_out.append(
                    SectorSummaryRow(
                        sector=english_label,
                        compliant_count=int(compliant_raw),
                        total_count=int(total_raw),
                        percent=pct,
                    )
                )
    return rows_out, total


def _extract_additional_list(pdf, pages: list[int]) -> list[ChangeRecord]:
    records: list[ChangeRecord] = []
    row_re = re.compile(r"^(\d+)\.?\s+(\d{3,6})\s+(.+)$")
    for page_num in pages:
        page = pdf.pages[page_num - 1]
        text = page.extract_text() or ""
        for line in text.split("\n"):
            m = row_re.match(line.strip())
            if m:
                records.append(
                    ChangeRecord(
                        ticker=m.group(2), issuer_name=m.group(3).strip(), source_page=page_num
                    )
                )
    return records


def _words_by_column(page) -> tuple[list[RawWord], list[RawWord]]:
    words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
    left = [
        RawWord(w["text"], w["x0"], w["top"])
        for w in words
        if w["x0"] < COLUMN_SPLIT_X and w["top"] > 40
    ]
    right = [
        RawWord(w["text"], w["x0"], w["top"])
        for w in words
        if w["x0"] >= COLUMN_SPLIT_X and w["top"] > 40
    ]
    left.sort(key=lambda w: (round(w.top, 1), w.x0))
    right.sort(key=lambda w: (round(w.top, 1), w.x0))
    return left, right


def _next_sector(current_index: int) -> tuple[int, str | None]:
    next_index = current_index + 1
    if next_index >= len(SECTOR_ORDER):
        return current_index, None
    return next_index, SECTOR_ORDER[next_index]


@dataclass
class _ColumnState:
    market: str
    sector_index: int  # order-inference fallback; -1 = no sector assigned yet
    current_sector: str | None
    current_sector_source: str  # "header_text_match" | "order_inference" | "column_boundary_carry"
    prev_entry_no: int | None


def _scan_column(
    column_words: list[RawWord],
    state: _ColumnState,
    *,
    page_num: int,
    anomalies: list[NumberingAnomaly],
) -> list[MasterRecord]:
    """Bigram-scan one column's word stream for '<no>. <ticker> <name...>' rows.

    A section header can appear either before the first row of a column (the
    ordinary case) or immediately after the last row of the PRECEDING section
    with no row boundary between them (a section ending and a new one starting
    mid-column). To catch the second case, name-word accumulation for a row
    stops the moment it hits a run of two or more consecutive ALL-CAPS words --
    every section header in this document is immediately followed by its Malay
    translation, also ALL-CAPS, so a genuine header is reliably two-or-more caps
    words in a row, whereas an issuer name has at most one standalone acronym
    word (e.g. "IHH Healthcare Bhd") before returning to title case. Whatever
    is captured this way is deferred and matched against SECTOR_ORDER when the
    next row starts, taking priority over numbering-reset inference -- a
    numbering reset is not fully reliable on its own: at least one place in
    this document (LEAP Market, page 38) has a column-initial row block with NO
    preceding header whose numbering happens to continue contiguously from the
    prior block's numbering purely by coincidence, which would silently
    mislabel the sector under numbering alone.

    Mutates `state` in place and returns the extracted records.
    """
    records: list[MasterRecord] = []
    pending_header: list[str] = []
    i = 0
    n = len(column_words)
    while i < n:
        tok = column_words[i]
        if ROW_NO_RE.match(tok.text) and i + 1 < n and TICKER_RE.match(column_words[i + 1].text):
            entry_no = int(tok.text.rstrip("."))
            ticker = column_words[i + 1].text

            # Decide THIS row's sector from whatever header text the PREVIOUS
            # row's trailing span deferred here -- before computing this row's
            # own trailing span below, which must not leak into this decision.
            header_text = " ".join(pending_header)
            pending_header = []
            header_match = _match_sector_header(header_text)

            j = i + 2
            span_start = j
            while j < n and not (
                ROW_NO_RE.match(column_words[j].text)
                and j + 1 < n
                and TICKER_RE.match(column_words[j + 1].text)
            ):
                j += 1
            span = [w.text for w in column_words[span_start:j]]

            # A trailing section header (English + Malay, both ALL-CAPS) is
            # only recognized when the literal recurring column-label token
            # "No." also appears later in this span -- an ALL-CAPS run alone
            # is NOT sufficient, because a genuine issuer name can itself be
            # two or more consecutive ALL-CAPS acronym words (e.g. "PNE PCB
            # Bhd", "IFCA MSC Bhd") with no header following at all. Requiring
            # both signals together avoids misfiling those as header text.
            # This deferred text belongs to whatever row comes NEXT, not to
            # this row -- it is only appended to pending_header, never used to
            # influence the sector decision just made above for this row.
            header_start = None
            if "No." in span:
                no_idx = span.index("No.")
                boundary = no_idx
                while (
                    boundary > 0 and span[boundary - 1].isupper() and len(span[boundary - 1]) >= 2
                ):
                    boundary -= 1
                header_start = boundary
            if header_start is not None:
                name = " ".join(span[:header_start]).strip()
                pending_header.extend(span[header_start:])
            else:
                name = " ".join(span).strip()

            # Clean up LEAP Market bilingual footnote bleed-in
            import re
            name = re.split(r'\s*(?:\* For Sophisticated Investors|Markets and Services Act|Untuk pelabur sofistikated|di bawah Akta Pasaran)', name, flags=re.IGNORECASE)[0].strip()

            if header_match is not None:
                state.current_sector = header_match
                state.current_sector_source = "header_text_match"
                state.sector_index = SECTOR_ORDER.index(header_match)
                state.prev_entry_no = None
            elif entry_no == 1:
                if state.sector_index == -1:
                    state.sector_index = 0
                else:
                    state.sector_index, _ = _next_sector(state.sector_index)
                state.current_sector = (
                    SECTOR_ORDER[state.sector_index]
                    if 0 <= state.sector_index < len(SECTOR_ORDER)
                    else None
                )
                state.current_sector_source = "order_inference"
                state.prev_entry_no = None
            else:
                # Continuation: no header preceded this row and the numbering
                # didn't reset to 1, so the sector established by the most
                # recent header match or reset stays in force. This is the
                # ordinary case for a sector's list spanning a column or page
                # break with no repeated header.
                if state.prev_entry_no is not None and entry_no != state.prev_entry_no + 1:
                    anomalies.append(
                        NumberingAnomaly(
                            source_page=page_num,
                            market=state.market,
                            expected_next=state.prev_entry_no + 1,
                            observed=entry_no,
                            context=f"{ticker} {name}",
                        )
                    )

            records.append(
                MasterRecord(
                    ticker=ticker,
                    issuer_name=name,
                    market=state.market,
                    sector=state.current_sector,
                    sector_source=state.current_sector_source,
                    entry_no=entry_no,
                    source_page=page_num,
                )
            )
            state.prev_entry_no = entry_no
            i = j
        else:
            pending_header.append(tok.text)
            i += 1
    return records


def _extract_main_list(pdf, pages: list[int]) -> tuple[list[MasterRecord], list[NumberingAnomaly]]:
    records: list[MasterRecord] = []
    anomalies: list[NumberingAnomaly] = []

    state = _ColumnState(
        market="MAIN MARKET",
        sector_index=-1,
        current_sector=None,
        current_sector_source="order_inference",
        prev_entry_no=None,
    )

    for page_num in pages:
        page = pdf.pages[page_num - 1]
        text = page.extract_text() or ""
        for marker in MARKET_ORDER:
            if marker in text:
                state.market = marker
                state.sector_index = -1
                state.current_sector = None
                state.prev_entry_no = None

        left, right = _words_by_column(page)
        for column_words in (left, right):
            col_records = _scan_column(column_words, state, page_num=page_num, anomalies=anomalies)
            records.extend(col_records)

    return records, anomalies


def parse_pdf(pdf_path: str | Path) -> ParseResult:
    import pdfplumber

    pdf_path = Path(pdf_path)
    result = ParseResult(source_document_hash=_source_document_hash(pdf_path))

    with pdfplumber.open(pdf_path) as pdf:
        zones = _classify_pages(pdf)
        result.page_ranges = zones

        result.table1_newly_compliant = _extract_table1_or_2(pdf, zones["table1"])
        result.table2_newly_non_compliant = _extract_table1_or_2(pdf, zones["table2"])
        result.table3_summary, result.table3_official_total = _extract_table3(pdf, zones["table3"])
        result.additional_instruments = _extract_additional_list(pdf, zones["additional"])
        result.main_list, result.numbering_anomalies = _extract_main_list(pdf, zones["main_list"])

    return result


def reconcile(result: ParseResult) -> dict:
    """Produce the count-reconciliation report required before any human review.

    Never fabricates an explanation for a discrepancy it cannot support from the
    parsed evidence -- an unresolved gap is reported as unresolved.
    """
    unique_tickers = {r.ticker for r in result.main_list}
    ticker_counts: dict[str, int] = {}
    for r in result.main_list:
        ticker_counts[r.ticker] = ticker_counts.get(r.ticker, 0) + 1
    duplicate_tickers = {t: c for t, c in ticker_counts.items() if c > 1}

    invalid_records = [
        r
        for r in result.main_list
        if not r.issuer_name or not TICKER_RE.match(r.ticker) or r.sector is None
    ]

    sector_table_total = sum(row.compliant_count for row in result.table3_summary)

    per_sector_parsed: dict[str, int] = {}
    for r in result.main_list:
        key = r.sector or "UNASSIGNED"
        per_sector_parsed[key] = per_sector_parsed.get(key, 0) + 1

    sector_diffs = []
    for row in result.table3_summary:
        parsed = per_sector_parsed.get(row.sector, 0)
        sector_diffs.append(
            {
                "sector": row.sector,
                "table3_compliant": row.compliant_count,
                "parsed_count": parsed,
                "diff": parsed - row.compliant_count,
            }
        )

    official_total = result.table3_official_total
    parsed_total = len(result.main_list)
    unique_total = len(unique_tickers)

    header_confirmed_count = sum(
        1 for r in result.main_list if r.sector_source == "header_text_match"
    )

    return {
        "official_stated_total": official_total,
        "table3_sector_sum": sector_table_total,
        "parsed_record_count": parsed_total,
        "unique_ticker_count": unique_total,
        "duplicate_record_count": sum(c - 1 for c in duplicate_tickers.values()),
        "duplicate_tickers": duplicate_tickers,
        "invalid_record_count": len(invalid_records),
        "invalid_records": [
            {
                "ticker": r.ticker,
                "issuer_name": r.issuer_name,
                "sector": r.sector,
                "page": r.source_page,
            }
            for r in invalid_records
        ],
        "numbering_anomaly_count": len(result.numbering_anomalies),
        "additional_instrument_count": len(result.additional_instruments),
        "sector_reconciliation": sector_diffs,
        # Ticker identity and PASS/REJECT/UNKNOWN eligibility never depend on
        # sector -- these two counts are a transparency signal about the
        # SECTOR metadata field only. A record's sector is either confirmed by
        # matching the actual header text printed above it in the source PDF
        # (header_text_match) or inferred from the numbering sequence and the
        # fixed Table 3 sector order (order_inference) when no header was
        # captured (e.g. a sector's list continuing across a column or page
        # break with no repeated header). sector_reconciliation above is the
        # ground-truth check for both.
        "sector_header_confirmed_count": header_confirmed_count,
        "sector_order_inferred_count": len(result.main_list) - header_confirmed_count,
        "unresolved_discrepancy": (official_total - unique_total)
        if official_total is not None
        else None,
    }
