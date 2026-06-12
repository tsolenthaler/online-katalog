"""Shared utilities for the Bibliothek Stein AR catalog pipeline."""

from __future__ import annotations

import csv
import datetime as dt
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


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# ISBN helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------


def parse_iso_date(value: str) -> dt.date | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return dt.datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    return None


# ---------------------------------------------------------------------------
# JSON / CSV I/O
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Source parsers
# ---------------------------------------------------------------------------


@dataclass
class Entry:
    row_id: str
    title: str
    author: str
    media_type: str = "Buch"
    owner: str = ""
    date_added: str = ""


def parse_csv_rows(csv_path: Path) -> list[Entry]:
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
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    reader = PdfReader(str(pdf_path))
    full_text = " ".join((page.extract_text() or "") for page in reader.pages)

    segments = re.split(r"\s+\d{5,6}(?=[A-ZÄÖÜ])", full_text)

    entries: list[Entry] = []
    seen: set[tuple[str, str]] = set()

    for segment in segments:
        segment = clean_text(segment)
        if not segment or len(segment) < 4:
            continue

        match = re.search(
            r"^(.+?)\s+([A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)*,\s*[A-ZÄÖÜ][a-zäöüß].*?)$",
            segment,
        )
        if match:
            title = clean_text(match.group(1))
            author_raw = clean_text(match.group(2))
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


# ---------------------------------------------------------------------------
# Manual overrides
# ---------------------------------------------------------------------------

OVERRIDE_FIELDS = {
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


# ---------------------------------------------------------------------------
# External API helpers
# ---------------------------------------------------------------------------


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

    return {
        "title": title,
        "author": ", ".join(author_names),
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
