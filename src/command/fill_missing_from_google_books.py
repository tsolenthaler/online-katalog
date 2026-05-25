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
import json
import os
import re
import time
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, parse_qsl, quote, quote_plus, unquote, urlencode, urlparse, urlsplit, urlunsplit
from urllib.request import Request, urlopen


GOOGLE_SEARCH_URL = "https://www.google.com/search"
GOOGLE_BOOKS_API_URL = "https://www.googleapis.com/books/v1/volumes"
DUCKDUCKGO_HTML_URL = "https://duckduckgo.com/html/"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


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


def fetch_text(url: str, timeout: int = 20) -> str:
    url = sanitize_url(url)
    req = Request(
        url,
        headers={
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept-Language": "de-CH,de;q=0.9,en;q=0.8",
        },
    )
    with urlopen(req, timeout=timeout) as response:  # noqa: S310
        return response.read().decode("utf-8", "ignore")


def sanitize_url(url: str) -> str:
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return url

    safe_path = quote(parts.path, safe="/%:@-._~")
    safe_query = urlencode(parse_qsl(parts.query, keep_blank_values=True), doseq=True)
    safe_fragment = quote(parts.fragment, safe="")
    return urlunsplit((parts.scheme, parts.netloc, safe_path, safe_query, safe_fragment))


def build_search_query(title: str, author: str) -> str:
    title = title.strip()
    author = author.strip()
    if title and author:
        return f"{title},{author}"
    return title or author


def build_google_search_url(query: str) -> str:
    # Keep this URL format fixed to match the requested query style.
    return f"{GOOGLE_SEARCH_URL}?q={quote_plus(query)}&hl=de&gl=ch&udm=36"


def decode_google_redirect(href: str) -> str | None:
    if not href:
        return None

    href = unescape(href)
    if href.startswith("/url?"):
        parsed = urlparse(href)
        params = parse_qs(parsed.query)
        candidate = params.get("q", [""])[0] or params.get("url", [""])[0]
        candidate = unquote(candidate)
        if candidate.startswith("http://") or candidate.startswith("https://"):
            return candidate
        return None

    if href.startswith("http://") or href.startswith("https://"):
        return href

    return None


def extract_result_links(search_html: str) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()

    for match in re.finditer(r'href="([^"]+)"', search_html):
        href = match.group(1)
        decoded = decode_google_redirect(href)
        if not decoded:
            continue

        if "google." in urlparse(decoded).netloc and "/search" in decoded:
            continue

        if decoded in seen:
            continue

        seen.add(decoded)
        links.append(decoded)

    return links


def find_first_result_url(search_html: str) -> str | None:
    links = extract_result_links(search_html)

    # Use the first real search result in page order as requested.
    # Ignore internal Google utility pages that are not actual result targets.
    blocked_paths = {
        "/preferences",
        "/advanced_search",
        "/setprefs",
        "/support",
    }

    for link in links:
        parsed = urlparse(link)
        netloc = parsed.netloc.lower()
        path = parsed.path or ""
        if netloc.startswith("support.google."):
            continue
        if "google." in netloc and (path in blocked_paths or path.startswith("/policies")):
            continue
        return link

    return None


def find_first_books_result_url(search_html: str) -> str | None:
    for link in extract_result_links(search_html):
        parsed = urlparse(link)
        netloc = parsed.netloc.lower()
        path = (parsed.path or "").lower()
        if "books.google" in netloc:
            return link
        if path.startswith("/books/edition") or path.startswith("/books"):
            return link
    return None


def build_books_detail_url(volume_id: str) -> str:
    return f"https://www.google.com/books/edition/_/{quote(volume_id)}?hl=de&gbpv=0"


def find_first_books_result_via_api(title: str, author: str) -> str | None:
    query_parts = []
    if title.strip():
        query_parts.append(f'intitle:"{title.strip()}"')
    if author.strip():
        query_parts.append(f'inauthor:"{author.strip()}"')
    query = " ".join(query_parts).strip() or build_search_query(title, author)
    if not query:
        return None

    params = {
        "q": query,
        "maxResults": "1",
        "printType": "books",
        "projection": "lite",
        "langRestrict": "de",
    }
    url = f"{GOOGLE_BOOKS_API_URL}?{urlencode(params)}"
    payload = fetch_text(url)
    data = json.loads(payload)

    items = data.get("items")
    if not isinstance(items, list) or not items:
        return None

    first = items[0] if isinstance(items[0], dict) else {}
    volume_id = str(first.get("id") or "").strip()
    if volume_id:
        return build_books_detail_url(volume_id)

    volume_info = first.get("volumeInfo") if isinstance(first.get("volumeInfo"), dict) else {}
    info_link = str(volume_info.get("infoLink") or "").strip()
    if info_link.startswith("http://") or info_link.startswith("https://"):
        return info_link

    return None


def decode_duckduckgo_result_link(href: str) -> str | None:
    if not href:
        return None

    href = unescape(href)
    if href.startswith("//"):
        href = f"https:{href}"

    if href.startswith("https://duckduckgo.com/l/") or href.startswith("http://duckduckgo.com/l/"):
        parsed = urlparse(href)
        params = parse_qs(parsed.query)
        candidate = params.get("uddg", [""])[0]
        candidate = unquote(candidate)
        if candidate.startswith("http://") or candidate.startswith("https://"):
            return candidate
        return None

    if href.startswith("http://") or href.startswith("https://"):
        return href

    return None


def find_first_books_result_via_duckduckgo(title: str, author: str) -> str | None:
    terms = " ".join(part for part in [title.strip(), author.strip()] if part).strip()
    if not terms:
        return None

    query = f"site:books.google.com {terms}"
    search_url = f"{DUCKDUCKGO_HTML_URL}?{urlencode({'q': query})}"
    html = fetch_text(search_url)

    for match in re.finditer(r'href="([^"]+)"', html):
        candidate = decode_duckduckgo_result_link(match.group(1))
        if not candidate:
            continue
        netloc = urlparse(candidate).netloc.lower()
        if "books.google" in netloc:
            return candidate

    return None


def extract_isbn_candidates_from_text(text: str) -> list[str]:
    candidates: list[str] = []

    patterns = [
        r'"type"\s*:\s*"ISBN_13"\s*,\s*"identifier"\s*:\s*"([0-9Xx\- ]+)"',
        r'"type"\s*:\s*"ISBN_10"\s*,\s*"identifier"\s*:\s*"([0-9Xx\- ]+)"',
        r'ISBN(?:-13)?\s*[:\-]?\s*([0-9][0-9Xx\- ]{11,20})',
        r'ISBN(?:-10)?\s*[:\-]?\s*([0-9Xx][0-9Xx\- ]{8,15})',
    ]

    for pattern in patterns:
        for match in re.findall(pattern, text, flags=re.IGNORECASE):
            value = str(match).strip()
            if value:
                candidates.append(value)

    # Last-resort broad scan for 13-digit ISBNs in page text.
    for match in re.findall(r'\b97[89][0-9\- ]{10,20}\b', text):
        candidates.append(match)

    return candidates


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


def fetch_google_match(title: str, author: str, verbose: bool = False) -> MatchResult | None:
    query = build_search_query(title=title, author=author)
    if not query:
        return None

    # Primary flow requested by user:
    # 1) Search on google.com with udm=36
    # 2) Take first result
    # 3) Read ISBN from result detail page
    search_url = build_google_search_url(query)
    if verbose:
        print(f"    search_url={search_url}", flush=True)

    try:
        search_html = fetch_text(search_url)
        first_result = find_first_result_url(search_html)
        books_first_result = find_first_books_result_url(search_html)
        detail_candidates: list[str] = []
        if first_result:
            detail_candidates.append(first_result)
        if books_first_result and books_first_result not in detail_candidates:
            detail_candidates.append(books_first_result)

        if not detail_candidates:
            try:
                api_result = find_first_books_result_via_api(title=title, author=author)
            except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError):
                api_result = None
            if api_result:
                detail_candidates.append(api_result)

        if not detail_candidates:
            try:
                ddg_result = find_first_books_result_via_duckduckgo(title=title, author=author)
            except (HTTPError, URLError, TimeoutError, ValueError):
                ddg_result = None
            if ddg_result:
                detail_candidates.append(ddg_result)

        for detail_url in detail_candidates:
            if verbose:
                print(f"    detail_url={detail_url}", flush=True)

            detail_html = fetch_text(detail_url)
            candidates = extract_isbn_candidates_from_text(detail_html)
            chosen = choose_best_isbn(candidates)
            if not chosen:
                continue

            title_match = re.search(r"<title>(.*?)</title>", detail_html, flags=re.IGNORECASE | re.DOTALL)
            matched_title = ""
            if title_match:
                matched_title = re.sub(r"\s+", " ", unescape(title_match.group(1))).strip()

            return MatchResult(
                isbn=chosen,
                matched_title=matched_title,
                matched_author="",
                source="google_search_first_result",
            )
    except (HTTPError, URLError, TimeoutError, ValueError):
        pass
    return None


def process_missing(
    input_csv: Path,
    output_csv: Path,
    not_found_csv: Path,
    cache_csv: Path,
    delay_seconds: float,
    max_rows: int | None,
    respect_missing_cache: bool,
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
                if respect_missing_cache:
                    if verbose:
                        print(f"[{total}] cache says not found: {title}", flush=True)
                else:
                    if verbose:
                        print(f"[{total}] rechecking cached not-found: {title}", flush=True)
                    cached = None

            if cached is None and match is None:
                if verbose:
                    print(f"[{total}] querying Google Search -> first result: {title}", flush=True)
                try:
                    match = fetch_google_match(title=title, author=author, verbose=verbose)
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
        "--respect-missing-cache",
        action="store_true",
        help="Do not retry rows previously cached as not found",
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
        respect_missing_cache=args.respect_missing_cache,
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
