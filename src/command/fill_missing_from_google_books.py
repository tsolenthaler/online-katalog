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
import datetime as dt
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
    genre: str = ""
    short_description: str = ""
    cover_url: str = ""
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


def strip_html(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def shorten_text(value: str, max_length: int = 320) -> str:
    value = value.strip()
    if len(value) <= max_length:
        return value
    cut = value[: max_length - 1].rstrip()
    return f"{cut}..."


def to_https(url: str) -> str:
    if url.startswith("http://"):
        return "https://" + url[len("http://") :]
    return url


def extract_cover_url(image_links: dict[str, Any]) -> str:
    for key in ("extraLarge", "large", "medium", "small", "thumbnail", "smallThumbnail"):
        value = image_links.get(key)
        if isinstance(value, str) and value.strip():
            return to_https(value.strip())
    return ""


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


def build_search_text(title: str, author: str, genres: list[str], description: str) -> str:
    raw = " ".join([title, author, " ".join(genres), description]).strip()
    return normalize_text(raw)


def build_cache_key(title: str, author: str) -> str:
    return f"{normalize_text(title)}|{normalize_text(author)}"


def resolve_optional_input_path(path_value: str) -> Path:
    path = Path(path_value)
    if path.exists():
        return path

    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent.parent
    fallback = repo_root / path
    if fallback.exists():
        return fallback

    return path


def load_manual_overrides(path: Path, quiet: bool) -> dict[str, dict[str, str]]:
    overrides_by_title: dict[str, dict[str, str]] = {}

    if not path.exists():
        if not quiet:
            print(f"Manual override file not found (ignored): {path}", flush=True)
        return overrides_by_title

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        required = {"title", "author", "isbn"}
        if not required.issubset(set(fieldnames)):
            raise ValueError(
                f"Manual override CSV requires columns: title, author, isbn (got: {', '.join(fieldnames)})"
            )

        for row in reader:
            title = str(row.get("title") or "").strip()
            author = str(row.get("author") or "").strip()
            isbn = isbn_digits(str(row.get("isbn") or "").strip())
            if not title or not author or not isbn:
                continue

            title_key = normalize_text(title)
            if not title_key:
                continue

            overrides_by_title[title_key] = {
                "title": title,
                "author": author,
                "isbn": isbn,
            }

    if not quiet:
        print(f"Loaded manual overrides (title match): {len(overrides_by_title)}", flush=True)

    return overrides_by_title


def isbn_type_for(value: str) -> str:
    if len(value) == 13:
        return "ISBN_13"
    if len(value) == 10:
        return "ISBN_10"
    return ""


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
                genre=(row.get("genre") or "").strip(),
                short_description=(row.get("short_description") or "").strip(),
                cover_url=(row.get("cover_url") or "").strip(),
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
            "genre",
            "short_description",
            "cover_url",
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
                "genre": result.genre,
                "short_description": result.short_description,
                "cover_url": result.cover_url,
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

        categories = volume_info.get("categories", [])
        genre = ""
        if isinstance(categories, list):
            cleaned = dedupe_keep_order([str(cat).strip() for cat in categories if str(cat).strip()])
            genre = " | ".join(cleaned)

        short_description = ""
        description_value = volume_info.get("description")
        if isinstance(description_value, str):
            short_description = shorten_text(strip_html(description_value))

        cover_url = ""
        image_links = volume_info.get("imageLinks")
        if isinstance(image_links, dict):
            cover_url = extract_cover_url(image_links)

        matched_title = str(volume_info.get("title") or "").strip()
        google_id = str(item.get("id") or "").strip()

        return LookupResult(
            found=True,
            isbn=isbn,
            isbn_type=isbn_type,
            matched_title=matched_title,
            matched_author=matched_author,
            genre=genre,
            short_description=short_description,
            cover_url=cover_url,
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
        help="Output CSV for found entries (default: data/books_with_isbn_google.csv)",
    )
    parser.add_argument(
        "--missing-out",
        dest="missing_output_csv",
        default="data/missing_google.csv",
        help="Output CSV for missing entries (default: data/missing_google.csv)",
    )
    parser.add_argument(
        "--cache",
        dest="cache_csv",
        default="data/google_books_cache.csv",
        help="Cache CSV to avoid duplicate API calls (default: data/google_books_cache.csv)",
    )
    parser.add_argument(
        "--manual-overrides",
        dest="manual_overrides_csv",
        default="data/manual_catalog_overrides.csv",
        help=(
            "Optional CSV with columns title,author,isbn. "
            "Rows matching by title are used before cache/API (default: data/manual_catalog_overrides.csv)"
        ),
    )
    parser.add_argument(
        "--catalog-json-out",
        dest="catalog_json_out",
        default="data/catalog_google.json",
        help="Catalog JSON output for direct website usage (default: data/catalog_google.json)",
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

    input_csv = resolve_optional_input_path(args.input_csv)
    output_csv = Path(args.output_csv)
    missing_output_csv = Path(args.missing_output_csv)
    cache_csv = Path(args.cache_csv)
    catalog_json_out = Path(args.catalog_json_out)
    manual_overrides_csv = resolve_optional_input_path(args.manual_overrides_csv)

    if not input_csv.exists():
        raise SystemExit(f"Input CSV not found: {input_csv}")

    api_key = args.api_key or os.getenv("GOOGLE_BOOKS_API_KEY", "")

    cache = load_cache(cache_csv)
    overrides_by_title = load_manual_overrides(manual_overrides_csv, quiet=args.quiet)
    api_calls = 0
    found_count = 0
    cache_hits = 0
    quota_skipped = 0
    manual_override_hits = 0
    catalog_items: list[dict[str, Any]] = []

    with input_csv.open("r", encoding="utf-8", newline="") as src:
        reader = csv.DictReader(src)
        rows = list(reader)

    if args.max_rows is not None and args.max_rows >= 0:
        rows = rows[: args.max_rows]

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    missing_output_csv.parent.mkdir(parents=True, exist_ok=True)

    with output_csv.open("w", encoding="utf-8", newline="") as out_handle, missing_output_csv.open(
        "w", encoding="utf-8", newline=""
    ) as missing_handle:
        fieldnames = [
            "title",
            "author",
            "isbn",
            "isbn_type",
            "matched_title",
            "matched_author",
            "genre",
            "short_description",
            "cover_url",
            "google_id",
            "status",
            "from_cache",
        ]
        writer = csv.DictWriter(out_handle, fieldnames=fieldnames)
        writer.writeheader()

        missing_fieldnames = [
            "title",
            "author",
            "status",
            "from_cache",
        ]
        missing_writer = csv.DictWriter(missing_handle, fieldnames=missing_fieldnames)
        missing_writer.writeheader()

        for idx, row in enumerate(rows, start=1):
            title = str(row.get("title") or "").strip()
            author = str(row.get("author") or "").strip()
            cache_key = build_cache_key(title=title, author=author)
            title_key = normalize_text(title)

            from_cache = False
            result: LookupResult | None = None

            if not args.quiet:
                display_author = author if author else "-"
                print(f"[{idx}/{len(rows)}] query: title='{title}' author='{display_author}'", flush=True)

            override_entry = overrides_by_title.get(title_key) if title_key else None
            if override_entry is not None:
                manual_override_hits += 1
                result = LookupResult(
                    found=True,
                    isbn=override_entry["isbn"],
                    isbn_type=isbn_type_for(override_entry["isbn"]),
                    matched_title=override_entry["title"],
                    matched_author=override_entry["author"],
                    status="manual_override",
                )
                if not args.quiet:
                    print(
                        "  -> manual override: "
                        f"isbn={result.isbn} title='{result.matched_title}' author='{result.matched_author}'",
                        flush=True,
                    )
            else:
                result = cache.get(cache_key)

            if override_entry is None and result is not None:
                from_cache = True
                cache_hits += 1
                if not args.quiet:
                    print(
                        f"  -> cache hit: status={result.status} isbn={result.isbn or '-'}",
                        flush=True,
                    )
            elif override_entry is None:
                if api_calls >= args.max_requests:
                    result = LookupResult(found=False, status="quota_guard_reached")
                    quota_skipped += 1
                    if not args.quiet:
                        print("  -> skipped: quota guard reached", flush=True)
                else:
                    if not args.quiet:
                        print("  -> api request: google books", flush=True)
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

                    if not args.quiet:
                        if result.found:
                            print(
                                f"  -> found: isbn={result.isbn} title='{result.matched_title or title}'",
                                flush=True,
                            )
                        else:
                            print(f"  -> no isbn found: status={result.status}", flush=True)

            if result is None:
                result = LookupResult(found=False, status="not_found")

            if result.found:
                found_count += 1
                writer.writerow(
                    {
                        "title": title,
                        "author": result.matched_author or author,
                        "isbn": result.isbn,
                        "isbn_type": result.isbn_type,
                        "matched_title": result.matched_title,
                        "matched_author": result.matched_author,
                        "genre": result.genre,
                        "short_description": result.short_description,
                        "cover_url": result.cover_url,
                        "google_id": result.google_id,
                        "status": result.status,
                        "from_cache": str(from_cache).lower(),
                    }
                )

                genres = [part.strip() for part in result.genre.split("|") if part.strip()] if result.genre else []
                catalog_items.append(
                    {
                        "id": result.isbn,
                        "title": result.matched_title or title,
                        "author": result.matched_author or author,
                        "isbn": result.isbn,
                        "cover_url": result.cover_url,
                        "description": result.short_description,
                        "genres": genres,
                        "genre": genres[0] if genres else "",
                        "metadata_source": "google_books",
                        "isbn_source": "google_books",
                        "search_text": build_search_text(
                            title=result.matched_title or title,
                            author=result.matched_author or author,
                            genres=genres,
                            description=result.short_description,
                        ),
                    }
                )
            else:
                missing_writer.writerow(
                    {
                        "title": title,
                        "author": author,
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
        flush_to_disk(missing_handle)

    catalog_payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source_csv": str(input_csv),
        "total_rows": len(rows),
        "rows_with_isbn": found_count,
        "rows_missing": len(rows) - found_count,
        "items": catalog_items,
    }
    catalog_json_out.parent.mkdir(parents=True, exist_ok=True)
    with catalog_json_out.open("w", encoding="utf-8") as catalog_handle:
        json.dump(catalog_payload, catalog_handle, ensure_ascii=False, indent=2)
        catalog_handle.write("\n")
        flush_to_disk(catalog_handle)

    if not args.quiet:
        print("Done.", flush=True)
        print(f"Input rows: {len(rows)}", flush=True)
        print(f"Found ISBNs: {found_count}", flush=True)
        print(f"API calls this run: {api_calls}", flush=True)
        print(f"Cache hits: {cache_hits}", flush=True)
        print(f"Manual override hits: {manual_override_hits}", flush=True)
        print(f"Skipped by quota guard: {quota_skipped}", flush=True)
        print(f"Found output: {output_csv}", flush=True)
        print(f"Missing output: {missing_output_csv}", flush=True)
        print(f"Catalog JSON output: {catalog_json_out}", flush=True)
        print(f"Cache: {cache_csv}", flush=True)


if __name__ == "__main__":
    main()
