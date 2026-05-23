#!/usr/bin/env python3
"""Find likely ISBNs for books in a CSV and write an enriched output CSV.

The script queries OpenLibrary's search API by title and optional author,
scores candidate results, validates ISBN check digits, and writes one best
matching ISBN per input row.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen


OPENLIBRARY_SEARCH_URL = "https://openlibrary.org/search.json"


@dataclass
class MatchResult:
    isbn: str
    matched_title: str
    matched_author: str
    confidence: float
    source: str = "openlibrary"


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def normalize_author_for_compare(author: str) -> str:
    author = author.strip()
    if not author:
        return ""
    if "," in author:
        parts = [part.strip() for part in author.split(",", maxsplit=1)]
        if len(parts) == 2:
            author = f"{parts[1]} {parts[0]}"
    return normalize_text(author)


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


def pick_preferred_isbn(isbns: list[str]) -> str | None:
    normalized = []
    for candidate in isbns:
        digits = isbn_digits(candidate)
        if is_valid_isbn13(digits):
            normalized.append(digits)
    if normalized:
        return normalized[0]

    normalized = []
    for candidate in isbns:
        digits = isbn_digits(candidate)
        if is_valid_isbn10(digits):
            normalized.append(digits)
    if normalized:
        return normalized[0]

    return None


def title_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def author_similarity(search_author: str, candidate_authors: list[str]) -> float:
    if not search_author:
        return 0.5
    if not candidate_authors:
        return 0.0

    search = normalize_author_for_compare(search_author)
    scores = []
    for cand in candidate_authors:
        cand_norm = normalize_author_for_compare(cand)
        if not cand_norm:
            continue
        scores.append(SequenceMatcher(None, search, cand_norm).ratio())
    return max(scores) if scores else 0.0


def score_candidate(search_title: str, search_author: str, doc: dict[str, Any]) -> float:
    doc_title = normalize_text(str(doc.get("title", "")))
    title_score = title_similarity(normalize_text(search_title), doc_title)

    doc_authors = doc.get("author_name", []) or []
    doc_authors = [str(author) for author in doc_authors]
    author_score = author_similarity(search_author, doc_authors)

    if search_author.strip():
        return title_score * 0.75 + author_score * 0.25
    return title_score


def fetch_openlibrary_docs(title: str, author: str, limit: int = 10) -> list[dict[str, Any]]:
    params = {
        "title": title,
        "limit": str(limit),
    }
    if author.strip():
        params["author"] = author.strip()

    url = f"{OPENLIBRARY_SEARCH_URL}?{urlencode(params)}"
    with urlopen(url, timeout=15) as response:  # noqa: S310
        payload = json.loads(response.read().decode("utf-8"))
    docs = payload.get("docs", [])
    if not isinstance(docs, list):
        return []
    return [doc for doc in docs if isinstance(doc, dict)]


def find_best_match(
    title: str,
    author: str,
    min_confidence: float,
    docs_cache: dict[tuple[str, str], list[dict[str, Any]]],
    request_delay: float,
) -> MatchResult | None:
    cache_key = (title.strip(), author.strip())
    if cache_key not in docs_cache:
        docs_cache[cache_key] = fetch_openlibrary_docs(title=title, author=author)
        if request_delay > 0:
            time.sleep(request_delay)

    docs = docs_cache[cache_key]
    best_doc: dict[str, Any] | None = None
    best_score = -1.0

    for doc in docs:
        isbns = doc.get("isbn", []) or []
        if not isinstance(isbns, list):
            continue
        chosen_isbn = pick_preferred_isbn([str(value) for value in isbns])
        if not chosen_isbn:
            continue

        score = score_candidate(title, author, doc)
        if score > best_score:
            best_score = score
            best_doc = doc

    if not best_doc or best_score < min_confidence:
        return None

    chosen_isbn = pick_preferred_isbn([str(value) for value in best_doc.get("isbn", [])])
    if not chosen_isbn:
        return None

    authors = best_doc.get("author_name", []) or []
    matched_author = str(authors[0]) if authors else ""

    return MatchResult(
        isbn=chosen_isbn,
        matched_title=str(best_doc.get("title", "")),
        matched_author=matched_author,
        confidence=round(best_score, 4),
    )


def enrich_csv(
    input_csv: Path,
    output_csv: Path,
    min_confidence: float,
    request_delay: float,
    max_rows: int | None,
) -> tuple[int, int]:
    docs_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}

    with input_csv.open("r", encoding="utf-8", newline="") as in_file, output_csv.open(
        "w", encoding="utf-8", newline=""
    ) as out_file:
        reader = csv.DictReader(in_file)
        fieldnames = list(reader.fieldnames or [])
        if "title" not in fieldnames:
            raise ValueError("Input CSV requires a 'title' column.")
        if "author" not in fieldnames:
            fieldnames.append("author")

        output_fields = fieldnames + ["isbn", "matched_title", "matched_author", "confidence", "source"]
        writer = csv.DictWriter(out_file, fieldnames=output_fields)
        writer.writeheader()

        total = 0
        found = 0
        for row in reader:
            if max_rows is not None and total >= max_rows:
                break

            total += 1
            title = (row.get("title") or "").strip()
            author = (row.get("author") or "").strip()

            enriched = dict(row)
            enriched.setdefault("author", author)
            enriched.update(
                {
                    "isbn": "",
                    "matched_title": "",
                    "matched_author": "",
                    "confidence": "",
                    "source": "",
                }
            )

            if title:
                try:
                    match = find_best_match(
                        title=title,
                        author=author,
                        min_confidence=min_confidence,
                        docs_cache=docs_cache,
                        request_delay=request_delay,
                    )
                except Exception:
                    match = None

                if match:
                    found += 1
                    enriched["isbn"] = match.isbn
                    enriched["matched_title"] = match.matched_title
                    enriched["matched_author"] = match.matched_author
                    enriched["confidence"] = f"{match.confidence:.4f}"
                    enriched["source"] = match.source

            writer.writerow(enriched)

    return total, found


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Find matching ISBNs for rows in a CSV with title/author columns."
    )
    parser.add_argument(
        "--in",
        dest="input_csv",
        default="data/books.csv",
        help="Input CSV path (default: data/books.csv)",
    )
    parser.add_argument(
        "--out",
        dest="output_csv",
        default="data/books_with_isbn.csv",
        help="Output CSV path (default: data/books_with_isbn.csv)",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.82,
        help="Minimum match confidence between 0 and 1 (default: 0.82)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.12,
        help="Delay between API requests in seconds (default: 0.12)",
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        default=None,
        help="Optional limit for processed rows (useful for testing)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    input_csv = Path(args.input_csv)
    output_csv = Path(args.output_csv)

    if not input_csv.exists():
        raise FileNotFoundError(f"Input file not found: {input_csv}")

    output_csv.parent.mkdir(parents=True, exist_ok=True)

    total, found = enrich_csv(
        input_csv=input_csv,
        output_csv=output_csv,
        min_confidence=args.min_confidence,
        request_delay=max(args.delay, 0.0),
        max_rows=args.max_rows,
    )

    print(f"Processed {total} rows")
    print(f"ISBN found for {found} rows")
    print(f"Output written to {output_csv}")


if __name__ == "__main__":
    main()