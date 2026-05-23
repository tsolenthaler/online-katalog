# ISBN Enrichment Script

Diese Datei beschreibt, wie das Script fuer die ISBN-Anreicherung verwendet wird.

## Ziel

Das Script liest eine CSV mit mindestens den Spalten `title` und optional `author`, sucht passende ISBNs in externen Katalogen und schreibt eine neue CSV mit Zusatzspalten.

## Script-Dateien

- `enrich_isbn.py` (Wrapper im Projekt-Root)
- `src/command/enrich_isbn.py` (eigentliche Implementierung)

## Standard-Aufruf

Aus dem Projekt-Root:

```bash
python3 enrich_isbn.py --in books.csv --out data/books_with_isbn.csv
```

Hinweis: Wenn `--in books.csv` verwendet wird und die Datei im Root nicht existiert, versucht das Script automatisch `data/books.csv`.

## Wichtige Parameter

- `--in <pfad>`: Eingabe-CSV (default: `data/books.csv`)
- `--out <pfad>`: Ausgabe-CSV (default: `data/books_with_isbn.csv`)
- `--min-confidence <0..1>`: Mindest-Score fuer Treffer (default: `0.82`)
- `--delay <sekunden>`: Wartezeit zwischen API-Requests (default: `0.12`)
- `--max-rows <n>`: Nur erste `n` Zeilen verarbeiten (Testzwecke)
- `--sources <liste>`: Komma-getrennte Quellen (default: `openlibrary,dnb`)
- `--cache <pfad>`: Persistenter ISBN-Cache als CSV (default: `data/isbn_cache.csv`)
- `--progress-every <n>`: Fortschritt alle `n` Zeilen ausgeben (default: `50`)
- `--quiet`: Lauf ohne Live-Ausgabe

## Beispiel-Aufrufe

Schneller Testlauf:

```bash
python3 enrich_isbn.py --in books.csv --out data/books_with_isbn_sample.csv --max-rows 50 --delay 0 --progress-every 5
```

Voller Lauf mit lockererem Schwellenwert:

```bash
python3 enrich_isbn.py --in books.csv --out data/books_with_isbn.csv --min-confidence 0.72 --progress-every 25
```

Nur DNB verwenden:

```bash
python3 enrich_isbn.py --in books.csv --out data/books_with_isbn.csv --sources dnb
```

Voller Lauf mit persistentem Cache:

```bash
python3 enrich_isbn.py --in books.csv --out data/books_with_isbn.csv --cache data/isbn_cache.csv
```

Lauf fortsetzen nach Abbruch (gleicher Cache):

```bash
python3 enrich_isbn.py --in books.csv --out data/books_with_isbn.csv --cache data/isbn_cache.csv --progress-every 25
```

## Live-Feedback waehrend der Ausfuehrung

Das Script gibt standardmaessig Status aus:

- Eingabe-/Ausgabedatei
- Verwendete Quellen
- Fortschritt alle `n` Zeilen
- Jede Abfrage mit Titel/Autor pro Quelle wird im Terminal ausgegeben
- Treffer-Meldungen mit ISBN, Quelle und Confidence
- Cache-Nutzung (`Loaded cache entries`, `from cache`, `Cache hits`)
- Abschluss mit Anzahl verarbeiteter Zeilen und Treffer

Beispielausgabe:

```text
Reading input: .../data/books.csv
Writing output: data/books_with_isbn.csv
Using sources: openlibrary, dnb
Loaded cache entries: 120
[50] Processing: ...
  -> ISBN 978... from cache (source dnb, confidence 0.84)
  -> ISBN 978... via dnb (confidence 0.84)
Finished processing rows: 500
Matches found so far: 132
Cache hits: 87
Processed 500 rows
ISBN found for 132 rows
Output written to data/books_with_isbn.csv
```

## Cache und Resume

Das Script speichert gefundene ISBNs sofort in einer Cache-Datei und kann dadurch nach einem Abbruch weiterarbeiten, ohne bereits gefundene Treffer erneut ueber externe APIs zu suchen.

- Default-Cache: `data/isbn_cache.csv`
- Schluessel: normalisierter `title` + normalisierter `author`
- Inhalt pro Cache-Zeile: cache_key, status, title, author, isbn, matched_title, matched_author, confidence, source
- Reihenfolge der Quellen: zuerst `openlibrary`, danach Fallback auf `dnb`

Wichtig fuer Abbruchsicherheit:

- Jede Ausgabezeile wird sofort in die Ausgabe-CSV geschrieben (flush).
- Jeder neue Treffer wird sofort in die Cache-CSV angehaengt (flush).
- Auch bereits gepruefte Titel ohne Treffer werden in der Cache-CSV gemerkt und bei naechsten Laeufen uebersprungen.
- Beim naechsten Lauf werden Cache-Eintraege geladen und bevorzugt verwendet.

## Ausgabeformat

Die Ausgabe enthaelt alle Originalspalten plus:

- `isbn`: gefundene ISBN (bevorzugt ISBN-13)
- `matched_title`: Titel aus der Trefferquelle
- `matched_author`: Autor aus der Trefferquelle
- `confidence`: Matching-Score (0..1)
- `source`: verwendete Quelle (`openlibrary` oder `dnb`)

## Matching-Logik (Kurzfassung)

1. Titel/Autor werden normalisiert.
2. Fuer DACH-Titel werden Query-Varianten gebildet (z. B. Umlaut-Varianten wie `ae/oe/ue`, ASCII-Variante).
3. Pro Treffer wird ein kombinierter Score aus Titel- und Autor-Aehnlichkeit berechnet.
4. Es werden nur gueltige ISBN-10/ISBN-13 akzeptiert.
5. Der beste Treffer ueber dem Schwellwert (`--min-confidence`) wird gespeichert.

## Fehlerbehebung

### Fehler: "can't open file '.../enrich_isbn.py'"

Du bist vermutlich nicht im Projekt-Root oder die Datei wurde mit falschem Pfad gestartet.

Loesung:

```bash
cd /home/thomas/Dokumente/project/bibliothek-stein-ar/online-katalog
python3 enrich_isbn.py --in books.csv --out data/books_with_isbn.csv
```

### Fehler: "Input file not found"

Pruefe den Pfad von `--in`.

Empfohlen:

```bash
python3 enrich_isbn.py --in data/books.csv --out data/books_with_isbn.csv
```

### Keine oder wenige Treffer

- `--min-confidence` etwas senken (z. B. `0.72`)
- DNB aktiv lassen (`--sources openlibrary,dnb`)
- Testlauf mit `--max-rows` durchfuehren und Ergebnisse pruefen

## Empfehlung fuer Produktivlauf

```bash
python3 enrich_isbn.py --in data/books.csv --out data/books_with_isbn.csv --min-confidence 0.72 --progress-every 25
```
