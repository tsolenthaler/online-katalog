# Online-Katalog Bibliothek Stein AR

Dieses Projekt erstellt und veröffentlicht einen statischen Online-Katalog auf GitHub Pages.
Die Datengenerierung läuft lokal auf dem Bibliotheksrechner.

## Architektur

1. Build-Prozess (lokal):
	- PDF auslesen: `data/Titelliste.pdf`
	- Manuelle Korrekturen laden: `data/manual_overrides.csv`
	- ISBN pro Eintrag ermitteln (Override -> Cache -> DNB/Google)
	- Metadaten nur über ISBN abrufen (Open Library, Google Books, DNB)
	- Ergebnis schreiben nach `data/catalog.json`

2. Laufzeit (GitHub Pages):
	- Nur statische Dateien (`.html`, `.css`, `.js`, `.json`)
	- Browser lädt `data/catalog.json` und filtert clientseitig
	- Keine API-Aufrufe im Browser der Besucher

## Seiten

- `index.html`: Startseite mit Hauptsuche
- `catalog.html`: kompletter Katalog mit Filtern und Suchergebnissen
- `search.html`: Legacy-Weiterleitung auf `catalog.html` (für bestehende Links)
- `new.html`: Neuheiten-Ansicht
- `detail.html`: Detailseite eines Mediums (`?id=...`)
- `contributions.html`: Meldeseite inkl. CSV-Export

## Lokaler Ablauf

1. Abhängigkeiten installieren:

```bash
python -m pip install -r requirements.txt
```

2. Katalogdaten erzeugen:

```bash
python scripts/build_catalog.py --pdf data/Titelliste.pdf --manual data/manual_overrides.csv --out data/catalog.json
```

Optional offline (nur Cache + lokale Heuristik):

```bash
python scripts/build_catalog.py --offline
```

3. Lokal testen:

```bash
python -m http.server 8000
```

Dann im Browser öffnen: `http://localhost:8000`

## Wichtige Dateien

- `scripts/build_catalog.py`: Build-Skript für PDF/ISBN/Metadaten
- `data/catalog.json`: statische Katalogdaten für die Website
- `data/catalog_metadata_cache.json`: Metadaten-Cache
- `data/isbn_cache.csv`: ISBN-Cache
- `assets/catalog.js`: Suche, Filter, Rendering
- `assets/detail.js`: Detailseite + Share/OpenGraph im DOM
- `assets/contributions.js`: Community-Beiträge + CSV-Export

## Datenqualität und Nachpflege

Wenn keine ISBN ermittelt werden kann, bleibt der Eintrag erhalten und erhält den Status `Keine ISBN ermittelt`.
Diese Einträge können über `data/manual_overrides.csv` manuell ergänzt werden.