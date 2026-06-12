#!/usr/bin/env python3
"""Generate a FlexSearch-compatible search index from item JSON files.

Reads all data/item/*.json and writes:
  - data/search_index.json   Document list loaded at runtime by the browser
  - assets/search_index.js   Optional ES-module re-export (for build tools)

The browser-side code (assets/catalog.js) loads data/search_index.json,
builds a FlexSearch Document index in memory, and uses it for full-text
search across title, author, genre and description.

FlexSearch CDN: https://cdn.jsdelivr.net/npm/flexsearch/dist/flexsearch.bundle.min.js

Usage
-----
  python scripts/build_index.py
  python scripts/build_index.py --item-dir data/item --out data/search_index.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any

from catalog_utils import clean_text, normalize_isbn, normalize_text, parse_iso_date, save_json

DEFAULT_PLACEHOLDER_COVER = "assets/placeholder-cover.svg"


def _author_name(raw: Any) -> str:
    if isinstance(raw, dict):
        return clean_text(raw.get("name", ""))
    return clean_text(str(raw or ""))


def item_to_search_doc(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Extract the fields relevant for full-text search from an item JSON."""
    if "@type" in raw:
        cat = raw.get("_catalog", {}) or {}
        item_id = clean_text(str(cat.get("id", "") or ""))
        title = clean_text(str(raw.get("name", "") or ""))
        author = _author_name(raw.get("author"))
        isbn = normalize_isbn(str(raw.get("isbn", "") or ""))
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

        media_type = clean_text(str(cat.get("type", "") or "")) or "Buch"
        owner = clean_text(str(cat.get("owner", "") or ""))
        date_added = clean_text(str(cat.get("date_added", "") or ""))
        is_new = bool(cat.get("is_new", False))
        status = clean_text(str(cat.get("status", "") or ""))
        cover_url = clean_text(str(raw.get("image", "") or "")) or DEFAULT_PLACEHOLDER_COVER
    else:
        item_id = clean_text(str(raw.get("id", "") or ""))
        title = clean_text(str(raw.get("title", "") or ""))
        author = clean_text(str(raw.get("author", "") or ""))
        isbn = normalize_isbn(str(raw.get("isbn", "") or ""))
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

        media_type = clean_text(str(raw.get("type", "") or "")) or "Buch"
        owner = clean_text(str(raw.get("owner", "") or ""))
        date_added = clean_text(str(raw.get("date_added", "") or ""))
        is_new = bool(raw.get("is_new", False))
        status = clean_text(str(raw.get("status", "") or ""))
        cover_url = clean_text(str(raw.get("cover_url", "") or "")) or DEFAULT_PLACEHOLDER_COVER

    if not item_id or not title:
        return None

    parsed_date = parse_iso_date(date_added)
    # Recompute is_new with a 90-day window; the stored value might be stale
    is_new = bool(parsed_date and parsed_date >= (dt.date.today() - dt.timedelta(days=90)))

    return {
        # Primary key used by FlexSearch
        "id": item_id,
        # Indexed fields (searched by FlexSearch)
        "title": title,
        "author": author,
        "genre": genre,
        "description": description,
        # Stored-only fields (returned in search results, not indexed)
        "isbn": isbn,
        "type": media_type,
        "owner": owner,
        "date_added": date_added,
        "is_new": is_new,
        "status": status or ("OK" if isbn else "Keine ISBN ermittelt"),
        "cover_url": cover_url,
        "keywords": keywords,
    }


def load_search_docs(item_dir: Path) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    if not item_dir.exists():
        return docs

    for path in sorted(item_dir.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        if not raw.get("id") and "@type" not in raw:
            raw["id"] = path.stem

        doc = item_to_search_doc(raw)
        if doc:
            docs.append(doc)

    docs.sort(
        key=lambda d: (normalize_text(d.get("title", "")), normalize_text(d.get("author", "")))
    )
    return docs


def build_index(args: argparse.Namespace) -> None:
    docs = load_search_docs(Path(args.item_dir))

    output = {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "total": len(docs),
        # FlexSearch Document index configuration hint for the browser
        "flexsearch_config": {
            "document": {
                "id": "id",
                "index": [
                    {"field": "title", "tokenize": "forward", "resolution": 9},
                    {"field": "author", "tokenize": "forward", "resolution": 5},
                    {"field": "genre", "tokenize": "strict", "resolution": 3},
                    {"field": "description", "tokenize": "strict", "resolution": 1},
                ],
                "store": [
                    "id",
                    "title",
                    "author",
                    "genre",
                    "isbn",
                    "type",
                    "owner",
                    "date_added",
                    "is_new",
                    "status",
                    "cover_url",
                    "keywords",
                ],
            }
        },
        "documents": docs,
    }

    out_path = Path(args.out)
    save_json(out_path, output)
    print(f"Suchindex erstellt: {out_path} | Dokumente: {len(docs)}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build FlexSearch index from item JSON files")
    parser.add_argument("--item-dir", default="data/item", help="Folder with item JSON files")
    parser.add_argument(
        "--out",
        default="data/search_index.json",
        help="Output search index JSON",
    )
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    build_index(args)


if __name__ == "__main__":
    main()
