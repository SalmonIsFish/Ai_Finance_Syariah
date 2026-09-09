"""Read-only Obsidian vault indexer.

Builds an in-memory index of all Markdown notes in the vault with:
  - original path and relative path
  - YAML frontmatter (parsed)
  - tags (#tag)
  - wikilinks ([[link]])
  - backlinks (computed from other notes' wikilinks)
  - file hash (SHA-256)
  - modification timestamp
  - classification (derived from directory)
  - excerpt (first 600 chars of body text)

The vault is NEVER modified by this module. It reads, indexes, and serves
metadata. Copyrighted content is never served in full — only excerpts.

Designed behind a clean interface so a graph database (Graphiti, Neo4j, etc.)
could replace the in-memory storage later without changing the API contract.
"""

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

# Optional YAML parsing — gracefully degrade if not available
try:
    import yaml

    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
TAG_RE = re.compile(r"(?:^|\s)#([A-Za-z][A-Za-z0-9_/-]*)", re.MULTILINE)
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?\n)---\s*\n", re.DOTALL)

DIRECTORY_CLASSIFICATIONS = {
    "00-Raw-Books-PDF": "RAW_SOURCE",
    "00-Raw-Imports-MD": "RAW_IMPORT",
    "01-Shariah-Principles": "SCHOLARLY",
    "02-Shariah-Compliant-Screening-Malaysia": "METHODOLOGY",
    "06-Agent-Design-Specs": "DESIGN",
    "08-Data-Governance": "DATA_GOVERNANCE",
    "09-Strategies": "STRATEGY",
    "10-International-Paper": "DESIGN",
    "tools": "TOOLING",
}

MAX_EXCERPT_LENGTH = 600


def _compute_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    raw_fm = match.group(1)
    body = text[match.end() :]
    if _HAS_YAML:
        try:
            parsed = yaml.safe_load(raw_fm)
            if isinstance(parsed, dict):
                return parsed, body
        except yaml.YAMLError:
            pass
    fm = {}
    for line in raw_fm.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            fm[key.strip()] = value.strip()
    return fm, body


def _extract_wikilinks(text: str) -> list[str]:
    return WIKILINK_RE.findall(text)


def _extract_tags(text: str) -> list[str]:
    return TAG_RE.findall(text)


def _classify_note(relative_path: str) -> str:
    parts = Path(relative_path).parts
    if parts:
        top_dir = parts[0]
        if top_dir in DIRECTORY_CLASSIFICATIONS:
            return DIRECTORY_CLASSIFICATIONS[top_dir]
    return "UNCATEGORIZED"


def _excerpt(body: str) -> str:
    cleaned = " ".join(body.strip().split())
    return cleaned[:MAX_EXCERPT_LENGTH]


class NoteEntry:
    """Metadata for a single vault note."""

    __slots__ = (
        "original_path",
        "relative_path",
        "filename",
        "note_hash",
        "last_modified",
        "frontmatter",
        "classification",
        "wikilinks",
        "tags",
        "excerpt",
        "ingestion_timestamp",
    )

    def __init__(
        self,
        *,
        original_path: str,
        relative_path: str,
        filename: str,
        note_hash: str,
        last_modified: str,
        frontmatter: dict,
        classification: str,
        wikilinks: list[str],
        tags: list[str],
        excerpt: str,
        ingestion_timestamp: str,
    ):
        self.original_path = original_path
        self.relative_path = relative_path
        self.filename = filename
        self.note_hash = note_hash
        self.last_modified = last_modified
        self.frontmatter = frontmatter
        self.classification = classification
        self.wikilinks = wikilinks
        self.tags = tags
        self.excerpt = excerpt
        self.ingestion_timestamp = ingestion_timestamp

    def to_dict(self) -> dict:
        return {
            "original_path": self.original_path,
            "relative_path": self.relative_path,
            "filename": self.filename,
            "note_hash": self.note_hash,
            "last_modified": self.last_modified,
            "frontmatter": self.frontmatter,
            "classification": self.classification,
            "wikilinks": self.wikilinks,
            "tags": self.tags,
            "excerpt": self.excerpt,
            "ingestion_timestamp": self.ingestion_timestamp,
        }


class VaultIndex:
    """In-memory index of all Markdown notes in an Obsidian vault.

    The vault is read-only from this module's perspective. The index is
    rebuilt from scratch on each call to ``build()``.
    """

    def __init__(self):
        self._notes: dict[str, NoteEntry] = {}
        self._backlinks: dict[str, list[str]] = {}
        self._vault_root: str | None = None

    @property
    def note_count(self) -> int:
        return len(self._notes)

    @property
    def vault_root(self) -> str | None:
        return self._vault_root

    def build(self, vault_path: str | Path) -> dict:
        """Scan the vault and build the index. Returns a summary."""
        root = Path(vault_path)
        if not root.exists():
            return {"status": "error", "reason": "vault_not_found", "path": str(root)}

        self._notes.clear()
        self._backlinks.clear()
        self._vault_root = str(root)

        now = datetime.now(timezone.utc).isoformat()
        indexed = 0
        errors = 0

        for md_path in root.rglob("*.md"):
            if ".obsidian" in md_path.parts:
                continue
            try:
                raw_bytes = md_path.read_bytes()
                text = raw_bytes.decode("utf-8", errors="ignore")
                rel = str(md_path.relative_to(root))

                frontmatter, body = _parse_frontmatter(text)
                wikilinks = _extract_wikilinks(text)
                tags = _extract_tags(body)

                mtime = datetime.fromtimestamp(md_path.stat().st_mtime, tz=timezone.utc).isoformat()

                entry = NoteEntry(
                    original_path=str(md_path),
                    relative_path=rel,
                    filename=md_path.stem,
                    note_hash=_compute_hash(raw_bytes),
                    last_modified=mtime,
                    frontmatter=frontmatter,
                    classification=_classify_note(rel),
                    wikilinks=wikilinks,
                    tags=tags,
                    excerpt=_excerpt(body),
                    ingestion_timestamp=now,
                )
                self._notes[rel] = entry
                indexed += 1
            except OSError:
                errors += 1

        self._compute_backlinks()

        return {
            "status": "indexed",
            "vault_root": str(root),
            "notes_indexed": indexed,
            "errors": errors,
        }

    def _compute_backlinks(self) -> None:
        self._backlinks.clear()
        name_to_rel: dict[str, str] = {}
        for rel, entry in self._notes.items():
            name_to_rel[entry.filename.lower()] = rel
            stem_from_path = Path(rel).stem.lower()
            if stem_from_path not in name_to_rel:
                name_to_rel[stem_from_path] = rel

        for rel, entry in self._notes.items():
            for link in entry.wikilinks:
                link_lower = link.lower().strip()
                link_stem = Path(link_lower).stem
                target = name_to_rel.get(link_lower) or name_to_rel.get(link_stem)
                if target and target != rel:
                    self._backlinks.setdefault(target, [])
                    if rel not in self._backlinks[target]:
                        self._backlinks[target].append(rel)

    def get_note(self, relative_path: str) -> dict | None:
        entry = self._notes.get(relative_path)
        if not entry:
            return None
        result = entry.to_dict()
        result["backlinks"] = self._backlinks.get(relative_path, [])
        return result

    def search(self, query: str, *, limit: int = 10) -> list[dict]:
        terms = {t.lower() for t in query.split() if len(t) > 2}
        if not terms:
            return []

        scored: list[tuple[int, str]] = []
        for rel, entry in self._notes.items():
            searchable = (
                f"{entry.filename} {entry.excerpt} "
                f"{' '.join(entry.tags)} "
                f"{entry.frontmatter.get('title', '')}"
            ).lower()
            score = sum(searchable.count(t) for t in terms)
            if score > 0:
                scored.append((score, rel))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = []
        for _, rel in scored[:limit]:
            note = self.get_note(rel)
            if note:
                results.append(note)
        return results

    def by_classification(self) -> dict[str, list[dict]]:
        groups: dict[str, list[dict]] = {}
        for rel, entry in self._notes.items():
            cls = entry.classification
            groups.setdefault(cls, [])
            groups[cls].append(entry.to_dict())
        return groups

    def all_notes(self) -> list[dict]:
        return [e.to_dict() for e in self._notes.values()]

    def graph(self) -> dict:
        """Return nodes and edges for visualization."""
        nodes = []
        edges = []
        for rel, entry in self._notes.items():
            nodes.append(
                {
                    "id": rel,
                    "label": entry.filename,
                    "classification": entry.classification,
                }
            )
            for link in entry.wikilinks:
                link_lower = link.lower().strip()
                for target_rel, target_entry in self._notes.items():
                    if (
                        target_entry.filename.lower() == link_lower
                        or Path(target_rel).stem.lower() == Path(link_lower).stem
                    ):
                        edges.append({"source": rel, "target": target_rel})
                        break
        return {"nodes": nodes, "edges": edges}
