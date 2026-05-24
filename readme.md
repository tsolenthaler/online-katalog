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
* `book.html`: Detailansicht eines einzelnen Buchs
* `assets/styles.css`: responsives Design
* `assets/app.js`: Laden von `data/catalog.json`, Filterlogik, Rendering
* `assets/book.js`: Routing und Rendering fuer Detailseite

## Detailseiten-Routing

Die Detailseite wird ueber den Query-Parameter `book` aufgerufen:

* Mit ISBN: `book.html?book=9783522202602`
* Ohne ISBN (Fallback auf interne ID): `book.html?book=row-12`

Beim Wechsel von der Suche zur Detailseite wird der aktuelle Filterzustand als
`returnTo` mitgegeben. Der Link "Zurueck zur Suche" stellt diesen Zustand wieder her.

## Deep-Link Suche

Die Suchseite akzeptiert URL-Parameter und aktualisiert sie live beim Filtern:

* `q`: Suchtext
* `author`: exakter Autorname
* `genre`: exaktes Genre

Beispiel:

`index.html?q=geschichte&author=Ende,%20Michael&genre=Fantasy`

## Quellen auf Detailseite

Auf `book.html` werden klickbare Quellenlinks angezeigt, z. B.:

* OpenLibrary
* DNB
* Google Books (wenn vorhanden)