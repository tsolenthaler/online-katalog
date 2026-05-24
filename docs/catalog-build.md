# Catalog Build Script

Diese Datei beschreibt, wie aus `data/books_with_isbn.csv` automatisch Metadaten geladen und eine suchfertige `catalog.json` erzeugt wird.

## Ziel

Das Script laedt pro ISBN:

- `cover_url`
- `description`
- `genre` / `genres`

und schreibt eine JSON-Datei fuer die Websuche.

## Script-Dateien

- `build_catalog.py` (Wrapper im Projekt-Root)
- `src/command/build_catalog.py` (eigentliche Implementierung)

## Standard-Aufruf

Aus dem Projekt-Root:

```bash
python3 build_catalog.py --in data/books_with_isbn.csv --out data/catalog.json
```

## Wichtige Parameter

- `--in <pfad>`: Eingabe-CSV (default: `data/books_with_isbn.csv`)
- `--out <pfad>`: Ausgabe-JSON (default: `data/catalog.json`)
- `--cache <pfad>`: Metadaten-Cache als JSON (default: `data/catalog_metadata_cache.json`)
- `--delay <sekunden>`: Wartezeit zwischen API-Requests (default: `0.15`)
- `--max-rows <n>`: Nur erste `n` Zeilen verarbeiten (Testzwecke)
- `--progress-every <n>`: Cache/Fortschritt alle `n` Zeilen schreiben (default: `50`)
- `--quiet`: Lauf ohne Live-Ausgabe

## Datenquellen

Das Script verwendet folgende Reihenfolge:

1. OpenLibrary (primaere Quelle)
2. Google Books (Fallback fuer fehlendes Cover oder fehlende Beschreibung)

## Ausgabeformat

Die Datei `catalog.json` enthaelt Metadaten und eine Item-Liste:

- `generated_at`
- `source_csv`
- `total_rows`
- `rows_with_metadata`
- `rows_without_isbn`
- `items` (Array)

Jedes Item enthaelt u. a.:

- `id`
- `title`
- `author`
- `isbn`
- `cover_url`
- `description`
- `genres`
- `genre` (erstes Genre fuer einfache Filter)
- `metadata_source`
- `search_text` (normalisierter Suchtext fuer Client-Suche)

## Beispiel-Testlauf

```bash
python3 build_catalog.py --max-rows 25 --out data/catalog_sample.json --progress-every 5
```

## Empfehlung fuer Produktivlauf

```bash
python3 build_catalog.py --in data/books_with_isbn.csv --out data/catalog.json --delay 0.15 --progress-every 25
```