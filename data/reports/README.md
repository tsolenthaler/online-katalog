Dieser Ordner ist nur noch fuer Legacy-Meldungen.

Neuer Ablauf:
- Das Detail-Meldeformular erzeugt eine Datensatz-Datei mit der stabilen ID als Dateiname.
- Diese Datei wird direkt in `data/item/<id>.json` ersetzt.
- `python scripts/build_catalog.py` erstellt danach `data/catalog.json` aus allen Dateien in `data/item/`.

Hinweis: Dateien in `data/reports/` werden vom Build-Skript nicht mehr ausgewertet.
