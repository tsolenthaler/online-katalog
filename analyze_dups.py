#!/usr/bin/env python3
import csv
from pathlib import Path
from collections import defaultdict

# Zähle Duplikate in archiv/books.csv
titles_authors = defaultdict(int)
with open('archiv/books.csv', encoding='utf-8-sig', newline='') as f:
    for i, row in enumerate(csv.DictReader(f)):
        title = (row.get('title') or row.get('Titel') or row.get('titel') or '').strip()
        author = (row.get('author') or row.get('Verfasser') or row.get('autor') or '').strip()
        if title:
            key = f"{title}|{author}"
            titles_authors[key] += 1

# Zähle wie viele Duplikate
all_count = sum(titles_authors.values())
unique_count = len(titles_authors)
duplicate_count = sum(c - 1 for c in titles_authors.values() if c > 1)

print(f"Gesamt CSV-Zeilen: {all_count}")
print(f"Unique (title,author): {unique_count}")
print(f"Duplikate gesamt: {duplicate_count}")
print(f"Eintraege mit >1 Vorkommen: {len([c for c in titles_authors.values() if c > 1])}")

# Top-Duplikate
top_dups = sorted(titles_authors.items(), key=lambda x: -x[1])[:10]
print("\nTop 10 Duplikate:")
for key, count in top_dups:
    if count > 1:
        print(f"  {count}x: {key[:60]}")
