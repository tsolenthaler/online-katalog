#!/usr/bin/env python3
"""Build a search-ready catalog JSON from a CSV enriched with ISBN values.

The script reads rows from a CSV (usually data/books_with_isbn.csv), fetches
metadata by ISBN (cover URL, description, genre/category), and writes a
catalog JSON suitable for client-side web search.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus
from urllib.request import urlopen


OPENLIBRARY_BOOKS_URL = "https://openlibrary.org/api/books"
GOOGLE_BOOKS_URL = "https://www.googleapis.com/books/v1/volumes"


def flush_to_disk(handle: Any) -> None:
    handle.flush()
    os.fsync(handle.fileno())


def isbn_digits(raw: str) -> str:
    return re.sub(r"[^0-9Xx]", "", raw).upper()


def strip_html(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def normalize_text(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def to_https(url: str) -> str:
    if url.startswith("http://"):
        return "https://" + url[len("http://") :]
    return url


def choose_cover_url(openlibrary_cover: str, google_cover: str) -> str:
    if openlibrary_cover:
        return to_https(openlibrary_cover)
    if google_cover:
        return to_https(google_cover)
    return ""


def load_cache(cache_path: Path) -> dict[str, dict[str, Any]]:
    if not cache_path.exists():
        return {}
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(payload, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for key, value in payload.items():
        if isinstance(key, str) and isinstance(value, dict):
            result[key] = value
    return result


def save_cache(cache_path: Path, cache: dict[str, dict[str, Any]]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open("w", encoding="utf-8") as handle:
        json.dump(cache, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        flush_to_disk(handle)


def fetch_openlibrary(isbn: str) -> dict[str, Any]:
    url = (
        f"{OPENLIBRARY_BOOKS_URL}?bibkeys=ISBN:{quote_plus(isbn)}"
        "&format=json&jscmd=data"
    )
    with urlopen(url, timeout=20) as response:  # noqa: S310
        payload = json.loads(response.read().decode("utf-8"))

    key = f"ISBN:{isbn}"
    book = payload.get(key, {}) if isinstance(payload, dict) else {}
    if not isinstance(book, dict):
        return {}

    cover = ""
    cover_obj = book.get("cover")
    if isinstance(cover_obj, dict):
        for size in ("large", "medium", "small"):
            value = cover_obj.get(size)
            if isinstance(value, str) and value.strip():
                cover = value.strip()
                break

    description = ""
    description_obj = book.get("description")
    if isinstance(description_obj, str):
        description = description_obj.strip()
    elif isinstance(description_obj, dict):
        value = description_obj.get("value")
        if isinstance(value, str):
            description = value.strip()

    if not description:
        notes_obj = book.get("notes")
        if isinstance(notes_obj, str):
            description = notes_obj.strip()
        elif isinstance(notes_obj, dict):
            value = notes_obj.get("value")
            if isinstance(value, str):
                description = value.strip()

    genres: list[str] = []
    for subject in book.get("subjects", []) or []:
        if isinstance(subject, dict):
            name = subject.get("name")
            if isinstance(name, str) and name.strip():
                genres.append(name.strip())

    return {
        "cover_url": cover,
        "description": strip_html(description),
        "genres": dedupe_keep_order(genres),
        "source": "openlibrary",
    }


def fetch_google_books(isbn: str) -> dict[str, Any]:
    url = f"{GOOGLE_BOOKS_URL}?q=isbn:{quote_plus(isbn)}&maxResults=1"
    with urlopen(url, timeout=20) as response:  # noqa: S310
        payload = json.loads(response.read().decode("utf-8"))

    items = payload.get("items", []) if isinstance(payload, dict) else []
    if not isinstance(items, list) or not items:
        return {}

    volume_info = items[0].get("volumeInfo", {}) if isinstance(items[0], dict) else {}
    if not isinstance(volume_info, dict):
        return {}

    cover = ""
    image_links = volume_info.get("imageLinks")
    if isinstance(image_links, dict):
        for key in ("extraLarge", "large", "medium", "small", "thumbnail", "smallThumbnail"):
            value = image_links.get(key)
            if isinstance(value, str) and value.strip():
                cover = value.strip()
                break

    description = ""
    description_obj = volume_info.get("description")
    if isinstance(description_obj, str):
        description = description_obj.strip()

    genres: list[str] = []
    categories = volume_info.get("categories")
    if isinstance(categories, list):
        for category in categories:
            if isinstance(category, str) and category.strip():
                genres.append(category.strip())

    return {
        "cover_url": cover,
        "description": strip_html(description),
        "genres": dedupe_keep_order(genres),
        "source": "google_books",
    }


def dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(value.strip())
    return result


def merge_metadata(openlibrary_data: dict[str, Any], google_data: dict[str, Any]) -> dict[str, Any]:
    ol_cover = str(openlibrary_data.get("cover_url") or "").strip()
    gb_cover = str(google_data.get("cover_url") or "").strip()
    cover_url = choose_cover_url(ol_cover, gb_cover)

    ol_description = str(openlibrary_data.get("description") or "").strip()
    gb_description = str(google_data.get("description") or "").strip()
    description = ol_description or gb_description

    ol_genres = openlibrary_data.get("genres") if isinstance(openlibrary_data.get("genres"), list) else []
    gb_genres = google_data.get("genres") if isinstance(google_data.get("genres"), list) else []
    genres = dedupe_keep_order([
        *(str(item).strip() for item in ol_genres if str(item).strip()),
        *(str(item).strip() for item in gb_genres if str(item).strip()),
    ])

    metadata_source = ""
    if openlibrary_data and google_data:
        metadata_source = "openlibrary+google_books"
    elif openlibrary_data:
        metadata_source = "openlibrary"
    elif google_data:
        metadata_source = "google_books"

    return {
        "cover_url": cover_url,
        "description": description,
        "genres": genres,
        "metadata_source": metadata_source,
    }


def build_search_text(title: str, author: str, genres: list[str], description: str) -> str:
    parts = [title, author, " ".join(genres), description]
    raw = " ".join(part for part in parts if part)
    return normalize_text(raw)


def fetch_metadata_for_isbn(
    isbn: str,
    cache: dict[str, dict[str, Any]],
    delay_seconds: float,
    verbose: bool,
) -> dict[str, Any]:
    cached = cache.get(isbn)
    if cached is not None:
        return cached

    openlibrary_data: dict[str, Any] = {}
    google_data: dict[str, Any] = {}

    try:
        if verbose:
            print(f"  -> fetch openlibrary for ISBN {isbn}", flush=True)
        openlibrary_data = fetch_openlibrary(isbn)
    except Exception:
        openlibrary_data = {}

    if delay_seconds > 0:
        time.sleep(delay_seconds)

    # Fallback for missing pieces. Always query when OpenLibrary is incomplete.
    need_google = not openlibrary_data or not openlibrary_data.get("description") or not openlibrary_data.get("cover_url")
    if need_google:
        try:
            if verbose:
                print(f"  -> fetch google_books for ISBN {isbn}", flush=True)
            google_data = fetch_google_books(isbn)
        except Exception:
            google_data = {}

        if delay_seconds > 0:
            time.sleep(delay_seconds)

    merged = merge_metadata(openlibrary_data=openlibrary_data, google_data=google_data)
    cache[isbn] = merged
    return merged


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch cover_url, description and genre by ISBN and build catalog JSON."
    )
    parser.add_argument(
        "--in",
        dest="input_csv",
        default="data/books_with_isbn.csv",
        help="Input CSV path (default: data/books_with_isbn.csv)",
    )
    parser.add_argument(
        "--out",
        dest="output_json",
        default="data/catalog.json",
        help="Output catalog JSON path (default: data/catalog.json)",
    )
    parser.add_argument(
        "--cache",
        dest="cache_json",
        default="data/catalog_metadata_cache.json",
        help="JSON cache for fetched metadata (default: data/catalog_metadata_cache.json)",
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        default=None,
        help="Optional limit for processed rows (useful for testing)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.15,
        help="Delay between API requests in seconds (default: 0.15)",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=50,
        help="Print progress every N rows (default: 50)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Disable live progress output",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    input_csv = Path(args.input_csv)
    output_json = Path(args.output_json)
    cache_json = Path(args.cache_json)
    request_delay = max(args.delay, 0.0)
    progress_every = max(args.progress_every, 1)
    verbose = not args.quiet

    if not input_csv.exists():
        script_dir = Path(__file__).resolve().parent
        repo_root = script_dir.parent.parent
        fallback = repo_root / input_csv
        if fallback.exists():
            input_csv = fallback
        else:
            raise FileNotFoundError(f"Input CSV not found: {input_csv}")

    output_json.parent.mkdir(parents=True, exist_ok=True)
    cache_json.parent.mkdir(parents=True, exist_ok=True)

    cache = load_cache(cache_json)

    if verbose:
        print(f"Reading input: {input_csv}", flush=True)
        print(f"Writing catalog JSON: {output_json}", flush=True)
        print(f"Using metadata cache: {cache_json}", flush=True)
        print(f"Loaded cache entries: {len(cache)}", flush=True)

    items: list[dict[str, Any]] = []
    total = 0
    with_metadata = 0
    without_isbn = 0

    with input_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        if "title" not in fieldnames:
            raise ValueError("Input CSV requires a 'title' column.")
        if "author" not in fieldnames:
            raise ValueError("Input CSV requires an 'author' column.")
        if "isbn" not in fieldnames:
            raise ValueError("Input CSV requires an 'isbn' column.")

        for row in reader:
            if args.max_rows is not None and total >= args.max_rows:
                break

            total += 1
            title = str(row.get("title") or "").strip()
            author = str(row.get("author") or "").strip()
            isbn = isbn_digits(str(row.get("isbn") or "").strip())
            isbn_source = str(row.get("source") or "").strip()

            if not isbn:
                without_isbn += 1
                if verbose and total % progress_every == 0:
                    print(f"[{total}] no ISBN, skipped metadata", flush=True)

                items.append(
                    {
                        "id": f"row-{total}",
                        "title": title,
                        "author": author,
                        "isbn": "",
                        "cover_url": "",
                        "description": "",
                        "genres": [],
                        "genre": "",
                        "metadata_source": "",
                        "isbn_source": isbn_source,
                        "search_text": build_search_text(title=title, author=author, genres=[], description=""),
                    }
                )
                continue

            if verbose:
                preview = title if len(title) <= 70 else title[:67] + "..."
                print(f"[{total}] Processing: {preview}", flush=True)

            metadata = fetch_metadata_for_isbn(
                isbn=isbn,
                cache=cache,
                delay_seconds=request_delay,
                verbose=verbose,
            )

            cover_url = str(metadata.get("cover_url") or "").strip()
            description = str(metadata.get("description") or "").strip()
            genres = metadata.get("genres") if isinstance(metadata.get("genres"), list) else []
            genres = [str(value).strip() for value in genres if str(value).strip()]
            metadata_source = str(metadata.get("metadata_source") or "").strip()

            if cover_url or description or genres:
                with_metadata += 1

            items.append(
                {
                    "id": isbn,
                    "title": title,
                    "author": author,
                    "isbn": isbn,
                    "cover_url": cover_url,
                    "description": description,
                    "genres": genres,
                    "genre": genres[0] if genres else "",
                    "metadata_source": metadata_source,
                    "isbn_source": isbn_source,
                    "search_text": build_search_text(
                        title=title,
                        author=author,
                        genres=genres,
                        description=description,
                    ),
                }
            )

            if total % progress_every == 0:
                save_cache(cache_json, cache)
                if verbose:
                    print(
                        f"Progress: {total} rows, {with_metadata} with metadata, {without_isbn} without ISBN",
                        flush=True,
                    )

    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source_csv": str(input_csv),
        "total_rows": total,
        "rows_with_metadata": with_metadata,
        "rows_without_isbn": without_isbn,
        "items": items,
    }

    with output_json.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        flush_to_disk(handle)

    save_cache(cache_json, cache)

    print(f"Processed rows: {total}")
    print(f"Rows with metadata: {with_metadata}")
    print(f"Rows without ISBN: {without_isbn}")
    print(f"Catalog written to {output_json}")
    print(f"Metadata cache written to {cache_json}")


if __name__ == "__main__":
    main()