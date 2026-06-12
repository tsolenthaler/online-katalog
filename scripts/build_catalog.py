#!/usr/bin/env python3
"""Build a static catalog JSON for Bibliothek Stein AR.

Pipeline:
1. Parse source PDF into title/author rows
2. Apply manual ISBN overrides
3. Resolve ISBNs when possible
4. Fetch metadata from Open Library, Google Books, and DNB by ISBN
5. Write a static catalog JSON consumed by the website
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import requests
from pypdf import PdfReader


DEFAULT_PLACEHOLDER_COVER = "assets/placeholder-cover.svg"
REPORT_OVERRIDE_FIELDS = {
    "title",
    "author",
    "isbn",
    "type",
    "owner",
    "date_added",
    "description",
    "cover_url",
    "genre",
    "status",
}

ITEM_FIELDS = {
    "id",
    "title",
    "author",
    "isbn",
    "type",
    "owner",
    "date_added",
    "is_new",
    "cover_url",
    "description",
    "genres",
    "genre",
    "metadata_source",
    "isbn_source",
    "status",
    "openlibrary_link",
    "google_books_link",
    "dnb_link",
    "search_text",
}


@dataclass
class Entry:
    row_id: str
    title: str
    author: str
    media_type: str = "Buch"
    owner: str = ""
    date_added: str = ""


def normalize_text(value: str) -> str:
    value = value or ""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value


def clean_text(value: str) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def isbn_digits(value: str) -> str:
    return re.sub(r"[^0-9Xx]", "", value or "")


def valid_isbn10(value: str) -> bool:
    v = isbn_digits(value)
    if len(v) != 10:
        return False
    total = 0
    for idx, ch in enumerate(v):
        n = 10 if ch in {"X", "x"} else int(ch)
        total += (10 - idx) * n
    return total % 11 == 0


def valid_isbn13(value: str) -> bool:
    v = isbn_digits(value)
    if len(v) != 13 or not v.isdigit():
        return False
    total = 0
    for idx, ch in enumerate(v[:12]):
        total += int(ch) * (1 if idx % 2 == 0 else 3)
    checksum = (10 - (total % 10)) % 10
    return checksum == int(v[-1])


def normalize_isbn(value: str) -> str:
    v = isbn_digits(value)
    if valid_isbn13(v):
        return v
    if valid_isbn10(v):
        # Convert ISBN-10 -> ISBN-13
        base = "978" + v[:-1]
        total = 0
        for idx, ch in enumerate(base):
            total += int(ch) * (1 if idx % 2 == 0 else 3)
        checksum = (10 - (total % 10)) % 10
        return f"{base}{checksum}"
    return ""


def extract_isbn(text: str) -> str:
    if not text:
        return ""
    patterns = [
        r"(?:ISBN(?:-1[03])?:?\s*)([0-9Xx\- ]{10,20})",
        r"\b(97[89][0-9\- ]{10,17})\b",
        r"\b([0-9Xx\- ]{10,20})\b",
    ]
    for pat in patterns:
        for match in re.findall(pat, text):
            isbn = normalize_isbn(match)
            if isbn:
                return isbn
    return ""


def parse_csv_rows(csv_path: Path) -> list[Entry]:
    """Parse a CSV file with at minimum 'title' and 'author' columns."""
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    entries: list[Entry] = []
    seen: set[tuple[str, str]] = set()

    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            title = clean_text(row.get("title", "") or row.get("Titel", "") or row.get("titel", ""))
            author = clean_text(row.get("author", "") or row.get("Verfasser", "") or row.get("autor", ""))
            media_type = clean_text(row.get("type", "") or row.get("typ", "")) or "Buch"
            owner = clean_text(row.get("owner", "") or row.get("besitzer", ""))
            date_added = clean_text(row.get("date_added", "") or row.get("datum", ""))

            if not title:
                continue

            key = (normalize_text(title), normalize_text(author))
            if key in seen:
                continue
            seen.add(key)

            entries.append(
                Entry(
                    row_id=f"row-{len(entries) + 1}",
                    title=title,
                    author=author,
                    media_type=media_type,
                    owner=owner,
                    date_added=date_added,
                )
            )

    return entries


def parse_pdf_rows(pdf_path: Path) -> list[Entry]:
    """Parse title/author rows from PDF.

    The PDF is expected to contain a table where each entry occupies a line in the form:
        NNNNN<title><Lastname, Firstname>
    or simply
        <title>,<author>

    Because pdfminer/pypdf may merge many rows into a single text block, we apply a
    best-effort heuristic: look for the 5-to-6-digit acquisition-number pattern that
    precedes each new entry and split on that.
    """
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    reader = PdfReader(str(pdf_path))
    full_text = " ".join((page.extract_text() or "") for page in reader.pages)

    # Try to split on acquisition-number boundaries (5-6 digits that start a new entry).
    # Pattern seen in the actual PDF: "<number><title><Lastname, Firstname>"
    segments = re.split(r"\s+\d{5,6}(?=[A-ZÄÖÜ])", full_text)

    entries: list[Entry] = []
    seen: set[tuple[str, str]] = set()

    for segment in segments:
        segment = clean_text(segment)
        if not segment or len(segment) < 4:
            continue

        # Author in the Bibliothek list is "Lastname, Firstname" — the LAST comma group.
        # Remaining text before it is the title.
        # We look for a comma not immediately preceded by a digit (avoid "Band 001,")
        match = re.search(r"^(.+?)\s+([A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)*,\s*[A-ZÄÖÜ][a-zäöüß].*?)$", segment)
        if match:
            title = clean_text(match.group(1))
            author_raw = clean_text(match.group(2))
            # Convert "Lastname, Firstname" → "Firstname Lastname" for consistency with books.csv.
            if "," in author_raw:
                parts = [p.strip() for p in author_raw.split(",", 1)]
                author = f"{parts[1]} {parts[0]}"
            else:
                author = author_raw
        elif "," in segment:
            left, right = segment.rsplit(",", 1)
            title = clean_text(left)
            author = clean_text(right)
        else:
            continue

        if len(title) < 2:
            continue

        key = (normalize_text(title), normalize_text(author))
        if key in seen:
            continue
        seen.add(key)

        entries.append(
            Entry(
                row_id=f"row-{len(entries) + 1}",
                title=title,
                author=author,
            )
        )

    return entries


def load_source_rows(args: argparse.Namespace) -> list[Entry]:
    """Load entries from CSV (preferred) or fall back to PDF."""
    # Explicit CSV flag takes precedence.
    if args.csv:
        csv_path = Path(args.csv)
        if csv_path.exists():
            return parse_csv_rows(csv_path)
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    pdf_path = Path(args.pdf)

    # Prefer known CSV sources before falling back to PDF parsing.
    candidate_csvs = [
        pdf_path.with_suffix(".csv"),
        Path("archiv/books.csv"),
    ]
    for candidate_csv in candidate_csvs:
        if candidate_csv.exists():
            return parse_csv_rows(candidate_csv)

    # Fallback: parse the PDF.
    return parse_pdf_rows(pdf_path)


def load_manual_overrides(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    overrides: dict[tuple[str, str], dict[str, str]] = {}
    if not path.exists():
        return overrides

    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            title = clean_text(row.get("title", ""))
            author = clean_text(row.get("author", ""))
            isbn = normalize_isbn(row.get("isbn", ""))
            media_type = clean_text(row.get("type", "Buch")) or "Buch"
            owner = clean_text(row.get("owner", ""))
            date_added = clean_text(row.get("date_added", ""))
            description = clean_text(row.get("description", ""))
            cover_url = clean_text(row.get("cover_url", ""))
            genre = clean_text(row.get("genre", ""))
            status = clean_text(row.get("status", ""))

            if not title:
                continue

            key = (normalize_text(title), normalize_text(author))
            overrides[key] = {
                "isbn": isbn,
                "title": title,
                "author": author,
                "type": media_type,
                "owner": owner,
                "date_added": date_added,
                "description": description,
                "cover_url": cover_url,
                "genre": genre,
                "status": status,
            }
    return overrides


def merge_entry_override(
    target: dict[tuple[str, str], dict[str, str]],
    key: tuple[str, str],
    values: dict[str, str],
) -> None:
    cleaned = {
        field: clean_text(value)
        for field, value in values.items()
        if field in REPORT_OVERRIDE_FIELDS and clean_text(value)
    }
    if not cleaned:
        return
    target.setdefault(key, {}).update(cleaned)


def apply_report_change(target: dict[str, str], field_name: str, proposed_value: str) -> None:
    normalized_field = clean_text(field_name).lower()
    if not normalized_field:
        return

    aliases = {
        "titel": "title",
        "autor": "author",
        "beschreibung": "description",
        "cover": "cover_url",
        "cover-url": "cover_url",
        "cover_url": "cover_url",
        "genre": "genre",
        "typ": "type",
        "besitzer": "owner",
        "status": "status",
        "isbn": "isbn",
    }
    canonical = aliases.get(normalized_field, normalized_field)
    if canonical == "isbn":
        normalized_isbn = normalize_isbn(proposed_value)
        if normalized_isbn:
            target[canonical] = normalized_isbn
        return
    if canonical in REPORT_OVERRIDE_FIELDS:
        target[canonical] = clean_text(proposed_value)


def load_report_overrides(reports_dir: Path) -> dict[tuple[str, str], dict[str, str]]:
    overrides: dict[tuple[str, str], dict[str, str]] = {}
    if not reports_dir.exists():
        return overrides

    for path in sorted(reports_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in {".json", ".csv"}:
            continue

        if path.suffix.lower() == ".json":
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)

            title = clean_text(payload.get("title", ""))
            author = clean_text(payload.get("author", ""))
            if not title:
                continue

            values: dict[str, str] = {}
            if payload.get("isbn"):
                apply_report_change(values, "isbn", str(payload.get("isbn", "")))
            for change in payload.get("changes", []):
                apply_report_change(values, str(change.get("field_name", "")), str(change.get("proposed_value", "")))
            merge_entry_override(overrides, (normalize_text(title), normalize_text(author)), values)
            continue

        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            rows_by_key: dict[tuple[str, str], dict[str, str]] = {}
            for row in reader:
                title = clean_text(row.get("title", ""))
                author = clean_text(row.get("author", ""))
                if not title:
                    continue
                key = (normalize_text(title), normalize_text(author))
                current = rows_by_key.setdefault(key, {})
                if row.get("isbn"):
                    apply_report_change(current, "isbn", row.get("isbn", ""))
                apply_report_change(current, row.get("field_name", ""), row.get("proposed_value", ""))

            for key, values in rows_by_key.items():
                merge_entry_override(overrides, key, values)

    return overrides


def parse_iso_date(value: str) -> dt.date | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return dt.datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    return None


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_isbn_cache(path: Path) -> dict[str, dict[str, str]]:
    cache: dict[str, dict[str, str]] = {}
    if not path.exists():
        return cache
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Support both our own format ("key") and the archiv format ("cache_key").
            key = row.get("key") or row.get("cache_key", "")
            if not key:
                continue
            isbn_raw = row.get("isbn", "")
            if not isbn_raw:
                continue
            isbn = normalize_isbn(isbn_raw)
            if not isbn:
                continue
            cache[key] = {
                "isbn": isbn,
                "source": row.get("source", ""),
            }
    return cache


def save_isbn_cache(path: Path, cache: dict[str, dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["key", "isbn", "source"])
        writer.writeheader()
        for key, value in sorted(cache.items()):
            writer.writerow({"key": key, **value})


def dnb_search_isbn(session: requests.Session, title: str, author: str) -> str:
    query = f'tit="{title}" and per="{author}"' if author else f'tit="{title}"'
    params = {
        "version": "1.1",
        "operation": "searchRetrieve",
        "recordSchema": "mods",
        "maximumRecords": "3",
        "query": query,
    }
    resp = session.get("https://services.dnb.de/sru/dnb", params=params, timeout=20)
    if resp.status_code != 200:
        return ""

    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError:
        return ""

    # Parse any ISBN-ish values from the XML payload.
    text_blob = " ".join(root.itertext())
    return extract_isbn(text_blob)


def dnb_metadata_by_isbn(session: requests.Session, isbn: str) -> dict[str, Any]:
    params = {
        "version": "1.1",
        "operation": "searchRetrieve",
        "recordSchema": "mods",
        "maximumRecords": "1",
        "query": f'isbn="{isbn}"',
    }
    resp = session.get("https://services.dnb.de/sru/dnb", params=params, timeout=20)
    if resp.status_code != 200:
        return {}

    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError:
        return {}

    blob = " ".join(clean_text(t) for t in root.itertext() if clean_text(t))
    if not blob:
        return {}
    return {
        "title": "",
        "author": "",
        "description": "",
        "genres": [],
        "cover_url": "",
        "source_link": f"https://portal.dnb.de/opac/simpleSearch?query={isbn}",
    }


def openlibrary_metadata_by_isbn(session: requests.Session, isbn: str) -> dict[str, Any]:
    resp = session.get(f"https://openlibrary.org/isbn/{isbn}.json", timeout=20)
    if resp.status_code != 200:
        return {}

    data = resp.json()
    subjects = [clean_text(s) for s in data.get("subjects", []) if clean_text(s)]

    description = ""
    desc = data.get("description")
    if isinstance(desc, str):
        description = clean_text(desc)
    elif isinstance(desc, dict):
        description = clean_text(desc.get("value", ""))

    title = clean_text(data.get("title", ""))

    author_names: list[str] = []
    for author_ref in data.get("authors", []):
        key = author_ref.get("key")
        if not key:
            continue
        ar = session.get(f"https://openlibrary.org{key}.json", timeout=20)
        if ar.status_code == 200:
            name = clean_text(ar.json().get("name", ""))
            if name:
                author_names.append(name)

    author = ", ".join(author_names)

    return {
        "title": title,
        "author": author,
        "description": description,
        "genres": subjects,
        "cover_url": f"https://covers.openlibrary.org/b/isbn/{isbn}-L.jpg",
        "source_link": f"https://openlibrary.org/isbn/{isbn}",
    }


def google_isbn_search(session: requests.Session, title: str, author: str) -> str:
    q = f'intitle:"{title}" inauthor:"{author}"'
    resp = session.get(
        "https://www.googleapis.com/books/v1/volumes",
        params={"q": q, "maxResults": 5},
        timeout=20,
    )
    if resp.status_code != 200:
        return ""

    for item in resp.json().get("items", []):
        ids = item.get("volumeInfo", {}).get("industryIdentifiers", [])
        for id_obj in ids:
            isbn = normalize_isbn(id_obj.get("identifier", ""))
            if isbn:
                return isbn
    return ""


def google_metadata_by_isbn(session: requests.Session, isbn: str) -> dict[str, Any]:
    resp = session.get(
        "https://www.googleapis.com/books/v1/volumes",
        params={"q": f"isbn:{isbn}", "maxResults": 1},
        timeout=20,
    )
    if resp.status_code != 200:
        return {}

    items = resp.json().get("items", [])
    if not items:
        return {}

    info = items[0].get("volumeInfo", {})
    title = clean_text(info.get("title", ""))
    authors = [clean_text(a) for a in info.get("authors", []) if clean_text(a)]
    categories = [clean_text(a) for a in info.get("categories", []) if clean_text(a)]
    description = clean_text(info.get("description", ""))
    img = info.get("imageLinks", {}) or {}

    return {
        "title": title,
        "author": ", ".join(authors),
        "description": description,
        "genres": categories,
        "cover_url": img.get("thumbnail", "") or img.get("smallThumbnail", ""),
        "source_link": info.get("infoLink", ""),
    }


def merge_metadata(base: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)

    for field in ("title", "author", "description", "cover_url"):
        if not merged.get(field) and fallback.get(field):
            merged[field] = fallback[field]

    base_genres = list(merged.get("genres") or [])
    fallback_genres = list(fallback.get("genres") or [])
    merged["genres"] = list(dict.fromkeys([*base_genres, *fallback_genres]))
    return merged


def entry_search_text(entry: dict[str, Any]) -> str:
    values = [
        entry.get("title", ""),
        entry.get("author", ""),
        entry.get("isbn", ""),
        entry.get("type", ""),
        entry.get("owner", ""),
        entry.get("genre", ""),
        " ".join(entry.get("genres", []) or []),
    ]
    return normalize_text(" ".join(values))


def make_item_id(title: str, author: str, media_type: str) -> str:
    stable_key = f"{normalize_text(title)}|{normalize_text(author)}|{normalize_text(media_type)}"
    digest = hashlib.sha1(stable_key.encode("utf-8")).hexdigest()[:12]

    slug_base = normalize_text(title) or "eintrag"
    slug = re.sub(r"[^a-z0-9]+", "-", slug_base).strip("-")[:32] or "eintrag"
    return f"item-{slug}-{digest}"


def sanitize_item_record(item: dict[str, Any], cutoff: dt.date) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for field in ITEM_FIELDS:
        if field in item:
            normalized[field] = item[field]

    normalized["id"] = clean_text(str(normalized.get("id", "") or ""))
    normalized["title"] = clean_text(str(normalized.get("title", "") or ""))
    normalized["author"] = clean_text(str(normalized.get("author", "") or ""))
    normalized["isbn"] = normalize_isbn(str(normalized.get("isbn", "") or ""))
    normalized["type"] = clean_text(str(normalized.get("type", "") or "")) or "Buch"
    normalized["owner"] = clean_text(str(normalized.get("owner", "") or ""))
    normalized["date_added"] = clean_text(str(normalized.get("date_added", "") or ""))
    normalized["cover_url"] = clean_text(str(normalized.get("cover_url", "") or "")) or DEFAULT_PLACEHOLDER_COVER
    normalized["description"] = clean_text(str(normalized.get("description", "") or ""))
    normalized["genre"] = clean_text(str(normalized.get("genre", "") or ""))
    normalized["metadata_source"] = clean_text(str(normalized.get("metadata_source", "") or ""))
    normalized["isbn_source"] = clean_text(str(normalized.get("isbn_source", "") or ""))
    normalized["status"] = clean_text(str(normalized.get("status", "") or ""))
    normalized["openlibrary_link"] = clean_text(str(normalized.get("openlibrary_link", "") or ""))
    normalized["google_books_link"] = clean_text(str(normalized.get("google_books_link", "") or ""))
    normalized["dnb_link"] = clean_text(str(normalized.get("dnb_link", "") or ""))

    raw_genres = normalized.get("genres")
    if isinstance(raw_genres, list):
        genres = [clean_text(str(g)) for g in raw_genres if clean_text(str(g))]
    elif isinstance(raw_genres, str):
        genres = [clean_text(part) for part in raw_genres.split(",") if clean_text(part)]
    else:
        genres = []
    normalized["genres"] = genres

    if not normalized["genre"] and genres:
        normalized["genre"] = genres[0]

    parsed_date = parse_iso_date(normalized["date_added"])
    normalized["is_new"] = bool(parsed_date and parsed_date >= cutoff)

    if not normalized["status"]:
        normalized["status"] = "OK" if normalized["isbn"] else "Keine ISBN ermittelt"

    if normalized["isbn"] and not normalized["openlibrary_link"]:
        normalized["openlibrary_link"] = f"https://openlibrary.org/isbn/{normalized['isbn']}"
    if normalized["isbn"] and not normalized["dnb_link"]:
        normalized["dnb_link"] = f"https://portal.dnb.de/opac/simpleSearch?query={normalized['isbn']}"

    normalized["search_text"] = entry_search_text(normalized)
    return normalized


def load_item_file(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Item file is not an object: {path}")
    return payload


def prune_item_dir(item_dir: Path, keep_ids: set[str]) -> None:
    for path in item_dir.glob("*.json"):
        if path.stem not in keep_ids:
            path.unlink()


def load_items_from_dir(item_dir: Path, cutoff: dt.date) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not item_dir.exists():
        return items

    for path in sorted(item_dir.glob("*.json")):
        try:
            raw_item = load_item_file(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue

        if not raw_item.get("id"):
            raw_item["id"] = path.stem

        item = sanitize_item_record(raw_item, cutoff)
        if not item["id"]:
            continue
        items.append(item)

    items.sort(key=lambda x: (normalize_text(x.get("title", "")), normalize_text(x.get("author", ""))))
    return items


def build_catalog(args: argparse.Namespace) -> dict[str, Any]:
    pdf_entries = load_source_rows(args)
    if args.max_rows:
        pdf_entries = pdf_entries[: args.max_rows]

    manual_path = Path(args.manual)
    if not manual_path.exists():
        fallback = Path("archiv/manual_catalog_overrides.csv")
        if fallback.exists():
            manual_path = fallback
    overrides = load_manual_overrides(manual_path)
    item_dir = Path(args.item_dir)
    item_dir.mkdir(parents=True, exist_ok=True)

    metadata_cache = load_json(Path(args.cache), default={})
    isbn_cache = load_isbn_cache(Path(args.isbn_cache))

    session = requests.Session()
    session.headers.update({"User-Agent": args.user_agent})

    now = dt.datetime.now(dt.UTC)
    cutoff = now.date() - dt.timedelta(days=args.days_new)

    managed_item_ids: set[str] = set()
    stats = {
        "rows_with_isbn": 0,
        "rows_without_isbn": 0,
        "rows_with_metadata": 0,
    }

    for source in pdf_entries:
        key = f"{normalize_text(source.title)}|{normalize_text(source.author)}"
        override = overrides.get((normalize_text(source.title), normalize_text(source.author)), {})
        source_title = override.get("title") or source.title
        source_author = override.get("author") or source.author

        isbn = ""
        isbn_source = ""

        if override.get("isbn"):
            isbn = override["isbn"]
            isbn_source = "manual_override"
        elif isbn_cache.get(key, {}).get("isbn"):
            isbn = isbn_cache[key]["isbn"]
            isbn_source = isbn_cache[key].get("source", "cache")
        else:
            isbn = extract_isbn(f"{source.title} {source.author}")
            if isbn:
                isbn_source = "pdf"

        if not isbn and not args.offline:
            try:
                isbn = dnb_search_isbn(session, source_title, source_author)
                if isbn:
                    isbn_source = "dnb"
            except requests.RequestException:
                isbn = ""

        if not isbn and not args.offline:
            try:
                isbn = google_isbn_search(session, source_title, source_author)
                if isbn:
                    isbn_source = "google_books"
            except requests.RequestException:
                isbn = ""

        if isbn:
            isbn_cache[key] = {"isbn": isbn, "source": isbn_source or "lookup"}
            stats["rows_with_isbn"] += 1
        else:
            stats["rows_without_isbn"] += 1

        media_type = override.get("type") or source.media_type
        owner = override.get("owner") or source.owner
        date_added = override.get("date_added") or source.date_added
        parsed_date = parse_iso_date(date_added)

        metadata: dict[str, Any] = {
            "title": "",
            "author": "",
            "description": "",
            "genres": [],
            "cover_url": "",
            "openlibrary_link": "",
            "google_books_link": "",
            "dnb_link": "",
        }
        metadata_source = ""

        if isbn:
            if isbn in metadata_cache:
                metadata = metadata_cache[isbn]
                metadata_source = metadata.get("metadata_source", "cache")
            elif not args.offline:
                ol = {}
                gb = {}
                dnb = {}

                try:
                    ol = openlibrary_metadata_by_isbn(session, isbn)
                except requests.RequestException:
                    ol = {}

                try:
                    gb = google_metadata_by_isbn(session, isbn)
                except requests.RequestException:
                    gb = {}

                try:
                    dnb = dnb_metadata_by_isbn(session, isbn)
                except requests.RequestException:
                    dnb = {}

                merged = merge_metadata(ol, gb)
                merged = merge_metadata(merged, dnb)
                metadata = {
                    "title": merged.get("title", ""),
                    "author": merged.get("author", ""),
                    "description": merged.get("description", ""),
                    "genres": merged.get("genres", []),
                    "cover_url": merged.get("cover_url", ""),
                    "openlibrary_link": ol.get("source_link", f"https://openlibrary.org/isbn/{isbn}"),
                    "google_books_link": gb.get("source_link", ""),
                    "dnb_link": dnb.get("source_link", f"https://portal.dnb.de/opac/simpleSearch?query={isbn}"),
                }
                if ol:
                    metadata_source = "openlibrary"
                elif gb:
                    metadata_source = "google_books"
                elif dnb:
                    metadata_source = "dnb"
                else:
                    metadata_source = ""

                metadata["metadata_source"] = metadata_source
                metadata_cache[isbn] = metadata

        title = override.get("title") or metadata.get("title") or source.title
        author = override.get("author") or metadata.get("author") or source.author
        genres = [clean_text(g) for g in (metadata.get("genres") or []) if clean_text(g)]
        genre = override.get("genre") or (genres[0] if genres else "")
        description = override.get("description") or metadata.get("description") or ""
        cover_url = override.get("cover_url") or metadata.get("cover_url") or DEFAULT_PLACEHOLDER_COVER

        if isbn and (description or genres or metadata.get("cover_url")):
            stats["rows_with_metadata"] += 1

        item_id = make_item_id(source.title, source.author, source.media_type)
        managed_item_ids.add(item_id)

        base_item = {
            "id": item_id,
            "title": title,
            "author": author,
            "isbn": isbn,
            "type": media_type,
            "owner": owner,
            "date_added": date_added,
            "is_new": bool(parsed_date and parsed_date >= cutoff),
            "cover_url": cover_url,
            "description": description,
            "genres": genres,
            "genre": genre,
            "metadata_source": metadata_source,
            "isbn_source": isbn_source,
            "status": override.get("status") or ("OK" if isbn else "Keine ISBN ermittelt"),
            "openlibrary_link": metadata.get("openlibrary_link", f"https://openlibrary.org/isbn/{isbn}" if isbn else ""),
            "google_books_link": metadata.get("google_books_link", ""),
            "dnb_link": metadata.get("dnb_link", f"https://portal.dnb.de/opac/simpleSearch?query={isbn}" if isbn else ""),
        }
        item_path = item_dir / f"{item_id}.json"

        if item_path.exists():
            try:
                existing_item = load_item_file(item_path)
            except (OSError, ValueError, json.JSONDecodeError):
                existing_item = {}
            merged_item = {**base_item, **existing_item}
            merged_item["id"] = item_id
        else:
            merged_item = base_item

        final_item = sanitize_item_record(merged_item, cutoff)
        save_json(item_path, final_item)

    if not args.keep_orphans:
        prune_item_dir(item_dir, managed_item_ids)

    items = load_items_from_dir(item_dir, cutoff)

    output = {
        "generated_at": now.isoformat(),
        "source_pdf": args.pdf,
        "manual_overrides": str(manual_path),
        "item_folder": str(item_dir),
        "total_rows": len(items),
        **stats,
        "items": items,
    }

    save_json(Path(args.out), output)
    save_json(Path(args.cache), metadata_cache)
    save_isbn_cache(Path(args.isbn_cache), isbn_cache)
    return output


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build static catalog JSON")
    parser.add_argument("--pdf", default="data/Titelliste.pdf", help="Input title list PDF")
    parser.add_argument("--csv", default="", help="Input titles CSV (title,author,...); overrides --pdf when given")
    parser.add_argument("--manual", default="data/manual_overrides.csv", help="Manual overrides CSV")
    parser.add_argument("--out", default="data/catalog.json", help="Output catalog JSON")
    parser.add_argument("--item-dir", default="data/item", help="Folder with one JSON file per item")
    parser.add_argument("--cache", default="data/catalog_metadata_cache.json", help="Metadata cache JSON")
    parser.add_argument("--isbn-cache", default="data/isbn_cache.csv", help="ISBN lookup cache CSV")
    parser.add_argument("--keep-orphans", action="store_true", help="Keep item JSON files not present in source rows")
    parser.add_argument("--days-new", type=int, default=90, help="Days considered new")
    parser.add_argument("--max-rows", type=int, default=0, help="Only process first N rows")
    parser.add_argument("--offline", action="store_true", help="Disable external API requests")
    parser.add_argument(
        "--user-agent",
        default="Bibliothek-Stein-AR-Katalog/1.0 (contact: github-pages)",
        help="Custom user agent for API calls",
    )
    return parser


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()
    result = build_catalog(args)
    print(
        f"Katalog erstellt: {args.out} | Eintraege: {result['total_rows']} | "
        f"mit ISBN: {result['rows_with_isbn']} | ohne ISBN: {result['rows_without_isbn']}"
    )


if __name__ == "__main__":
    main()
