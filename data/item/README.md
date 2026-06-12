Eine Datei pro Katalog-Datensatz.

Regeln:
- Dateiname = `<id>.json`
- Inhalt = kompletter Datensatz (Objekt mit mindestens `id`, `title`, `author`)
- `scripts/build_catalog.py` aktualisiert diese Dateien und erzeugt danach `data/catalog.json`

Direkte Korrekturen:
- Das Meldeformular auf der Detailseite exportiert genau diese Datensatz-Datei.
- Datei in diesem Ordner ersetzen und Build erneut ausfuehren.
