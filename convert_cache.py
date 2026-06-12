#!/usr/bin/env python3
import csv
from pathlib import Path

# Lese archiv/isbn_cache.csv und konvertiere Keys
with open('archiv/isbn_cache.csv', encoding='utf-8-sig', newline='') as f:
    archiv_rows = []
    for row in csv.DictReader(f):
        cache_key = row.get('cache_key', '')
        isbn = row.get('isbn', '')
        if cache_key and isbn:
            # cache_key ist bereits "title|author" in normalisierter Form
            archiv_rows.append((cache_key, isbn, row.get('source', 'dnb')))

print(f'Konvertiere {len(archiv_rows)} Archiv-Eintraege mit ISBN...')

# Schreibe in neues Format
output = Path('data/isbn_cache.csv')
with output.open('w', encoding='utf-8', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=['key', 'isbn', 'source'])
    writer.writeheader()
    for key, isbn, source in sorted(archiv_rows):
        writer.writerow({'key': key, 'isbn': isbn, 'source': source})

print(f'Geschrieben: {len(archiv_rows)} Eintraege in {output}')
