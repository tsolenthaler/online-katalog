Projekt-Prompt: Online-Katalog Bibliothek Stein AR
Rolle: Du bist ein erfahrener Full-Stack Webentwickler spezialisiert auf statische Webseiten (Static Site Generators) und API-Integrationen.

Ziel: Erstelle ein Konzept und den Code für einen öffentlichen Online-Katalog für die "Bibliothek Stein AR". Die Webseite läuft ausschließlich auf GitHub Pages (daher nur statische HTML/CSS/JS ohne Backend-Server).

1. Ausgangslage & Datenbasis
Quelldaten: Alle Bestände liegen aktuell in einer PDF-Datei unter /data/Titelliste.pdf vor. Diese enthält Titel und Autor, aber keine ISBN.
Ergänzungsdaten: Es gibt die Möglichkeit einer manuellen Overrides-Liste (manual_overrides.csv) mit Format title,author,isbn, um falsche automatisierte Zuordnungen zu korrigieren.
Datengenerierung: Da GitHub Pages keinen Server hat, muss der Katalog beim Build-Prozess (z.B. via GitHub Actions) generiert werden. Ein Skript muss:
Die PDF parsen.
Die manuelle CSV laden.
Für jeden Titel/Autor-Paar ohne gültige ISBN eine API-Abfrage durchführen an:
Open Library API
Google Books API
Deutsches Nationalbibliothek (DNB) Portal
Die gefundenen Metadaten (ISBN, Cover-Bild URL, Beschreibung, Genre) speichern und eine statische JSON-Datenbank (catalog.json) generieren.
2. Sitemap & Seitenstruktur
Die Seite soll folgende HTML-Seiten enthalten:

Startseite (index.html):
Großes Suchfeld ("Suche nach Titel, Autor...") + Button.
Einfache Willkommens-Nachricht.
Links zum kompletten Katalog und zur Meldung neuer Bücher.
Katalog Übersicht (catalog.html):
Anzeige aller Items als Grid/Liste.
Filter-Sidebar oder Dropdowns:
Typ (Bücher, CDs, Zeitschriften, Spiele/Kartenspiele).
Genre/Rubrik.
Besitzer (optional, wenn vorhanden).
Checkbox: "Neu hinzugefügt" (basierend auf einem date_added Feld).
Suchfunktion direkt auf dieser Seite (client-seitig über JS oder durch Neuladen mit Parametern).
Suchergebnisseite (search.html):
Wird von der Startseite bei Klick erreicht.
Zeigt Ergebnisse für die Query sowie aktive Filter.
Neuheiten-Seite (new.html):
Dedizierte Ansicht nur für Objekte, die in den letzten X Tagen/Monaten hinzugefügt wurden.
Detailseite (item-detail.html oder dynamisch detail.html?id=xyz):
Anzeige pro Objekt: Titel, Autor, Cover, Beschreibung, Kategorie, Besitzer.
Externe Links: Button zu OpenLibrary.org und Google Books (mit spezifischer ID, falls verfügbar).
Social-Sharing: Buttons zum Teilen auf WhatsApp etc., die dynamisch Title, Author und eine kurze Zusammenfassung als "Rich Snippet" (Open Graph Tags) vorbereiten.
3. Funktionalitäten & UI-Anforderungen
Sprache: Deutsch (alle Labels, Kategorien, Fehlermeldungen).
Usability: Sehr einfach bedienbar ("One-Click"-Philosophie). Große Buttons, klare Schriftarten. Mobile responsive.
Datenanreicherung: Fallback-Logik für Cover-Bilder (falls keine API ein Bild liefert, Platzhalter verwenden).
Benutzereingabe (Community Feature):
Formulart oder CSV-Upload-Seite (contributions.html), wo Privatpersonen ihre eigenen Medien melden können, um den Katalog zu erweitern.
Eingabeform: Titel, Autor, Typ, Besitzer (optional), Hinweis auf "Tausch意愿" (Wunsch nach Tausch).
4. Technische Umsetzung (GitHub Pages Constraints)
Architektur: Static Site Generation (SSG).
Empfehlung: Nutze ein Tool wie Jekyll, Hugo oder ein simples Node.js Script in einer GitHub Action, das bei jedem Push das catalog.json regeneriert und die HTML-Seiten baut.
Das JavaScript auf den Seiten lädt dann nur noch das catalog.json und filtert/sortiert client-seitig.
APIs: Die Abfragen an OpenLibrary/GoogleBooks müssen während des Build-Prozesses (in der Cloud via GitHub Actions) laufen, nicht im Browser des Besuchers, um Rate-Limits zu vermeiden und Performance zu sichern.
Social Sharing: Nutze Open Graph Meta-Tags (og:title, og:description, og:image) auf der Detailseite, damit WhatsApp/Facebook die Vorschau korrekt anzeigen.
5. Deliverables (Was du liefern sollst)
Erstelle folgenden Output:

Projektdokumentation: Kurze Erklärung der Architektur (Build-Prozess vs. Laufzeit).
Skript-Entwurf: Ein Beispiel für das Build-Script (Python oder Node.js), das PDF liest, APIs abfragt und JSON erstellt.
HTML/CSS Templates:
Grundgerüst für index.html, catalog.html, detail.html.
CSS für ein sauberes, modernes, deutsches Design.
JavaScript Logik: Code für das clientseitige Filtern und Suchen in der JSON-Datei.
Konfigurationsvorschlag: Wie die GitHub Actions Workflow Datei (build.yml) auszusehen hat.
Hinweis zur Datensammlung: Wenn die APIs keine ISBN finden, sollte der Eintrag trotzdem mit dem Status "Keine ISBN ermittelt" in den Katalog kommen, aber manuell nachträglich bearbeitet werden können.