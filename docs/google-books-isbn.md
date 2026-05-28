# Google Books ISBN Lookup

Diese Anleitung zeigt, wie du das Script fuer fehlende ISBNs mit der Google Books API verwendest.

## Ziel

Das Script liest `data/missing.csv`, sucht pro Titel bei Google Books und schreibt die Ergebnisse in `data/books_with_isbn_google.csv`.

Script-Dateien:

- `fill_missing_from_google_books.py` (Wrapper im Projekt-Root)
- `src/command/fill_missing_from_google_books.py` (Implementierung)

## Voraussetzungen

- Python 3 installiert
- Optional: Google API Key

Hinweis: Fuer die Google Books API sind auch Anfragen ohne API Key moeglich, aber ein API Key ist fuer stabile Nutzung empfohlen.

## API Key setzen

Empfohlen: Key als Umgebungsvariable setzen, nicht direkt im Befehl speichern.

### Variante A: pro Shell-Session

```bash
export GOOGLE_BOOKS_API_KEY="dein_api_key"
```

### Variante B: aus .env laden

Wenn in `.env` eine Zeile wie unten steht:

```dotenv
GOOGLE_BOOKS_API_KEY=dein_api_key
```

Dann in der Shell laden:

```bash
set -a
source .env
set +a
```

## Standardlauf

Aus dem Projekt-Root:

```bash
python3 fill_missing_from_google_books.py
```

Default-Pfade:

- Input: `data/missing.csv`
- Output: `data/books_with_isbn_google.csv`
- Cache: `data/google_books_cache.csv`

## Quota-schonend arbeiten (kostenlos bleiben)

Das Script ist bereits auf API-Schonung ausgelegt:

- Cache verhindert doppelte Requests fuer bereits abgefragte Titel
- Delay zwischen Requests
- Hartes Request-Limit pro Lauf

Wichtige Parameter:

- `--delay`: Pause zwischen API-Calls (Default: `0.35` Sekunden)
- `--max-requests`: maximal erlaubte API-Calls pro Lauf (Default: `300`)
- `--max-rows`: nur erste N Zeilen verarbeiten (Tests)
- `--cache`: Cache-Datei (Default: `data/google_books_cache.csv`)

Beispiel fuer vorsichtigen Lauf:

```bash
python3 fill_missing_from_google_books.py --max-rows 100 --max-requests 80 --delay 0.5
```

## Nützliche Beispielaufrufe

Schneller Test mit wenigen Zeilen:

```bash
python3 fill_missing_from_google_books.py --max-rows 20 --max-requests 20
```

Eigene Pfade verwenden:

```bash
python3 fill_missing_from_google_books.py \
  --in data/missing.csv \
  --out data/books_with_isbn_google.csv \
  --cache data/google_books_cache.csv
```

API Key direkt als Argument (optional):

```bash
python3 fill_missing_from_google_books.py --api-key "dein_api_key"
```

## Ausgabe verstehen

Die Datei `data/books_with_isbn_google.csv` enthaelt unter anderem:

- `title`, `author`
- `isbn`, `isbn_type`
- `matched_title`, `matched_author`
- `google_id`
- `status`
- `from_cache`

Wichtige Statuswerte:

- `found`: ISBN gefunden
- `not_found`: kein Treffer
- `isbn_missing`: Treffer ohne ISBN
- `request_error`: Request/Netzwerkproblem
- `quota_guard_reached`: internes Request-Limit in diesem Lauf erreicht

## Lauf fortsetzen

Einfach denselben Befehl erneut ausfuehren.
Durch den Cache werden schon bekannte Titel nicht erneut abgefragt.

## Fehlerbehebung

### Input CSV nicht gefunden

Pruefe den Pfad bei `--in`.

### Viele `request_error`

- Internetverbindung pruefen
- `--delay` erhoehen (z. B. `0.7`)
- spaeter erneut starten

### Zu viele `not_found`

- Titel in `data/missing.csv` auf Tippfehler pruefen
- fehlende Autoren nachtragen, falls moeglich
- zuerst einen Testlauf mit `--max-rows` machen
