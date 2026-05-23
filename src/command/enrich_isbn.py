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
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen


OPENLIBRARY_SEARCH_URL = "https://openlibrary.org/search.json"
DNB_SRU_URL = "https://services.dnb.de/sru/dnb"

SRU_NS = {"srw": "http://www.loc.gov/zing/srw/", "marc": "http://www.loc.gov/MARC21/slim"}


@dataclass
class MatchResult:
    isbn: str
    matched_title: str
    matched_author: str
    confidence: float
    source: str = "openlibrary"


def build_cache_key(title: str, author: str) -> str:
    return f"{normalize_text(title)}|{normalize_author_for_compare(author)}"


def load_isbn_cache(cache_csv: Path) -> dict[str, MatchResult]:
    cache: dict[str, MatchResult] = {}
    if not cache_csv.exists():
        return cache

    with cache_csv.open("r", encoding="utf-8", newline="") as cache_file:
        reader = csv.DictReader(cache_file)
        for row in reader:
            key = (row.get("cache_key") or "").strip()
            isbn = (row.get("isbn") or "").strip()
            if not key or not isbn:
                continue

            confidence_raw = (row.get("confidence") or "").strip()
            try:
                confidence = float(confidence_raw) if confidence_raw else 0.0
            except ValueError:
                confidence = 0.0

            cache[key] = MatchResult(
                isbn=isbn,
                matched_title=(row.get("matched_title") or "").strip(),
                matched_author=(row.get("matched_author") or "").strip(),
                confidence=confidence,
                source=(row.get("source") or "cache").strip() or "cache",
            )

    return cache


def append_isbn_cache(cache_csv: Path, cache_key: str, title: str, author: str, match: MatchResult) -> None:
    exists = cache_csv.exists()
    cache_csv.parent.mkdir(parents=True, exist_ok=True)

    with cache_csv.open("a", encoding="utf-8", newline="") as cache_file:
        fieldnames = [
            "cache_key",
            "title",
            "author",
            "isbn",
            "matched_title",
            "matched_author",
            "confidence",
            "source",
        ]
        writer = csv.DictWriter(cache_file, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()

        writer.writerow(
            {
                "cache_key": cache_key,
                "title": title,
                "author": author,
                "isbn": match.isbn,
                "matched_title": match.matched_title,
                "matched_author": match.matched_author,
                "confidence": f"{match.confidence:.4f}",
                "source": match.source,
            }
        )
        cache_file.flush()


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def clean_visible_text(value: str) -> str:
    value = "".join(ch for ch in value if ch.isprintable() or ch in "\t\n\r")
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


def normalize_title_for_query(title: str) -> str:
    # Remove common catalog noise so external APIs can match better.
    title = re.sub(r"\s*-\s*band\s*\d+\s*-\s*", " ", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*-\s*teil\s*\d+\s*-\s*", " ", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*\(.*?\)\s*", " ", title)
    title = re.sub(r"\s+", " ", title).strip(" -,:;")
    return title


def umlaut_variants(value: str) -> list[str]:
    if not value:
        return []

    variants = {value}
    replace_map = {
        "ä": "ae",
        "ö": "oe",
        "ü": "ue",
        "Ä": "Ae",
        "Ö": "Oe",
        "Ü": "Ue",
        "ß": "ss",
    }

    as_ae = value
    for src, dst in replace_map.items():
        as_ae = as_ae.replace(src, dst)
    variants.add(as_ae)

    as_plain = unicodedata.normalize("NFKD", value)
    as_plain = "".join(ch for ch in as_plain if not unicodedata.combining(ch))
    variants.add(as_plain)

    return [v.strip() for v in variants if v.strip()]


def build_query_variants(title: str, author: str) -> list[tuple[str, str]]:
    base_title = normalize_title_for_query(title)
    base_author = author.strip()

    variants: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for t in umlaut_variants(base_title):
        author_variants = umlaut_variants(base_author) if base_author else [""]
        for a in author_variants:
            key = (t, a)
            if key not in seen:
                seen.add(key)
                variants.append(key)

    if not variants:
        variants.append((title.strip(), author.strip()))

    return variants


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


def fetch_dnb_docs(title: str, author: str, limit: int = 10) -> list[dict[str, Any]]:
    query_text = " ".join(part for part in [title.strip(), author.strip()] if part).strip()
    if not query_text:
        return []

    params = {
        "version": "1.1",
        "operation": "searchRetrieve",
        "recordSchema": "MARC21-xml",
        "maximumRecords": str(limit),
        "query": query_text,
    }
    url = f"{DNB_SRU_URL}?{urlencode(params)}"

    with urlopen(url, timeout=20) as response:  # noqa: S310
        payload = response.read().decode("utf-8")

    root = ET.fromstring(payload)
    docs: list[dict[str, Any]] = []

    for record in root.findall(".//marc:record", SRU_NS):
        title_parts = [
            clean_visible_text(node.text or "")
            for node in record.findall("./marc:datafield[@tag='245']/marc:subfield", SRU_NS)
            if clean_visible_text(node.text or "")
        ]
        title_text = " ".join(title_parts)

        authors_primary = [
            clean_visible_text(node.text or "")
            for node in record.findall("./marc:datafield[@tag='100']/marc:subfield[@code='a']", SRU_NS)
            if clean_visible_text(node.text or "")
        ]
        authors_secondary = [
            clean_visible_text(node.text or "")
            for node in record.findall("./marc:datafield[@tag='700']/marc:subfield[@code='a']", SRU_NS)
            if clean_visible_text(node.text or "")
        ]
        authors = authors_primary + authors_secondary

        isbn_values = [
            clean_visible_text(node.text or "")
            for node in record.findall("./marc:datafield[@tag='020']/marc:subfield[@code='a']", SRU_NS)
            if clean_visible_text(node.text or "")
        ]

        docs.append(
            {
                "title": title_text,
                "author_name": authors,
                "isbn": isbn_values,
            }
        )

    return docs


def fetch_docs_from_source(source: str, title: str, author: str) -> list[dict[str, Any]]:
    if source == "openlibrary":
        return fetch_openlibrary_docs(title=title, author=author)
    if source == "dnb":
        return fetch_dnb_docs(title=title, author=author)
    return []


def find_best_match(
    title: str,
    author: str,
    min_confidence: float,
    docs_cache: dict[tuple[str, str, str, str], list[dict[str, Any]]],
    request_delay: float,
    sources: list[str],
) -> MatchResult | None:
    best_doc: dict[str, Any] | None = None
    best_source = ""
    best_score = -1.0

    for source in sources:
        for q_title, q_author in build_query_variants(title=title, author=author):
            cache_key = (source, q_title.strip(), q_author.strip(), title.strip())
            if cache_key not in docs_cache:
                docs_cache[cache_key] = fetch_docs_from_source(source=source, title=q_title, author=q_author)
                if request_delay > 0:
                    time.sleep(request_delay)

            docs = docs_cache[cache_key]
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
                    best_source = source

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
        source=best_source or "unknown",
    )


def enrich_csv(
    input_csv: Path,
    output_csv: Path,
    cache_csv: Path,
    min_confidence: float,
    request_delay: float,
    max_rows: int | None,
    sources: list[str],
    verbose: bool,
    progress_every: int,
) -> tuple[int, int]:
    docs_cache: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    isbn_cache = load_isbn_cache(cache_csv)
    cache_hits = 0

    if verbose:
        print(f"Reading input: {input_csv}", flush=True)
        print(f"Writing output: {output_csv}", flush=True)
        print(f"Using cache: {cache_csv}", flush=True)
        print(f"Loaded cache entries: {len(isbn_cache)}", flush=True)
        print(f"Using sources: {', '.join(sources)}", flush=True)

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

            if verbose and (total == 1 or total % max(progress_every, 1) == 0):
                preview = title if len(title) <= 70 else title[:67] + "..."
                print(f"[{total}] Processing: {preview}", flush=True)

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
                cache_key = build_cache_key(title, author)
                cached = isbn_cache.get(cache_key)
                if cached:
                    match = cached
                    cache_hits += 1
                    if verbose:
                        print(
                            f"  -> ISBN {match.isbn} from cache (source {match.source}, confidence {match.confidence:.4f})",
                            flush=True,
                        )
                else:
                    match = None

                try:
                    if match is None:
                        match = find_best_match(
                            title=title,
                            author=author,
                            min_confidence=min_confidence,
                            docs_cache=docs_cache,
                            request_delay=request_delay,
                            sources=sources,
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
                    if verbose and cached is None:
                        print(
                            f"  -> ISBN {match.isbn} via {match.source} (confidence {match.confidence:.4f})",
                            flush=True,
                        )

                    if cached is None:
                        isbn_cache[cache_key] = match
                        append_isbn_cache(cache_csv, cache_key, title, author, match)

            writer.writerow(enriched)
            out_file.flush()

        if verbose:
            print(f"Finished processing rows: {total}", flush=True)
            print(f"Matches found so far: {found}", flush=True)
            print(f"Cache hits: {cache_hits}", flush=True)

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
        "--cache",
        dest="cache_csv",
        default="data/isbn_cache.csv",
        help="CSV cache for found ISBNs (default: data/isbn_cache.csv)",
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
        "--sources",
        default="openlibrary,dnb",
        help="Comma separated lookup sources (default: openlibrary,dnb)",
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        default=None,
        help="Optional limit for processed rows (useful for testing)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Disable live progress output",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=50,
        help="Print progress every N rows (default: 50)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    input_csv = Path(args.input_csv)
    output_csv = Path(args.output_csv)
    cache_csv = Path(args.cache_csv)
    sources = [part.strip().lower() for part in args.sources.split(",") if part.strip()]

    valid_sources = {"openlibrary", "dnb"}
    invalid_sources = [source for source in sources if source not in valid_sources]
    if invalid_sources:
        raise ValueError(f"Unknown sources: {', '.join(invalid_sources)}")
    if not sources:
        raise ValueError("At least one source must be configured.")

    if not input_csv.exists():
        script_dir = Path(__file__).resolve().parent
        repo_root = script_dir.parent.parent

        fallbacks: list[Path] = []
        if not input_csv.is_absolute():
            fallbacks.append(repo_root / input_csv)
            # Common shorthand: --in books.csv -> data/books.csv
            if input_csv.parent == Path("."):
                fallbacks.append(repo_root / "data" / input_csv.name)

        resolved = next((candidate for candidate in fallbacks if candidate.exists()), None)
        if resolved is not None:
            input_csv = resolved
        else:
            searched = [str(input_csv)] + [str(path) for path in fallbacks]
            raise FileNotFoundError(
                "Input file not found. Checked: " + ", ".join(searched)
            )

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    cache_csv.parent.mkdir(parents=True, exist_ok=True)

    total, found = enrich_csv(
        input_csv=input_csv,
        output_csv=output_csv,
        cache_csv=cache_csv,
        min_confidence=args.min_confidence,
        request_delay=max(args.delay, 0.0),
        max_rows=args.max_rows,
        sources=sources,
        verbose=not args.quiet,
        progress_every=max(args.progress_every, 1),
    )

    print(f"Processed {total} rows")
    print(f"ISBN found for {found} rows")
    print(f"Output written to {output_csv}")


if __name__ == "__main__":
    main()