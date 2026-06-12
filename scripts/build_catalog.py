#!/usr/bin/env python3
"""Aggregate item JSON files into a single catalog.json for the website.

Reads all data/item/<uuid>.json files and writes data/catalog.json.
Understands both:
  - New schema.org Book format (written by build_items.py)
  - Legacy flat format (older item files)

Usage
-----
  python scripts/build_catalog.py
  python scripts/build_catalog.py --item-dir data/item --out data/catalog.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any

from catalog_utils import (
    DEFAULT_PLACEHOLDER_COVER,
    clean_text,
    normalize_isbn,
    normalize_text,
    parse_iso_date,
    save_json,
)


def _author_name(raw: Any) -> str:
    """Extract a plain author name from either a string or a schema.org Person dict."""
    if isinstance(raw, dict):
        return clean_text(raw.get("name", ""))
    return clean_text(str(raw or ""))


def _same_as_link(same_as: Any, fragment: str) -> str:
    if not isinstance(same_as, list):
        return ""
    return next((u for u in same_as if isinstance(u, str) and fragment in u), "")


def item_to_catalog_record(raw: dict[str, Any], cutoff: dt.date) -> dict[str, Any] | None:
    """Convert a raw item JSON (schema.org or legacy flat) to a catalog record."""
    if "@type" in raw:
        cat = raw.get("_catalog", {}) or {}
        item_id = clean_text(str(cat.get("id", "") or ""))
        title = clean_text(str(raw.get("name", "") or ""))
        author = _author_name(raw.get("author"))
        isbn = normalize_isbn(str(raw.get("isbn", "") or ""))
        cover_url = clean_text(str(raw.get("image", "") or "")) or DEFAULT_PLACEHOLDER_COVER
        description = clean_text(str(raw.get("description", "") or ""))
        genre = clean_text(str(raw.get("genre", "") or ""))

        raw_kw = raw.get("keywords", [])
        if isinstance(raw_kw, list):
            keywords = [clean_text(str(k)) for k in raw_kw if clean_text(str(k))]
        elif isinstance(raw_kw, str):
            keywords = [clean_text(p) for p in raw_kw.split(",") if clean_text(p)]
        else:
            keywords = []
        if not genre and keywords:
            genre = keywords[0]

        same_as = raw.get("sameAs", [])
        ol_link = _same_as_link(same_as, "openlibrary")
        dnb_link = _same_as_link(same_as, "dnb.de")
        gb_link = _same_as_link(same_as, "google")

        media_type = clean_text(str(cat.get("type", "") or "")) or "Buch"
        owner = clean_text(str(cat.get("owner", "") or ""))
        date_added = clean_text(str(cat.get("date_added", "") or ""))
        status = clean_text(str(cat.get("status", "") or ""))
        metadata_source = clean_text(str(cat.get("metadata_source", "") or ""))
        isbn_source = clean_text(str(cat.get("isbn_source", "") or ""))
        search_text = clean_text(str(cat.get("search_text", "") or ""))
    else:
        item_id = clean_text(str(raw.get("id", "") or ""))
        title = clean_text(str(raw.get("title", "") or ""))
        author = clean_text(str(raw.get("author", "") or ""))
        isbn = normalize_isbn(str(raw.get("isbn", "") or ""))
        cover_url = clean_text(str(raw.get("cover_url", "") or "")) or DEFAULT_PLACEHOLDER_COVER
        description = clean_text(str(raw.get("description", "") or ""))
        genre = clean_text(str(raw.get("genre", "") or ""))

        raw_genres = raw.get("genres", [])
        if isinstance(raw_genres, list):
            keywords = [clean_text(str(g)) for g in raw_genres if clean_text(str(g))]
        elif isinstance(raw_genres, str):
            keywords = [clean_text(p) for p in raw_genres.split(",") if clean_text(p)]
        else:
            keywords = []
        if not genre and keywords:
            genre = keywords[0]

        same_as = raw.get("sameAs", [])
        ol_link = _same_as_link(same_as, "openlibrary") or clean_text(str(raw.get("openlibrary_link", "") or ""))
        dnb_link = _same_as_link(same_as, "dnb.de") or clean_text(str(raw.get("dnb_link", "") or ""))
        gb_link = _same_as_link(same_as, "google") or clean_text(str(raw.get("google_books_link", "") or ""))

        media_type = clean_text(str(raw.get("type", "") or "")) or "Buch"
        owner = clean_text(str(raw.get("owner", "") or ""))
        date_added = clean_text(str(raw.get("date_added", "") or ""))
        status = clean_text(str(raw.get("status", "") or ""))
        metadata_source = clean_text(str(raw.get("metadata_source", "") or ""))
        isbn_source = clean_text(str(raw.get("isbn_source", "") or ""))
        search_text = clean_text(str(raw.get("search_text", "") or ""))

    if not item_id or not title:
        return None

    parsed_date = parse_iso_date(date_added)
    is_new = bool(parsed_date and parsed_date >= cutoff)

    if not status:
        status = "OK" if isbn else "Keine ISBN ermittelt"
    if isbn and not ol_link:
        ol_link = f"https://openlibrary.org/isbn/{isbn}"
    if isbn and not dnb_link:
        dnb_link = f"https://portal.dnb.de/opac/simpleSearch?query={isbn}"

    if not search_text:
        parts = [title, author, isbn, media_type, owner, genre, " ".join(keywords)]
        search_text = normalize_text(" ".join(p for p in parts if p))

    return {
        "id": item_id,
        "title": title,
        "author": author,
        "isbn": isbn,
        "type": media_type,
        "owner": owner,
        "date_added": date_added,
        "is_new": is_new,
        "cover_url": cover_url,
        "description": description,
        "genres": keywords,
        "genre": genre,
        "metadata_source": metadata_source,
        "isbn_source": isbn_source,
        "status": status,
        "openlibrary_link": ol_link,
        "google_books_link": gb_link,
        "dnb_link": dnb_link,
        "search_text": search_text,
    }


def load_items_from_dir(item_dir: Path, cutoff: dt.date) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not item_dir.exists():
        return items

    for path in sorted(item_dir.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        if not raw.get("id") and "@type" not in raw:
            raw["id"] = path.stem

        record = item_to_catalog_record(raw, cutoff)
        if record:
            items.append(record)

    items.sort(
        key=lambda x: (normalize_text(x.get("title", "")), normalize_text(x.get("author", "")))
    )
    return items


def build_catalog(args: argparse.Namespace) -> dict[str, Any]:
    now = dt.datetime.now(dt.UTC)
    cutoff = now.date() - dt.timedelta(days=args.days_new)

    items = load_items_from_dir(Path(args.item_dir), cutoff)

    with_isbn = sum(1 for i in items if i.get("isbn"))
    without_isbn = len(items) - with_isbn
    with_metadata = sum(
        1 for i in items if i.get("description") or (i.get("genres") and i.get("isbn"))
    )

    output: dict[str, Any] = {
        "generated_at": now.isoformat(),
        "item_folder": args.item_dir,
        "total_rows": len(items),
        "rows_with_isbn": with_isbn,
        "rows_without_isbn": without_isbn,
        "rows_with_metadata": with_metadata,
        "items": items,
    }

    save_json(Path(args.out), output)
    return output


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Aggregate item JSONs into catalog.json")
    parser.add_argument("--item-dir", default="data/item", help="Folder with item JSON files")
    parser.add_argument("--out", default="data/catalog.json", help="Output catalog JSON")
    parser.add_argument("--days-new", type=int, default=90, help="Days threshold for is_new flag")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    result = build_catalog(args)
    print(
        f"Katalog erstellt: {args.out} | Eintraege: {result['total_rows']} | "
        f"mit ISBN: {result['rows_with_isbn']} | ohne ISBN: {result['rows_without_isbn']}"
    )


if __name__ == "__main__":
    main()
