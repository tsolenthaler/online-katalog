#!/usr/bin/env python3
"""Build individual item JSON files for Bibliothek Stein AR.

For each entry in the source (CSV or PDF):
  1. Derive a stable UUID via uuid5 (deterministic from title + author)
  2. Persist the key→UUID mapping in data/uuid_map.json
  3. Look up / fetch ISBN and metadata
  4. Write data/item/<uuid>.json following the schema.org Book vocabulary

The UUID mapping guarantees that the same book always gets the same UUID,
no matter how many times this script is run or which source file is used.

Usage
-----
  # From CSV (recommended):
  python scripts/build_items.py --csv archiv/books.csv

  # From PDF:
  python scripts/build_items.py --pdf data/Titelliste.pdf

  # Offline (skip external API calls):
  python scripts/build_items.py --csv archiv/books.csv --offline
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import uuid
from pathlib import Path
from typing import Any

import requests

from catalog_utils import (
    DEFAULT_PLACEHOLDER_COVER,
    Entry,
    clean_text,
    dnb_metadata_by_isbn,
    dnb_search_isbn,
    extract_isbn,
    google_isbn_search,
    google_metadata_by_isbn,
    load_isbn_cache,
    load_json,
    load_manual_overrides,
    merge_metadata,
    normalize_isbn,
    normalize_text,
    openlibrary_metadata_by_isbn,
    parse_csv_rows,
    parse_iso_date,
    parse_pdf_rows,
    save_isbn_cache,
    save_json,
)

# Namespace UUID for Bibliothek Stein AR – fixed so that uuid5 always
# produces the same result for a given stable key.
_CATALOG_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # uuid.NAMESPACE_URL


def stable_uuid(title: str, author: str) -> str:
    """Return a deterministic UUID5 string for the given title."""
    # UUID is intentionally title-based so entries from Titelliste.pdf keep
    # the same UUID across repeated runs.
    key = f"bibliothek-stein-ar:title:{normalize_text(title)}"
    return str(uuid.uuid5(_CATALOG_NS, key))


def load_uuid_map(path: Path) -> dict[str, str]:
    """Load existing key→uuid mappings from disk."""
    return load_json(path, default={})


def save_uuid_map(path: Path, mapping: dict[str, str]) -> None:
    save_json(path, mapping)


def _item_stable_key(title: str, author: str) -> str:
    return normalize_text(title)


def get_or_create_uuid(mapping: dict[str, str], title: str, author: str) -> str:
    """Look up the UUID for a book or create and persist a new one."""
    key = _item_stable_key(title, author)
    legacy_key = f"{normalize_text(title)}|{normalize_text(author)}"

    # Backward compatibility: migrate old title|author keys to title-only keys.
    if key not in mapping and legacy_key in mapping:
        mapping[key] = mapping[legacy_key]

    if key not in mapping:
        mapping[key] = stable_uuid(title, author)
    return mapping[key]


def entry_search_text(
    title: str,
    author: str,
    isbn: str,
    media_type: str,
    owner: str,
    genre: str,
    keywords: list[str],
) -> str:
    parts = [title, author, isbn, media_type, owner, genre, " ".join(keywords)]
    return normalize_text(" ".join(p for p in parts if p))


def build_schemaorg_item(
    item_uuid: str,
    title: str,
    author: str,
    isbn: str,
    media_type: str,
    owner: str,
    date_added: str,
    is_new: bool,
    cover_url: str,
    description: str,
    keywords: list[str],
    genre: str,
    metadata_source: str,
    isbn_source: str,
    status: str,
    openlibrary_link: str,
    google_books_link: str,
    dnb_link: str,
) -> dict[str, Any]:
    """Return an item dict structured according to schema.org/Book."""

    same_as: list[str] = []
    if openlibrary_link:
        same_as.append(openlibrary_link)
    if dnb_link:
        same_as.append(dnb_link)
    if google_books_link:
        same_as.append(google_books_link)

    search_text = entry_search_text(title, author, isbn, media_type, owner, genre, keywords)

    item: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "Book",
        "@id": f"urn:uuid:{item_uuid}",
        "name": title,
        "author": {"@type": "Person", "name": author} if author else {"@type": "Person", "name": ""},
        "isbn": isbn,
        "genre": genre,
        "keywords": keywords,
        "description": description,
        "image": cover_url or DEFAULT_PLACEHOLDER_COVER,
    }
    if same_as:
        item["sameAs"] = same_as

    # Non-standard catalog fields stored under _catalog (not part of schema.org)
    item["_catalog"] = {
        "id": item_uuid,
        "type": media_type,
        "owner": owner,
        "date_added": date_added,
        "is_new": is_new,
        "status": status,
        "metadata_source": metadata_source,
        "isbn_source": isbn_source,
        "search_text": search_text,
    }

    return item


def load_source_rows(args: argparse.Namespace) -> list[Entry]:
    if args.csv:
        csv_path = Path(args.csv)
        if csv_path.exists():
            return parse_csv_rows(csv_path)
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    pdf_path = Path(args.pdf)
    for candidate in [pdf_path.with_suffix(".csv"), Path("archiv/books.csv")]:
        if candidate.exists():
            return parse_csv_rows(candidate)

    return parse_pdf_rows(pdf_path)


def build_items(args: argparse.Namespace) -> dict[str, Any]:
    source_entries = load_source_rows(args)
    if args.max_rows:
        source_entries = source_entries[: args.max_rows]

    manual_path = Path(args.manual)
    if not manual_path.exists():
        fallback = Path("archiv/manual_catalog_overrides.csv")
        if fallback.exists():
            manual_path = fallback
    overrides = load_manual_overrides(manual_path)

    item_dir = Path(args.item_dir)
    item_dir.mkdir(parents=True, exist_ok=True)

    uuid_map_path = Path(args.uuid_map)
    uuid_map = load_uuid_map(uuid_map_path)

    metadata_cache = load_json(Path(args.cache), default={})
    isbn_cache = load_isbn_cache(Path(args.isbn_cache))

    session = requests.Session()
    session.headers.update({"User-Agent": args.user_agent})

    now = dt.datetime.now(dt.UTC)
    cutoff = now.date() - dt.timedelta(days=args.days_new)

    managed_uuids: set[str] = set()
    stats = {
        "processed": 0,
        "with_isbn": 0,
        "without_isbn": 0,
        "with_metadata": 0,
    }

    for source in source_entries:
        override = overrides.get(
            (normalize_text(source.title), normalize_text(source.author)), {}
        )
        src_title = override.get("title") or source.title
        src_author = override.get("author") or source.author

        # --- UUID: deterministic, stable for title+author ---
        item_uuid = get_or_create_uuid(uuid_map, source.title, source.author)
        managed_uuids.add(item_uuid)

        # --- ISBN resolution ---
        isbn = ""
        isbn_source = ""
        lookup_key = f"{normalize_text(source.title)}|{normalize_text(source.author)}"

        if override.get("isbn"):
            isbn = override["isbn"]
            isbn_source = "manual_override"
        elif isbn_cache.get(lookup_key, {}).get("isbn"):
            isbn = isbn_cache[lookup_key]["isbn"]
            isbn_source = isbn_cache[lookup_key].get("source", "cache")
        else:
            isbn = extract_isbn(f"{source.title} {source.author}")
            if isbn:
                isbn_source = "pdf"

        if not isbn and not args.offline:
            try:
                isbn = dnb_search_isbn(session, src_title, src_author)
                if isbn:
                    isbn_source = "dnb"
            except requests.RequestException:
                isbn = ""

        if not isbn and not args.offline:
            try:
                isbn = google_isbn_search(session, src_title, src_author)
                if isbn:
                    isbn_source = "google_books"
            except requests.RequestException:
                isbn = ""

        if isbn:
            isbn_cache[lookup_key] = {"isbn": isbn, "source": isbn_source or "lookup"}
            stats["with_isbn"] += 1
        else:
            stats["without_isbn"] += 1

        # --- Metadata resolution ---
        metadata: dict[str, Any] = {
            "title": "",
            "author": "",
            "description": "",
            "genres": [],
            "cover_url": "",
            "openlibrary_link": "",
            "google_books_link": "",
            "dnb_link": "",
        }
        metadata_source = ""

        if isbn:
            if isbn in metadata_cache:
                metadata = metadata_cache[isbn]
                metadata_source = metadata.get("metadata_source", "cache")
            elif not args.offline:
                ol: dict[str, Any] = {}
                gb: dict[str, Any] = {}
                dnb: dict[str, Any] = {}

                try:
                    ol = openlibrary_metadata_by_isbn(session, isbn)
                except requests.RequestException:
                    ol = {}
                try:
                    gb = google_metadata_by_isbn(session, isbn)
                except requests.RequestException:
                    gb = {}
                try:
                    dnb = dnb_metadata_by_isbn(session, isbn)
                except requests.RequestException:
                    dnb = {}

                merged = merge_metadata(ol, gb)
                merged = merge_metadata(merged, dnb)
                metadata = {
                    "title": merged.get("title", ""),
                    "author": merged.get("author", ""),
                    "description": merged.get("description", ""),
                    "genres": merged.get("genres", []),
                    "cover_url": merged.get("cover_url", ""),
                    "openlibrary_link": ol.get(
                        "source_link", f"https://openlibrary.org/isbn/{isbn}"
                    ),
                    "google_books_link": gb.get("source_link", ""),
                    "dnb_link": dnb.get(
                        "source_link",
                        f"https://portal.dnb.de/opac/simpleSearch?query={isbn}",
                    ),
                }
                if ol:
                    metadata_source = "openlibrary"
                elif gb:
                    metadata_source = "google_books"
                elif dnb:
                    metadata_source = "dnb"

                metadata["metadata_source"] = metadata_source
                metadata_cache[isbn] = metadata

        # --- Resolve final field values ---
        title = override.get("title") or metadata.get("title") or source.title
        author = override.get("author") or metadata.get("author") or source.author
        keywords = [clean_text(g) for g in (metadata.get("genres") or []) if clean_text(g)]
        genre = override.get("genre") or (keywords[0] if keywords else "")
        description = override.get("description") or metadata.get("description") or ""
        cover_url = (
            override.get("cover_url")
            or metadata.get("cover_url")
            or DEFAULT_PLACEHOLDER_COVER
        )
        media_type = override.get("type") or source.media_type
        owner = override.get("owner") or source.owner
        date_added = override.get("date_added") or source.date_added
        parsed_date = parse_iso_date(date_added)
        is_new = bool(parsed_date and parsed_date >= cutoff)
        status = override.get("status") or ("OK" if isbn else "Keine ISBN ermittelt")
        openlibrary_link = metadata.get("openlibrary_link") or (
            f"https://openlibrary.org/isbn/{isbn}" if isbn else ""
        )
        google_books_link = metadata.get("google_books_link", "")
        dnb_link = metadata.get("dnb_link") or (
            f"https://portal.dnb.de/opac/simpleSearch?query={isbn}" if isbn else ""
        )

        if isbn and (description or keywords or metadata.get("cover_url")):
            stats["with_metadata"] += 1

        # --- Build schema.org item ---
        new_item = build_schemaorg_item(
            item_uuid=item_uuid,
            title=title,
            author=author,
            isbn=isbn,
            media_type=media_type,
            owner=owner,
            date_added=date_added,
            is_new=is_new,
            cover_url=cover_url,
            description=description,
            keywords=keywords,
            genre=genre,
            metadata_source=metadata_source,
            isbn_source=isbn_source,
            status=status,
            openlibrary_link=openlibrary_link,
            google_books_link=google_books_link,
            dnb_link=dnb_link,
        )

        item_path = item_dir / f"{item_uuid}.json"

        if item_path.exists():
            try:
                existing = json.loads(item_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing = {}

            # Merge: prefer existing catalog-private fields (manual edits),
            # but always keep schema.org structure from the new build.
            if "_catalog" in existing:
                existing_cat = existing["_catalog"]
                new_cat = new_item["_catalog"]
                # Keep manually-set fields that are richer than what we computed
                for field in ("owner", "date_added", "status"):
                    if existing_cat.get(field) and not new_cat.get(field):
                        new_cat[field] = existing_cat[field]

        save_json(item_path, new_item)
        stats["processed"] += 1

    # Prune orphaned item files (items no longer in source).
    # To avoid accidental mass deletion during test runs, pruning is skipped
    # when --max-rows is used.
    if not args.keep_orphans and not args.max_rows:
        for path in item_dir.glob("*.json"):
            if path.stem not in managed_uuids:
                path.unlink()

    save_uuid_map(uuid_map_path, uuid_map)
    save_json(Path(args.cache), metadata_cache)
    save_isbn_cache(Path(args.isbn_cache), isbn_cache)

    return stats


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build item JSON files for the catalog")
    parser.add_argument("--pdf", default="data/Titelliste.pdf", help="Input PDF")
    parser.add_argument(
        "--csv", default="", help="Input CSV (title,author,...); overrides --pdf"
    )
    parser.add_argument("--manual", default="data/manual_overrides.csv")
    parser.add_argument("--item-dir", default="data/item", help="Output folder for item JSONs")
    parser.add_argument(
        "--uuid-map",
        default="data/uuid_map.json",
        help="Path to persist key→UUID mapping",
    )
    parser.add_argument("--cache", default="data/catalog_metadata_cache.json")
    parser.add_argument("--isbn-cache", default="data/isbn_cache.csv")
    parser.add_argument(
        "--keep-orphans",
        action="store_true",
        help="Keep item files not present in source",
    )
    parser.add_argument("--days-new", type=int, default=90)
    parser.add_argument("--max-rows", type=int, default=0)
    parser.add_argument("--offline", action="store_true", help="Skip external API calls")
    parser.add_argument(
        "--user-agent",
        default="Bibliothek-Stein-AR-Katalog/1.0 (contact: github-pages)",
    )
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    stats = build_items(args)
    print(
        f"Items geschrieben: {stats['processed']} | "
        f"mit ISBN: {stats['with_isbn']} | "
        f"ohne ISBN: {stats['without_isbn']} | "
        f"mit Metadaten: {stats['with_metadata']}"
    )


if __name__ == "__main__":
    main()
