#!/usr/bin/env python3
"""
Diagnostic: Compare archive cache keys with generated keys to find mismatches.
"""
import csv
import re
import unicodedata
from collections import defaultdict

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

# Load archive cache
archiv_cache = {}
with open('archiv/isbn_cache.csv', encoding='utf-8-sig', newline='') as f:
    for row in csv.DictReader(f):
        cache_key = row.get('cache_key', '')
        isbn = row.get('isbn', '')
        if cache_key and isbn:
            archiv_cache[cache_key] = isbn

print(f"Archive cache keys loaded: {len(archiv_cache)}")

# Load books.csv and generate keys
generated_keys = defaultdict(list)
with open('archiv/books.csv', encoding='utf-8-sig', newline='') as f:
    for i, row in enumerate(csv.DictReader(f)):
        title = clean_text(row.get('title') or row.get('Titel') or '')
        author = clean_text(row.get('author') or row.get('Verfasser') or '')
        if title:
            key = f"{normalize_text(title)}|{normalize_text(author)}"
            generated_keys[key].append((title, author, i))

print(f"Generated unique keys: {len(generated_keys)}")

# Find matches
matches = 0
mismatches = []
for archiv_key in archiv_cache:
    if archiv_key in generated_keys:
        matches += 1
    else:
        mismatches.append(archiv_key)

print(f"\nMatches: {matches}/{len(archiv_cache)}")
print(f"Mismatches: {len(mismatches)}")

if mismatches:
    print("\nFirst 10 mismatches:")
    for i, key in enumerate(mismatches[:10]):
        isbn = archiv_cache[key]
        title, author = key.split('|', 1) if '|' in key else (key, '')
        print(f"  {i+1}. ISBN {isbn}: '{title}' | '{author}'")
        # Try to find similar keys in generated_keys
        similar = [k for k in generated_keys if title in k or author in k][:2]
        if similar:
            print(f"     Similar in CSV: {similar[0]}")

# Check data/isbn_cache.csv (current)
print("\n\nCurrent data/isbn_cache.csv:")
current_cache = {}
with open('data/isbn_cache.csv', encoding='utf-8-sig', newline='') as f:
    for row in csv.DictReader(f):
        key = row.get('key', '')
        isbn = row.get('isbn', '')
        if key and isbn:
            current_cache[key] = isbn

print(f"Current cache entries: {len(current_cache)}")
current_matches = sum(1 for k in current_cache if k in generated_keys)
print(f"Current matches with CSV: {current_matches}/{len(current_cache)}")
