#!/usr/bin/env python3
"""Export titles from Titelliste.pdf into a CSV file."""

import argparse
import csv
import re
from collections import defaultdict

import fitz


def normalize_author(author: str) -> str:
    """Normalize author names like 'Lastname, Firstname' to 'Firstname Lastname'."""
    author = re.sub(r"\s+", " ", author).strip()
    if "," not in author:
        return author

    parts = [part.strip() for part in author.split(",") if part.strip()]
    if len(parts) == 2:
        return f"{parts[1]} {parts[0]}".strip()
    return author


def extract_books(pdf_path: str) -> list[dict[str, str]]:
    doc = fitz.open(pdf_path)
    books: list[dict[str, str]] = []

    for page in doc:
        words = page.get_text("words")
        blocks: dict[int, list[tuple]] = defaultdict(list)

        for word in words:
            x0, y0, x1, y1, text, block_no, line_no, word_no = word
            _ = (x1, y1, line_no, word_no)
            if y0 < 100 or y0 > 790:
                continue
            blocks[block_no].append(word)

        for block_no in sorted(blocks.keys()):
            block_words = blocks[block_no]
            lines: dict[int, list[tuple]] = defaultdict(list)

            for word in block_words:
                lines[word[6]].append(word)

            number_lines = set()
            for line_no, line_words in lines.items():
                for word in line_words:
                    x0, y0, x1, y1, text, _, _, _ = word
                    _ = (y0, x1, y1)
                    if x0 > 470 and re.fullmatch(r"\d{5,7}", text):
                        number_lines.add(line_no)

            if not number_lines:
                continue

            candidate_lines = [line_no for line_no in sorted(lines.keys()) if line_no not in number_lines]
            if not candidate_lines:
                continue

            title_words: list[str] = []
            author_words: list[str] = []
            for line_no in candidate_lines:
                line_words = sorted(lines[line_no], key=lambda item: item[0])
                for word in line_words:
                    x0, y0, x1, y1, text, _, _, _ = word
                    _ = (y0, x1, y1)
                    if x0 < 300:
                        title_words.append(text)
                    elif x0 < 470:
                        author_words.append(text)

            title = " ".join(title_words)
            title = re.sub(r"\s+", " ", title).strip()
            author = " ".join(author_words)
            author = re.sub(r"\s+", " ", author).strip()
            author = normalize_author(author)

            if not title or title.startswith("©") or title in {"Titel", "Titel-Liste"}:
                continue

            books.append({"title": title, "author": author})

    return books


def write_csv(output_path: str, books: list[dict[str, str]]) -> None:
    with open(output_path, "w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(["title", "author"])
        for book in books:
            writer.writerow([book["title"], book["author"]])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export titles from a PDF list into CSV.")
    parser.add_argument(
        "--pdf",
        default="data/Titelliste.pdf",
        help="Path to input PDF (default: data/Titelliste.pdf)",
    )
    parser.add_argument(
        "--out",
        default="data/books.csv",
        help="Path to output CSV (default: data/books.csv)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    books = extract_books(args.pdf)
    write_csv(args.out, books)
    print(f"Exported {len(books)} entries to {args.out}")


if __name__ == "__main__":
    main()
