"""Tests for the Obsidian vault indexer.

Tests both:
1. Fixture-based: a temporary directory with synthetic notes
2. Real vault: indexes the actual Obsidian vault if available

The indexer is read-only — it never modifies vault files.
"""

import tempfile
from pathlib import Path

from vault_indexer import VaultIndex


def _create_fixture_vault(root: Path) -> None:
    """Create a minimal vault structure for testing."""
    (root / "01-Shariah-Principles").mkdir(parents=True)
    (root / "06-Agent-Design-Specs").mkdir(parents=True)
    (root / "08-Data-Governance" / "shariah-universe").mkdir(parents=True)
    (root / ".obsidian").mkdir(parents=True)

    (root / "01-Shariah-Principles" / "Riba.md").write_text(
        "---\ntitle: Riba\nstatus: active\n---\n\n"
        "# Riba (Interest/Usury)\n\n"
        "Riba is prohibited in Islamic finance. See also [[Gharar]] and [[Maysir]].\n\n"
        "#shariah #principles\n",
        encoding="utf-8",
    )
    (root / "01-Shariah-Principles" / "Gharar.md").write_text(
        "---\ntitle: Gharar\nstatus: active\n---\n\n"
        "# Gharar (Uncertainty)\n\n"
        "Excessive uncertainty in contracts. Related to [[Riba]].\n\n"
        "#shariah #principles\n",
        encoding="utf-8",
    )
    (root / "06-Agent-Design-Specs" / "risk-policy.md").write_text(
        "---\ntitle: Risk Policy\nstatus: draft\nmachine_usable: true\n---\n\n"
        "# Risk Policy\n\n"
        "Maximum single-position exposure: 5%\n",
        encoding="utf-8",
    )
    (root / "08-Data-Governance" / "shariah-universe" / "README.md").write_text(
        "---\ntitle: Shariah Universe Data Layer\nstatus: active-design\n---\n\n"
        "# Shariah Universe Data Layer\n\n"
        "See [[shariah-gating-rules]] and [[audit-log-spec]].\n",
        encoding="utf-8",
    )
    (root / ".obsidian" / "app.json").write_text("{}", encoding="utf-8")


def test_fixture_indexing():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _create_fixture_vault(root)

        idx = VaultIndex()
        result = idx.build(root)

        assert result["status"] == "indexed"
        assert result["notes_indexed"] == 4, f"Expected 4 notes, got {result['notes_indexed']}"
        assert result["errors"] == 0
        print(f"PASS: fixture_indexing — indexed {result['notes_indexed']} notes")


def test_frontmatter_parsing():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _create_fixture_vault(root)

        idx = VaultIndex()
        idx.build(root)

        riba = idx.get_note("01-Shariah-Principles\\Riba.md") or idx.get_note(
            "01-Shariah-Principles/Riba.md"
        )
        assert riba is not None, "Riba note not found"
        assert riba["frontmatter"].get("title") == "Riba"
        assert riba["frontmatter"].get("status") == "active"
        print("PASS: frontmatter_parsing")


def test_wikilinks():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _create_fixture_vault(root)

        idx = VaultIndex()
        idx.build(root)

        riba = idx.get_note("01-Shariah-Principles\\Riba.md") or idx.get_note(
            "01-Shariah-Principles/Riba.md"
        )
        assert riba is not None
        assert "Gharar" in riba["wikilinks"]
        assert "Maysir" in riba["wikilinks"]
        print("PASS: wikilinks — extracted [[Gharar]] and [[Maysir]]")


def test_backlinks():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _create_fixture_vault(root)

        idx = VaultIndex()
        idx.build(root)

        gharar = idx.get_note("01-Shariah-Principles\\Gharar.md") or idx.get_note(
            "01-Shariah-Principles/Gharar.md"
        )
        assert gharar is not None
        assert len(gharar["backlinks"]) >= 1, (
            f"Gharar should have backlinks from Riba: {gharar['backlinks']}"
        )
        print("PASS: backlinks — Gharar has backlinks from Riba")


def test_tags():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _create_fixture_vault(root)

        idx = VaultIndex()
        idx.build(root)

        riba = idx.get_note("01-Shariah-Principles\\Riba.md") or idx.get_note(
            "01-Shariah-Principles/Riba.md"
        )
        assert riba is not None
        assert "shariah" in riba["tags"]
        assert "principles" in riba["tags"]
        print("PASS: tags — #shariah and #principles extracted")


def test_classification():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _create_fixture_vault(root)

        idx = VaultIndex()
        idx.build(root)

        riba = idx.get_note("01-Shariah-Principles\\Riba.md") or idx.get_note(
            "01-Shariah-Principles/Riba.md"
        )
        risk = idx.get_note("06-Agent-Design-Specs\\risk-policy.md") or idx.get_note(
            "06-Agent-Design-Specs/risk-policy.md"
        )
        readme = idx.get_note("08-Data-Governance\\shariah-universe\\README.md") or idx.get_note(
            "08-Data-Governance/shariah-universe/README.md"
        )

        assert riba["classification"] == "SCHOLARLY"
        assert risk["classification"] == "DESIGN"
        assert readme["classification"] == "DATA_GOVERNANCE"
        print("PASS: classification — directory-based classification works")


def test_search():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _create_fixture_vault(root)

        idx = VaultIndex()
        idx.build(root)

        results = idx.search("riba interest")
        assert len(results) >= 1
        assert results[0]["filename"] == "Riba"
        print("PASS: search — found Riba note for 'riba interest'")


def test_hashing():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _create_fixture_vault(root)

        idx = VaultIndex()
        idx.build(root)

        riba = idx.get_note("01-Shariah-Principles\\Riba.md") or idx.get_note(
            "01-Shariah-Principles/Riba.md"
        )
        assert riba is not None
        assert len(riba["note_hash"]) == 64
        print("PASS: hashing — SHA-256 hash computed")


def test_obsidian_dir_excluded():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _create_fixture_vault(root)
        (root / ".obsidian" / "test.md").write_text("# Config", encoding="utf-8")

        idx = VaultIndex()
        idx.build(root)

        assert idx.note_count == 4, f".obsidian notes should be excluded, got {idx.note_count}"
        print("PASS: obsidian_dir_excluded — .obsidian/ not indexed")


def test_nonexistent_vault():
    idx = VaultIndex()
    result = idx.build("/nonexistent/vault/path")
    assert result["status"] == "error"
    assert result["reason"] == "vault_not_found"
    print("PASS: nonexistent_vault — graceful error")


def test_by_classification():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _create_fixture_vault(root)

        idx = VaultIndex()
        idx.build(root)

        groups = idx.by_classification()
        assert "SCHOLARLY" in groups
        assert "DESIGN" in groups
        assert len(groups["SCHOLARLY"]) == 2
        print("PASS: by_classification — grouped correctly")


def test_real_vault():
    """Index the actual Obsidian vault if available."""
    vault_path = Path(r"E:\Projects Stuff\Multi_Ai_IslamicFinance")
    if not vault_path.exists():
        print("SKIP: real_vault — vault not available at expected path")
        return

    idx = VaultIndex()
    result = idx.build(vault_path)

    assert result["status"] == "indexed"
    assert result["notes_indexed"] >= 30, f"Expected ~34 notes, got {result['notes_indexed']}"

    groups = idx.by_classification()
    assert "SCHOLARLY" in groups, "Expected SCHOLARLY notes"
    assert "DESIGN" in groups, "Expected DESIGN notes"
    assert "DATA_GOVERNANCE" in groups, "Expected DATA_GOVERNANCE notes"

    results = idx.search("riba gharar")
    assert len(results) >= 1, "Search for 'riba gharar' should find notes"

    graph = idx.graph()
    assert len(graph["nodes"]) >= 30
    assert len(graph["edges"]) >= 1, "Vault should have wikilink edges"

    print(
        f"PASS: real_vault — indexed {result['notes_indexed']} notes, "
        f"{len(groups)} classifications, {len(graph['edges'])} edges"
    )


def main():
    test_fixture_indexing()
    test_frontmatter_parsing()
    test_wikilinks()
    test_backlinks()
    test_tags()
    test_classification()
    test_search()
    test_hashing()
    test_obsidian_dir_excluded()
    test_nonexistent_vault()
    test_by_classification()
    test_real_vault()
    print("\nAll vault indexer tests passed.")


if __name__ == "__main__":
    main()
