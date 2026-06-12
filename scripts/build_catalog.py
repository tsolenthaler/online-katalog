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


def parse_pdf_rows(pdf_path: Path) -> list[Entry]:
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    reader = PdfReader(str(pdf_path))
    text = "\n".join((page.extract_text() or "") for page in reader.pages)
    raw_lines = [clean_text(line) for line in text.splitlines()]
    lines = [line for line in raw_lines if line and len(line) > 2]

    entries: list[Entry] = []
    seen: set[tuple[str, str]] = set()

    for line in lines:
        # Preferred format: "title, author"
        if "," in line:
            left, right = line.rsplit(",", 1)
            title = clean_text(left)
            author = clean_text(right)
        elif " - " in line:
            left, right = line.rsplit(" - ", 1)
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

            if not title:
                continue

            key = (normalize_text(title), normalize_text(author))
            overrides[key] = {
                "isbn": isbn,
                "type": media_type,
                "owner": owner,
                "date_added": date_added,
            }
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
            key = row.get("key", "")
            if not key:
                continue
            cache[key] = {
                "isbn": normalize_isbn(row.get("isbn", "")),
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


def build_catalog(args: argparse.Namespace) -> dict[str, Any]:
    pdf_entries = parse_pdf_rows(Path(args.pdf))
    if args.max_rows:
        pdf_entries = pdf_entries[: args.max_rows]

    manual_path = Path(args.manual)
    if not manual_path.exists():
        fallback = Path("archiv/manual_catalog_overrides.csv")
        if fallback.exists():
            manual_path = fallback
    overrides = load_manual_overrides(manual_path)

    metadata_cache = load_json(Path(args.cache), default={})
    isbn_cache = load_isbn_cache(Path(args.isbn_cache))

    session = requests.Session()
    session.headers.update({"User-Agent": args.user_agent})

    now = dt.datetime.now(dt.UTC)
    cutoff = now.date() - dt.timedelta(days=args.days_new)

    items: list[dict[str, Any]] = []
    stats = {
        "rows_with_isbn": 0,
        "rows_without_isbn": 0,
        "rows_with_metadata": 0,
    }

    for source in pdf_entries:
        key = f"{normalize_text(source.title)}|{normalize_text(source.author)}"
        override = overrides.get((normalize_text(source.title), normalize_text(source.author)), {})

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
                isbn = dnb_search_isbn(session, source.title, source.author)
                if isbn:
                    isbn_source = "dnb"
            except requests.RequestException:
                isbn = ""

        if not isbn and not args.offline:
            try:
                isbn = google_isbn_search(session, source.title, source.author)
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

        title = metadata.get("title") or source.title
        author = metadata.get("author") or source.author
        genres = [clean_text(g) for g in (metadata.get("genres") or []) if clean_text(g)]
        genre = genres[0] if genres else ""
        description = metadata.get("description") or ""
        cover_url = metadata.get("cover_url") or DEFAULT_PLACEHOLDER_COVER

        if isbn and (description or genres or metadata.get("cover_url")):
            stats["rows_with_metadata"] += 1

        item_id = isbn or source.row_id
        item = {
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
            "status": "OK" if isbn else "Keine ISBN ermittelt",
            "openlibrary_link": metadata.get("openlibrary_link", f"https://openlibrary.org/isbn/{isbn}" if isbn else ""),
            "google_books_link": metadata.get("google_books_link", ""),
            "dnb_link": metadata.get("dnb_link", f"https://portal.dnb.de/opac/simpleSearch?query={isbn}" if isbn else ""),
        }
        item["search_text"] = entry_search_text(item)
        items.append(item)

    items.sort(key=lambda x: (normalize_text(x.get("title", "")), normalize_text(x.get("author", ""))))

    output = {
        "generated_at": now.isoformat(),
        "source_pdf": args.pdf,
        "manual_overrides": str(manual_path),
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
    parser.add_argument("--manual", default="data/manual_overrides.csv", help="Manual overrides CSV")
    parser.add_argument("--out", default="data/catalog.json", help="Output catalog JSON")
    parser.add_argument("--cache", default="data/catalog_metadata_cache.json", help="Metadata cache JSON")
    parser.add_argument("--isbn-cache", default="data/isbn_cache.csv", help="ISBN lookup cache CSV")
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
