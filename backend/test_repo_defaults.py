"""A fresh clone must run with no .env and no private research vault.

This is the "a judge clones the repo" guarantee: the Shariah universe and the policy
notes both resolve to committed in-repo paths when nothing is configured.
"""

import json
import os
import tempfile
from pathlib import Path

import config


VAULT_KEYS = ["SHARIAH_UNIVERSE_PATH", "SHARIAH_WIKI_PATH"]


def check_defaults_resolve_into_the_repo() -> None:
    settings = config.load_settings()

    universe = Path(settings.shariah_universe_path)
    wiki = Path(settings.shariah_wiki_path)
    assert universe.exists(), f"committed universe dataset is missing: {universe}"
    assert wiki.is_dir(), f"committed policy notes are missing: {wiki}"

    # Both must live inside the repository, not on someone's personal drive.
    root = config.REPO_ROOT.resolve()
    assert root in universe.resolve().parents, universe
    assert root in wiki.resolve().parents, wiki


def check_universe_dataset_is_intact() -> None:
    settings = config.load_settings()
    dataset = json.loads(Path(settings.shariah_universe_path).read_text(encoding="utf-8-sig"))

    assert dataset["dataset_id"] == "sc-sac-my-2026-05-29"
    assert dataset["source"]["authority"].startswith("Securities Commission Malaysia")
    records = dataset["records"]
    assert len(records) == dataset["expected_record_count"], (
        "record count must match the published total"
    )
    assert len(records) == 688, len(records)
    assert all("ticker" in row and "shariah_status" in row for row in records)


def check_gate_and_notes_work_from_the_repo() -> None:
    import shariah_gate
    import wiki_context

    # The committed legacy universe JSON is a migration-compatibility fixture,
    # not an authority: even for a ticker it marks COMPLIANT, the gate must
    # never produce PASS without an approved, activated SC publication. A
    # fresh clone (no SC publication ever approved) must fail closed to
    # UNKNOWN for every ticker -- this is the invariant itself, not a gap.
    # The fresh clone has to be *simulated*, not assumed. shariah_gate reaches
    # the SC store through sc_malaysia_store.connect_default(), which opens
    # sc_malaysia_store.DB_PATH -- the real backend/paper_trading.db. On a true
    # fresh clone that file does not exist (it is gitignored) so every ticker is
    # UNKNOWN, which is the condition asserted below. On a developer machine it
    # is populated, and without this redirect the assertion reports whatever
    # that developer's database happens to hold. It passed for months only
    # because a self-superseded publication made every live ticker resolve
    # UNKNOWN; repairing that publication turned it red, since 7113 (Top Glove)
    # is genuinely COMPLIANT in the activated SC list.
    import sc_malaysia_store

    original_db_path = sc_malaysia_store.DB_PATH
    with tempfile.TemporaryDirectory() as fresh_clone_dir:
        sc_malaysia_store.DB_PATH = Path(fresh_clone_dir) / "paper_trading.db"
        try:
            no_authority_yet = shariah_gate.check_symbol("7113")
            assert no_authority_yet["status"] != "PASS", (
                f"the legacy JSON fixture must never produce PASS on its own: {no_authority_yet}"
            )

            unknown = shariah_gate.check_symbol("NOTAREALTICKER")
            assert unknown["status"] in ("REJECT", "UNKNOWN"), unknown
            assert unknown["status"] != "PASS", "absent ticker must never be PASS"
        finally:
            sc_malaysia_store.DB_PATH = original_db_path

    hits = wiki_context.find_policy_context("riba interest prohibition screening")
    assert hits, "the committed policy notes must be searchable for explanations"
    assert all(str(config.REPO_ROOT) in hit["file"] for hit in hits)


def check_no_redistributable_material_leaked() -> None:
    """The extraction must not have dragged in book PDFs or pasted chapter text."""
    wiki = Path(config.load_settings().shariah_wiki_path)

    assert not list(wiki.rglob("*.pdf")), "no PDFs may be published from the research vault"
    for note in wiki.rglob("*.md"):
        text = note.read_text(encoding="utf-8", errors="ignore")
        longest = max((len(block.strip()) for block in text.split("\n\n")), default=0)
        assert longest < 5000, (
            f"{note.name} contains a {longest}-char block; likely pasted source text"
        )


def main() -> None:
    saved = {key: os.environ.get(key) for key in VAULT_KEYS}
    for key in VAULT_KEYS:
        os.environ.pop(key, None)
    try:
        check_defaults_resolve_into_the_repo()
        check_universe_dataset_is_intact()
        check_gate_and_notes_work_from_the_repo()
        check_no_redistributable_material_leaked()
    finally:
        for key, value in saved.items():
            if value is not None:
                os.environ[key] = value

    print("PASS: a clean clone resolves the Shariah universe and policy notes from the repo.")


if __name__ == "__main__":
    main()
