# Übersicht


## Ziele

* Online-Katalog aller Bücher der Biliothek Stein AR
* Suche nach Title
* Suche nach ISBN
* Suche nach Genre / Rubrik
* Suche nach Autor
* Suche in einem Eingabe-Feld
* Responsive / Als Mobile Web App abrufbar
* Statistik nach Autor
* Statistik nach Genre


## Anforderungen

* Statische Seite -> Githube Page


## Web-Katalog starten

1. Katalogdaten erzeugen:

```bash
python3 build_catalog.py --in data/books_with_isbn.csv --out data/catalog.json
```

2. Lokal testen (statischer Server):

```bash
python3 -m http.server 8000
```

Dann im Browser aufrufen: `http://localhost:8000`

## Web-Katalog Dateien

* `index.html`: UI fuer Suche, Filter und Statistik
* `assets/styles.css`: responsives Design
* `assets/app.js`: Laden von `data/catalog.json`, Filterlogik, Rendering