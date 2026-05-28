#!/usr/bin/env python3
"""Merge the main catalog with Google Books results into one catalog JSON."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path
from typing import Any


def normalize_text(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def build_search_text(title: str, author: str, genres: list[str], description: str) -> str:
    parts = [title, author, " ".join(genres), description]
    raw = " ".join(part for part in parts if part)
    return normalize_text(raw)


def dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = str(value).strip()
        key = cleaned.lower()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def load_catalog(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Catalog {path} must contain a JSON object")

    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError(f"Catalog {path} must contain an 'items' array")

    return payload


def item_key(item: dict[str, Any]) -> str:
    for field in ("id", "isbn"):
        value = str(item.get(field) or "").strip()
        if value:
            return value

    title = str(item.get("title") or "").strip().lower()
    author = str(item.get("author") or "").strip().lower()
    return f"{title}|{author}"


def merge_source_label(primary: str, secondary: str) -> str:
    labels = dedupe_keep_order([
        part.strip()
        for value in (primary, secondary)
        for part in str(value or "").split("+")
        if part.strip()
    ])
    return "+".join(labels)


def merge_items(primary: dict[str, Any], secondary: dict[str, Any]) -> dict[str, Any]:
    merged = dict(primary)

    for field in ("id", "title", "author", "isbn", "cover_url", "description", "isbn_source"):
        if not str(merged.get(field) or "").strip():
            merged[field] = secondary.get(field, "")

    primary_genres = primary.get("genres") if isinstance(primary.get("genres"), list) else []
    secondary_genres = secondary.get("genres") if isinstance(secondary.get("genres"), list) else []
    merged_genres = dedupe_keep_order([*primary_genres, *secondary_genres])
    merged["genres"] = merged_genres
    merged["genre"] = merged_genres[0] if merged_genres else str(merged.get("genre") or secondary.get("genre") or "")
    merged["metadata_source"] = merge_source_label(
        str(primary.get("metadata_source") or ""),
        str(secondary.get("metadata_source") or ""),
    )
    merged["search_text"] = build_search_text(
        title=str(merged.get("title") or ""),
        author=str(merged.get("author") or ""),
        genres=merged_genres,
        description=str(merged.get("description") or ""),
    )
    return merged


def merge_catalogs(
    base_items: list[dict[str, Any]],
    google_items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    merged_by_key: dict[str, dict[str, Any]] = {}
    stats = {
        "base_duplicate_merges": 0,
        "google_duplicate_merges": 0,
        "cross_catalog_merges": 0,
    }

    for item in base_items:
        if not isinstance(item, dict):
            continue
        key = item_key(item)
        existing = merged_by_key.get(key)
        if existing is None:
            merged_by_key[key] = dict(item)
            continue
        merged_by_key[key] = merge_items(existing, item)
        stats["base_duplicate_merges"] += 1

    for item in google_items:
        if not isinstance(item, dict):
            continue
        key = item_key(item)
        existing = merged_by_key.get(key)
        if existing is None:
            merged_by_key[key] = dict(item)
            continue
        if str(existing.get("metadata_source") or "").strip().lower() == "google_books":
            stats["google_duplicate_merges"] += 1
        else:
            stats["cross_catalog_merges"] += 1
        merged_by_key[key] = merge_items(existing, item)

    return list(merged_by_key.values()), stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge data/catalog.json and data/catalog_google.json into one catalog JSON.",
    )
    parser.add_argument(
        "--base",
        default="data/catalog.json",
        help="Primary catalog JSON (default: data/catalog.json)",
    )
    parser.add_argument(
        "--google",
        default="data/catalog_google.json",
        help="Google catalog JSON to merge in (default: data/catalog_google.json)",
    )
    parser.add_argument(
        "--out",
        default="data/catalog.json",
        help="Output JSON path (default: data/catalog.json)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    base_path = Path(args.base)
    google_path = Path(args.google)
    out_path = Path(args.out)

    base_catalog = load_catalog(base_path)
    google_catalog = load_catalog(google_path)
    merged_items, merge_stats = merge_catalogs(
        [item for item in base_catalog.get("items", []) if isinstance(item, dict)],
        [item for item in google_catalog.get("items", []) if isinstance(item, dict)],
    )

    output = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source_catalogs": [str(base_path), str(google_path)],
        "total_rows": len(merged_items),
        "rows_with_metadata": sum(
            1
            for item in merged_items
            if any(
                [
                    str(item.get("cover_url") or "").strip(),
                    str(item.get("description") or "").strip(),
                    item.get("genres") if isinstance(item.get("genres"), list) else [],
                ]
            )
        ),
        **merge_stats,
        "items": merged_items,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"Merged {len(base_catalog['items'])} base items with {len(google_catalog['items'])} Google items.")
    print(f"Base duplicate merges: {merge_stats['base_duplicate_merges']}")
    print(f"Google duplicate merges: {merge_stats['google_duplicate_merges']}")
    print(f"Cross-catalog merges: {merge_stats['cross_catalog_merges']}")
    print(f"Wrote {len(merged_items)} items to {out_path}")


if __name__ == "__main__":
    main()