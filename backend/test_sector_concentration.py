"""Verify the sector-concentration limit: grouping, the cap, and how it fails.

This is a gate, so the questions that matter are the awkward ones. What happens
to a symbol nobody has screened yet? What stops a portfolio hiding concentration
behind missing data? And does it stay away from options, where the equity
exposure overlay has already caused one real bug (CLAUDE.md, known limitation 4)?
"""

import sqlite3

from sector_concentration import (
    UNKNOWN_SECTOR,
    sector_exposure,
    sector_for_sic,
    check_sector_concentration,
)


def check_sic_maps_to_a_sector() -> None:
    # Ranges taken from SEC's own SIC division blocks.
    assert sector_for_sic(2911) == "Energy", sector_for_sic(2911)  # Chevron
    assert sector_for_sic(6021) == "Financials", sector_for_sic(6021)  # national banks
    assert sector_for_sic(3571) == "Technology", sector_for_sic(3571)  # computers
    assert sector_for_sic(2834) == "Health Care", sector_for_sic(2834)  # pharma
    assert sector_for_sic(5812) == "Consumer", sector_for_sic(5812)  # eating places
    # Anything unmapped, absent or malformed lands in one explicit bucket rather
    # than silently vanishing from the concentration maths.
    for value in [None, "", "abc", 9999, -1]:
        assert sector_for_sic(value) == UNKNOWN_SECTOR, value


def check_exposure_groups_by_sector() -> None:
    positions = [
        {"symbol": "CVX", "exposure_value": 1000.0},
        {"symbol": "XOM", "exposure_value": 500.0},
        {"symbol": "AAPL", "exposure_value": 800.0},
    ]
    sectors = {"CVX": "Energy", "XOM": "Energy", "AAPL": "Technology"}
    grouped = sector_exposure(positions, sectors)
    assert grouped == {"Energy": 1500.0, "Technology": 800.0}, grouped


def check_within_the_cap_passes() -> None:
    result = check_sector_concentration(
        symbol="XOM",
        added_exposure=500.0,
        positions=[{"symbol": "CVX", "exposure_value": 1000.0}],
        sectors={"CVX": "Energy", "XOM": "Energy"},
        account_equity=100000.0,
        max_sector_pct=10.0,
    )
    assert result["status"] == "PASS", result
    assert result["sector"] == "Energy", result
    assert abs(result["projected_pct"] - 1.5) < 1e-9, result


def check_breaching_the_cap_blocks_and_explains() -> None:
    result = check_sector_concentration(
        symbol="XOM",
        added_exposure=6000.0,
        positions=[{"symbol": "CVX", "exposure_value": 6000.0}],
        sectors={"CVX": "Energy", "XOM": "Energy"},
        account_equity=100000.0,
        max_sector_pct=10.0,
    )
    assert result["status"] == "REJECT", result
    assert result["reason"] == "sector_concentration_limit", result
    assert abs(result["projected_pct"] - 12.0) < 1e-9, result
    message = result["message"]
    assert "Energy" in message and "12.00%" in message and "10.00%" in message, message


def check_a_different_sector_is_unaffected() -> None:
    """Concentration is per sector -- a second sector does not inherit the first's."""
    result = check_sector_concentration(
        symbol="AAPL",
        added_exposure=6000.0,
        positions=[{"symbol": "CVX", "exposure_value": 9000.0}],
        sectors={"CVX": "Energy", "AAPL": "Technology"},
        account_equity=100000.0,
        max_sector_pct=10.0,
    )
    assert result["status"] == "PASS", result
    assert result["sector"] == "Technology", result
    assert abs(result["projected_pct"] - 6.0) < 1e-9, result


def check_unknown_sector_still_counts_against_a_cap() -> None:
    """Missing SEC metadata must not become a way to stack unlimited exposure.

    Failing closed on an unknown sector would block every order until every held
    symbol had been re-screened, which is a hard breakage on deploy. Bucketing
    unknowns together instead means they concentrate against the same cap, so
    nothing hides behind absent data.
    """
    result = check_sector_concentration(
        symbol="NEWCO",
        added_exposure=6000.0,
        positions=[{"symbol": "MYSTERY", "exposure_value": 6000.0}],
        sectors={},  # neither symbol has ever been screened
        account_equity=100000.0,
        max_sector_pct=10.0,
    )
    assert result["status"] == "REJECT", result
    assert result["sector"] == UNKNOWN_SECTOR, result
    assert abs(result["projected_pct"] - 12.0) < 1e-9, result

    # ...but a single unknown position inside the cap is allowed through, so a
    # fresh install is not bricked.
    ok = check_sector_concentration(
        symbol="NEWCO",
        added_exposure=1000.0,
        positions=[],
        sectors={},
        account_equity=100000.0,
        max_sector_pct=10.0,
    )
    assert ok["status"] == "PASS", ok
    assert ok["sector"] == UNKNOWN_SECTOR, ok


def check_a_disabled_cap_passes_everything() -> None:
    for cap in [0, None]:
        result = check_sector_concentration(
            symbol="XOM",
            added_exposure=90000.0,
            positions=[{"symbol": "CVX", "exposure_value": 90000.0}],
            sectors={"CVX": "Energy", "XOM": "Energy"},
            account_equity=100000.0,
            max_sector_pct=cap,
        )
        assert result["status"] == "PASS", (cap, result)
        assert result["reason"] == "sector_limit_disabled", result


def check_zero_equity_does_not_divide_by_zero() -> None:
    result = check_sector_concentration(
        symbol="XOM",
        added_exposure=100.0,
        positions=[],
        sectors={},
        account_equity=0.0,
        max_sector_pct=10.0,
    )
    assert result["status"] == "PASS", result
    assert result["reason"] == "account_equity_unavailable", result


def check_sectors_are_read_from_recorded_screens() -> None:
    """The mapping comes from shariah_screens, not a new data source."""
    import json

    from sector_concentration import sectors_for_symbols
    from shariah_screen_store import ensure_shariah_screen_tables, record_shariah_screen

    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        ensure_shariah_screen_tables(connection)
        record_shariah_screen(
            connection,
            {"symbol": "CVX", "status": "COMPLIANT", "sic": 2911, "sic_description": "Petroleum"},
        )
        record_shariah_screen(
            connection,
            {"symbol": "JPM", "status": "NON_COMPLIANT", "sic": 6021, "sic_description": "Banks"},
        )
        sectors = sectors_for_symbols(connection, ["CVX", "JPM", "NEVERSEEN"])
        assert sectors["CVX"] == "Energy", sectors
        assert sectors["JPM"] == "Financials", sectors
        # A symbol with no screen simply is not in the map; the caller buckets it.
        assert "NEVERSEEN" not in sectors, sectors

        # An older row written before SIC was persisted must not explode.
        connection.execute(
            "INSERT INTO shariah_screens (screened_at, symbol, status, payload) VALUES (?,?,?,?)",
            ("2026-01-01T00:00:00+00:00", "OLD", "COMPLIANT", json.dumps({"symbol": "OLD"})),
        )
        connection.commit()
        legacy = sectors_for_symbols(connection, ["OLD"])
        assert "OLD" not in legacy, legacy
    finally:
        connection.close()


def check_lookup_never_raises() -> None:
    closed = sqlite3.connect(":memory:")
    closed.close()
    from sector_concentration import sectors_for_symbols

    assert sectors_for_symbols(closed, ["CVX"]) == {}


def main() -> None:
    check_sic_maps_to_a_sector()
    check_exposure_groups_by_sector()
    check_within_the_cap_passes()
    check_breaching_the_cap_blocks_and_explains()
    check_a_different_sector_is_unaffected()
    check_unknown_sector_still_counts_against_a_cap()
    check_a_disabled_cap_passes_everything()
    check_zero_equity_does_not_divide_by_zero()
    check_sectors_are_read_from_recorded_screens()
    check_lookup_never_raises()
    print("PASS: sector concentration is capped, and missing data cannot hide it.")


if __name__ == "__main__":
    main()
