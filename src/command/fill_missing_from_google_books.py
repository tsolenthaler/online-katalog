#!/usr/bin/env python3
"""Fill missing ISBN rows by querying Google Books and caching results.

This command reads a CSV (default: data/missing.csv), searches Google Books by
"title + author", extracts valid ISBN-13/ISBN-10 values, and writes:
- rows with ISBN to an output CSV
- rows without ISBN to a not-found CSV

A CSV cache stores both positive and negative lookups so repeated runs avoid
re-querying known rows.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus
from urllib.request import urlopen


GOOGLE_BOOKS_URL = "https://www.googleapis.com/books/v1/volumes"


@dataclass
class MatchResult:
    isbn: str
    matched_title: str
    matched_author: str
    source: str = "google_books"


@dataclass
class CachedLookup:
    found: bool
    match: MatchResult | None = None


def flush_to_disk(handle: Any) -> None:
    handle.flush()
    os.fsync(handle.fileno())


def normalize_text(value: str) -> str:
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


def choose_best_isbn(candidates: list[str]) -> str | None:
    normalized = [isbn_digits(value) for value in candidates]

    valid13 = [value for value in normalized if is_valid_isbn13(value)]
    if valid13:
        return valid13[0]

    valid10 = [value for value in normalized if is_valid_isbn10(value)]
    if valid10:
        return valid10[0]

    return None


def load_cache(cache_csv: Path) -> dict[str, CachedLookup]:
    cache: dict[str, CachedLookup] = {}
    if not cache_csv.exists():
        return cache

    with cache_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            cache_key = (row.get("cache_key") or "").strip()
            if not cache_key:
                continue

            status = (row.get("status") or "").strip().lower()
            isbn = isbn_digits((row.get("isbn") or "").strip())

            if status != "found" or not isbn or not (is_valid_isbn13(isbn) or is_valid_isbn10(isbn)):
                cache[cache_key] = CachedLookup(found=False)
                continue

            cache[cache_key] = CachedLookup(
                found=True,
                match=MatchResult(
                    isbn=isbn,
                    matched_title=(row.get("matched_title") or "").strip(),
                    matched_author=(row.get("matched_author") or "").strip(),
                    source=(row.get("source") or "google_books").strip() or "google_books",
                ),
            )

    return cache


def append_cache(
    cache_csv: Path,
    cache_key: str,
    title: str,
    author: str,
    lookup: CachedLookup,
) -> None:
    exists = cache_csv.exists()
    cache_csv.parent.mkdir(parents=True, exist_ok=True)

    with cache_csv.open("a", encoding="utf-8", newline="") as handle:
        fieldnames = [
            "cache_key",
            "status",
            "title",
            "author",
            "isbn",
            "matched_title",
            "matched_author",
            "source",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()

        writer.writerow(
            {
                "cache_key": cache_key,
                "status": "found" if lookup.found else "checked",
                "title": title,
                "author": author,
                "isbn": lookup.match.isbn if lookup.match else "",
                "matched_title": lookup.match.matched_title if lookup.match else "",
                "matched_author": lookup.match.matched_author if lookup.match else "",
                "source": lookup.match.source if lookup.match else "not_found",
            }
        )
        flush_to_disk(handle)


def fetch_google_match(title: str, author: str, max_results: int = 10) -> MatchResult | None:
    query = " ".join(part for part in [title.strip(), author.strip()] if part).strip()
    if not query:
        return None

    url = f"{GOOGLE_BOOKS_URL}?q={quote_plus(query)}&maxResults={max(1, min(max_results, 40))}"
    with urlopen(url, timeout=20) as response:  # noqa: S310
        payload = response.read().decode("utf-8")

    import json

    parsed = json.loads(payload)
    items = parsed.get("items", []) if isinstance(parsed, dict) else []
    if not isinstance(items, list):
        return None

    for item in items:
        if not isinstance(item, dict):
            continue
        volume_info = item.get("volumeInfo")
        if not isinstance(volume_info, dict):
            continue

        identifiers = volume_info.get("industryIdentifiers")
        if not isinstance(identifiers, list):
            continue

        raw_candidates: list[str] = []
        for ident in identifiers:
            if not isinstance(ident, dict):
                continue
            raw_value = ident.get("identifier")
            if isinstance(raw_value, str) and raw_value.strip():
                raw_candidates.append(raw_value.strip())

        chosen = choose_best_isbn(raw_candidates)
        if not chosen:
            continue

        matched_title = str(volume_info.get("title") or "").strip()

        matched_authors = volume_info.get("authors")
        if isinstance(matched_authors, list):
            author_values = [str(value).strip() for value in matched_authors if str(value).strip()]
        else:
            author_values = []
        matched_author = ", ".join(author_values)

        return MatchResult(
            isbn=chosen,
            matched_title=matched_title,
            matched_author=matched_author,
        )

    return None


def process_missing(
    input_csv: Path,
    output_csv: Path,
    not_found_csv: Path,
    cache_csv: Path,
    delay_seconds: float,
    max_rows: int | None,
    max_results: int,
    verbose: bool,
) -> tuple[int, int]:
    cache = load_cache(cache_csv)
    found_count = 0
    total = 0

    if verbose:
        print(f"Reading input: {input_csv}", flush=True)
        print(f"Writing found rows to: {output_csv}", flush=True)
        print(f"Writing not-found rows to: {not_found_csv}", flush=True)
        print(f"Using cache: {cache_csv} (loaded {len(cache)} entries)", flush=True)

    with (
        input_csv.open("r", encoding="utf-8", newline="") as in_handle,
        output_csv.open("w", encoding="utf-8", newline="") as out_handle,
        not_found_csv.open("w", encoding="utf-8", newline="") as not_found_handle,
    ):
        reader = csv.DictReader(in_handle)
        base_fields = list(reader.fieldnames or [])
        if "title" not in base_fields:
            raise ValueError("Input CSV requires a 'title' column.")
        if "author" not in base_fields:
            base_fields.append("author")

        out_fields = base_fields + ["isbn", "matched_title", "matched_author", "source"]
        out_writer = csv.DictWriter(out_handle, fieldnames=out_fields)
        out_writer.writeheader()
        flush_to_disk(out_handle)

        not_found_writer = csv.DictWriter(not_found_handle, fieldnames=base_fields)
        not_found_writer.writeheader()
        flush_to_disk(not_found_handle)

        for row in reader:
            if max_rows is not None and total >= max_rows:
                break

            total += 1
            title = (row.get("title") or "").strip()
            author = (row.get("author") or "").strip()

            base_row = dict(row)
            base_row.setdefault("author", author)

            cache_key = build_cache_key(title, author)
            cached = cache.get(cache_key)
            match: MatchResult | None = None

            if cached and cached.found and cached.match:
                match = cached.match
                if verbose:
                    print(f"[{total}] cache hit: {title} -> {match.isbn}", flush=True)
            elif cached and not cached.found:
                if verbose:
                    print(f"[{total}] cache says not found: {title}", flush=True)
            else:
                if verbose:
                    print(f"[{total}] querying Google Books: {title}", flush=True)
                try:
                    match = fetch_google_match(title=title, author=author, max_results=max_results)
                except Exception:
                    match = None

                if match:
                    lookup = CachedLookup(found=True, match=match)
                    cache[cache_key] = lookup
                    append_cache(cache_csv, cache_key, title, author, lookup)
                else:
                    lookup = CachedLookup(found=False)
                    cache[cache_key] = lookup
                    append_cache(cache_csv, cache_key, title, author, lookup)

                if delay_seconds > 0:
                    time.sleep(delay_seconds)

            if match:
                found_count += 1
                enriched = dict(base_row)
                enriched.update(
                    {
                        "isbn": match.isbn,
                        "matched_title": match.matched_title,
                        "matched_author": match.matched_author,
                        "source": match.source,
                    }
                )
                out_writer.writerow(enriched)
            else:
                not_found_writer.writerow(base_row)

        flush_to_disk(out_handle)
        flush_to_disk(not_found_handle)

    return total, found_count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Find ISBN for data/missing.csv rows via Google Books with persistent cache."
    )
    parser.add_argument(
        "--in",
        dest="input_csv",
        default="data/missing.csv",
        help="Input CSV path (default: data/missing.csv)",
    )
    parser.add_argument(
        "--out",
        dest="output_csv",
        default="data/found_google.csv",
        help="Output CSV path for found ISBN rows (default: data/found_google.csv)",
    )
    parser.add_argument(
        "--not-found",
        dest="not_found_csv",
        default="data/missing_google.csv",
        help="Output CSV path for still-missing rows (default: data/missing_google.csv)",
    )
    parser.add_argument(
        "--cache",
        dest="cache_csv",
        default="data/google_books_cache.csv",
        help="Cache CSV path (default: data/google_books_cache.csv)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.25,
        help="Delay between uncached requests in seconds (default: 0.25)",
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        default=None,
        help="Optional limit of rows to process",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=10,
        help="Google Books maxResults per query (default: 10, max: 40)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Disable progress logging",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    input_csv = Path(args.input_csv)
    output_csv = Path(args.output_csv)
    not_found_csv = Path(args.not_found_csv)
    cache_csv = Path(args.cache_csv)

    if not input_csv.exists():
        raise FileNotFoundError(f"Input file does not exist: {input_csv}")

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    not_found_csv.parent.mkdir(parents=True, exist_ok=True)
    cache_csv.parent.mkdir(parents=True, exist_ok=True)

    total, found = process_missing(
        input_csv=input_csv,
        output_csv=output_csv,
        not_found_csv=not_found_csv,
        cache_csv=cache_csv,
        delay_seconds=max(0.0, args.delay),
        max_rows=args.max_rows,
        max_results=args.max_results,
        verbose=not args.quiet,
    )

    print(f"Processed rows: {total}")
    print(f"Found ISBNs: {found}")
    print(f"Still missing: {total - found}")
    print(f"Found CSV: {output_csv}")
    print(f"Remaining CSV: {not_found_csv}")
    print(f"Cache CSV: {cache_csv}")


if __name__ == "__main__":
    main()
