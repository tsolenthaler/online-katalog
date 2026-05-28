#!/usr/bin/env python3
"""Resolve missing ISBN values via Google Books API with quota-safe defaults.

Reads a CSV with at least title/author columns, queries Google Books, and writes
an output CSV with the best ISBN candidate per row. To protect the free quota,
this command uses:
- local CSV cache (avoid repeat requests)
- delay between requests
- hard max request limit per run
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus
from urllib.request import urlopen

GOOGLE_BOOKS_URL = "https://www.googleapis.com/books/v1/volumes"


@dataclass
class LookupResult:
    found: bool
    isbn: str = ""
    isbn_type: str = ""
    matched_title: str = ""
    matched_author: str = ""
    google_id: str = ""
    status: str = "not_found"


def flush_to_disk(handle: Any) -> None:
    handle.flush()
    os.fsync(handle.fileno())


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def build_cache_key(title: str, author: str) -> str:
    return f"{normalize_text(title)}|{normalize_text(author)}"


def isbn_digits(raw: str) -> str:
    return re.sub(r"[^0-9Xx]", "", raw).upper()


def is_valid_isbn10(value: str) -> bool:
    if not re.fullmatch(r"\d{9}[\dX]", value):
        return False
    total = 0
    for i, ch in enumerate(value):
        digit = 10 if ch == "X" else int(ch)
        total += (10 - i) * digit
    return total % 11 == 0


def is_valid_isbn13(value: str) -> bool:
    if not re.fullmatch(r"\d{13}", value):
        return False
    total = 0
    for i, ch in enumerate(value[:12]):
        factor = 1 if i % 2 == 0 else 3
        total += int(ch) * factor
    check = (10 - (total % 10)) % 10
    return check == int(value[-1])


def choose_best_identifier(industry_identifiers: list[dict[str, Any]]) -> tuple[str, str]:
    isbn13 = ""
    isbn10 = ""

    for entry in industry_identifiers:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("type") or "").strip().upper()
        identifier = isbn_digits(str(entry.get("identifier") or ""))
        if kind == "ISBN_13" and is_valid_isbn13(identifier):
            isbn13 = identifier
            break

    if isbn13:
        return isbn13, "ISBN_13"

    for entry in industry_identifiers:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("type") or "").strip().upper()
        identifier = isbn_digits(str(entry.get("identifier") or ""))
        if kind == "ISBN_10" and is_valid_isbn10(identifier):
            isbn10 = identifier
            break

    if isbn10:
        return isbn10, "ISBN_10"

    return "", ""


def load_cache(cache_csv: Path) -> dict[str, LookupResult]:
    cache: dict[str, LookupResult] = {}
    if not cache_csv.exists():
        return cache

    with cache_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            key = (row.get("cache_key") or "").strip()
            if not key:
                continue

            status = (row.get("status") or "").strip() or "not_found"
            cache[key] = LookupResult(
                found=(row.get("found") or "").strip().lower() == "true",
                isbn=(row.get("isbn") or "").strip(),
                isbn_type=(row.get("isbn_type") or "").strip(),
                matched_title=(row.get("matched_title") or "").strip(),
                matched_author=(row.get("matched_author") or "").strip(),
                google_id=(row.get("google_id") or "").strip(),
                status=status,
            )

    return cache


def append_cache(
    cache_csv: Path,
    cache_key: str,
    title: str,
    author: str,
    result: LookupResult,
) -> None:
    exists = cache_csv.exists()
    cache_csv.parent.mkdir(parents=True, exist_ok=True)

    with cache_csv.open("a", encoding="utf-8", newline="") as handle:
        fieldnames = [
            "cache_key",
            "title",
            "author",
            "found",
            "status",
            "isbn",
            "isbn_type",
            "matched_title",
            "matched_author",
            "google_id",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()

        writer.writerow(
            {
                "cache_key": cache_key,
                "title": title,
                "author": author,
                "found": str(result.found).lower(),
                "status": result.status,
                "isbn": result.isbn,
                "isbn_type": result.isbn_type,
                "matched_title": result.matched_title,
                "matched_author": result.matched_author,
                "google_id": result.google_id,
            }
        )
        flush_to_disk(handle)


def build_query(title: str, author: str) -> str:
    title = title.strip()
    author = author.strip()
    if title and author:
        return f'intitle:"{title}"+inauthor:"{author}"'
    if title:
        return f'intitle:"{title}"'
    if author:
        return f'inauthor:"{author}"'
    return ""


def fetch_google_books(title: str, author: str, api_key: str) -> LookupResult:
    query = build_query(title=title, author=author)
    if not query:
        return LookupResult(found=False, status="invalid_query")

    url = f"{GOOGLE_BOOKS_URL}?q={quote_plus(query)}&maxResults=5&printType=books"
    if api_key:
        url = f"{url}&key={quote_plus(api_key)}"

    with urlopen(url, timeout=25) as response:  # noqa: S310
        payload = json.loads(response.read().decode("utf-8"))

    items = payload.get("items", []) if isinstance(payload, dict) else []
    if not isinstance(items, list) or not items:
        return LookupResult(found=False, status="not_found")

    for item in items:
        if not isinstance(item, dict):
            continue
        volume_info = item.get("volumeInfo", {})
        if not isinstance(volume_info, dict):
            continue

        industry_identifiers = volume_info.get("industryIdentifiers", [])
        if not isinstance(industry_identifiers, list):
            industry_identifiers = []

        isbn, isbn_type = choose_best_identifier(industry_identifiers)
        if not isbn:
            continue

        authors = volume_info.get("authors", [])
        matched_author = ""
        if isinstance(authors, list):
            matched_author = ", ".join(str(a).strip() for a in authors if str(a).strip())

        matched_title = str(volume_info.get("title") or "").strip()
        google_id = str(item.get("id") or "").strip()

        return LookupResult(
            found=True,
            isbn=isbn,
            isbn_type=isbn_type,
            matched_title=matched_title,
            matched_author=matched_author,
            google_id=google_id,
            status="found",
        )

    return LookupResult(found=False, status="isbn_missing")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Find ISBNs for missing titles using Google Books API and write CSV output."
    )
    parser.add_argument(
        "--in",
        dest="input_csv",
        default="data/missing.csv",
        help="Input CSV with title,author columns (default: data/missing.csv)",
    )
    parser.add_argument(
        "--out",
        dest="output_csv",
        default="data/books_with_isbn_google.csv",
        help="Output CSV (default: data/books_with_isbn_google.csv)",
    )
    parser.add_argument(
        "--cache",
        dest="cache_csv",
        default="data/google_books_cache.csv",
        help="Cache CSV to avoid duplicate API calls (default: data/google_books_cache.csv)",
    )
    parser.add_argument(
        "--api-key",
        dest="api_key",
        default="",
        help="Optional Google API key (or set GOOGLE_BOOKS_API_KEY env var)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.35,
        help="Delay between API calls in seconds (default: 0.35)",
    )
    parser.add_argument(
        "--max-requests",
        type=int,
        default=300,
        help="Hard cap for API calls per run (default: 300)",
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        default=None,
        help="Optional limit of input rows to process (for tests)",
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
        help="Disable progress output",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    input_csv = Path(args.input_csv)
    output_csv = Path(args.output_csv)
    cache_csv = Path(args.cache_csv)

    if not input_csv.exists():
        raise SystemExit(f"Input CSV not found: {input_csv}")

    api_key = args.api_key or os.getenv("GOOGLE_BOOKS_API_KEY", "")

    cache = load_cache(cache_csv)
    api_calls = 0
    found_count = 0
    cache_hits = 0
    quota_skipped = 0

    with input_csv.open("r", encoding="utf-8", newline="") as src:
        reader = csv.DictReader(src)
        rows = list(reader)

    if args.max_rows is not None and args.max_rows >= 0:
        rows = rows[: args.max_rows]

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8", newline="") as out_handle:
        fieldnames = [
            "title",
            "author",
            "isbn",
            "isbn_type",
            "matched_title",
            "matched_author",
            "google_id",
            "status",
            "from_cache",
        ]
        writer = csv.DictWriter(out_handle, fieldnames=fieldnames)
        writer.writeheader()

        for idx, row in enumerate(rows, start=1):
            title = str(row.get("title") or "").strip()
            author = str(row.get("author") or "").strip()
            cache_key = build_cache_key(title=title, author=author)

            from_cache = False
            result = cache.get(cache_key)

            if result is not None:
                from_cache = True
                cache_hits += 1
            else:
                if api_calls >= args.max_requests:
                    result = LookupResult(found=False, status="quota_guard_reached")
                    quota_skipped += 1
                else:
                    try:
                        result = fetch_google_books(title=title, author=author, api_key=api_key)
                    except Exception:
                        result = LookupResult(found=False, status="request_error")

                    api_calls += 1
                    cache[cache_key] = result
                    append_cache(
                        cache_csv=cache_csv,
                        cache_key=cache_key,
                        title=title,
                        author=author,
                        result=result,
                    )

                    if args.delay > 0:
                        time.sleep(args.delay)

            if result.found:
                found_count += 1

            writer.writerow(
                {
                    "title": title,
                    "author": author,
                    "isbn": result.isbn,
                    "isbn_type": result.isbn_type,
                    "matched_title": result.matched_title,
                    "matched_author": result.matched_author,
                    "google_id": result.google_id,
                    "status": result.status,
                    "from_cache": str(from_cache).lower(),
                }
            )

            if not args.quiet and args.progress_every > 0 and idx % args.progress_every == 0:
                print(
                    f"[{idx}/{len(rows)}] found={found_count} api_calls={api_calls} "
                    f"cache_hits={cache_hits} quota_skipped={quota_skipped}",
                    flush=True,
                )

        flush_to_disk(out_handle)

    if not args.quiet:
        print("Done.", flush=True)
        print(f"Input rows: {len(rows)}", flush=True)
        print(f"Found ISBNs: {found_count}", flush=True)
        print(f"API calls this run: {api_calls}", flush=True)
        print(f"Cache hits: {cache_hits}", flush=True)
        print(f"Skipped by quota guard: {quota_skipped}", flush=True)
        print(f"Output: {output_csv}", flush=True)
        print(f"Cache: {cache_csv}", flush=True)


if __name__ == "__main__":
    main()
