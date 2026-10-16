# sidequest

Öffentliche Seite der Marke sidequest. Acht Kriterien, jedes 0–10 als Mittelwert seiner Teilkennzahlen. Der Aktien-Score ist der gewichtete Mittelwert mal 10, also 0–100. C_07 zählt mit Gewicht 0,5, die übrigen Kriterien mit 1. Ab 70 grün, von 50 bis 69 orange, darunter rot. K.O.-Hinweise stehen getrennt vom Schnitt. Fehlt einer Kennzahl die Datenbasis, bleibt der Wert leer, das Feld `status` ist „wenig Daten“ und die Punkte sind nicht 0. Dieselbe Bezeichnung steht im zugeklappten Kriterium. Der Score zählt nur Kriterien mit Daten und normiert deren Gewichte neu. Liegen für weniger als drei Kriterien Daten vor (`minCriteriaWithData` in `config/scoring.json`), ist `score` leer und `status` „wenig Daten“. Die Karte zeigt dann den Hinweis `missingDataNote`, sobald mindestens ein Kriterium fehlt. Ein Datum ohne Quelle bleibt „unbekannt“.

Ich halte diese Aktien selbst. Keine Anlageberatung.

Zieladresse: https://yoursidequest.org (Vercel, Basispfad `/`).

Bis zum Umschalten bleibt GitHub Pages unter https://makx164.github.io/Aktien-check/ erreichbar. Daten und Schrift werden relativ zur Seite geladen.

## Was die Seite zeigt

Nur diese Ticker, in dieser Reihenfolge: PLTR, OSS, AMZN, TSLA, HIMS, LMND, IBRX, MU, IREN, GRAB, SIVE, SPCX, PYPL.

Die Startseite ist ein Kachelraster, eine Kachel je Position: Ticker, Name, Score von 0 bis 100 in der Ampelfarbe (oder „wenig Daten“) und „Stand der Daten“. Keine Beträge und keine Gewichte. Ein Klick öffnet die Analyse unter `#/TICKER`, zum Beispiel `#/PLTR`. „Alle Positionen“ und die Wortmarke führen zurück zum Raster. Die Hinweise „Ich halte diese Aktien selbst.“ und „Keine Anlageberatung.“ stehen auf der Startseite und auf jeder Analyse.

Keine Beträge, Gewichte oder Kaufdaten. Oben stehen die Hinweise „Ich halte diese Aktien selbst.“ und „Keine Anlageberatung.“ „Stand der Daten“ ist das Datum des jüngsten Datenpunkts (Kursdatum oder Ende der Berichtsperiode), nicht der Tag des Abrufs. Fehlt er, steht „unbekannt“.

Die Analyse folgt dem Layout der Version 3.2: rote Ticker-Zeile, Aktien-Score mit Ampel, runde Skala, „Passt am besten als“ aus den Rollen-Gewichten (Satellit, Tenbagger, Compounder, Dividenden-Aktie), Score-Matrix und zugeklappte Kriterien C_01 bis C_08. Ein Klick öffnet Teilkennzahlen mit Wert, Punkten und Quelle. C_07 hat im Aktien-Score das Gewicht 0,5. Fehlt die Dividendenrendite, bleibt die Dividenden-Rolle „wenig Daten“. Kurs und Branche stehen nur, wenn sie in den Daten liegen.

## Daten

Pro Ticker eine Datei `data/<TICKER>/score.json`. Schwellen stehen in `config/scoring.json`, die Ticker in `config/tickers.json`. Die Rechnung macht `python3 scripts/build_scores.py`.

Phase 1 holt Jahreszahlen aus der SEC-EDGAR-API `companyfacts` (10-K, 20-F, 40-F) und Kurse zuerst als Stooq-CSV. Lehnt Stooq den Abruf ab, springt der Lauf auf die öffentliche Nasdaq-Historie und schreibt das in die Datenqualität. Der S&P-500-Vergleich nutzt dann den ETF SPY und benennt ihn so. SIVE (Sivers Semiconductors, Stockholm) hat keine SEC-Meldung: Fundamentalkennzahlen und C_06 (Analysten) sind „wenig Daten“. Die Kurse kommen von der Yahoo-Finance-Chart-API, Symbol `SIVE.ST`, weil Stooq blockiert. Quelle, Symbol und URL stehen in `data/SIVE/score.json`. SPCX ist frisch gelistet, ohne 12-Monats-Historie und ohne 200-Tage-Linie; fehlende Kennzahlen bleiben „wenig Daten“ und ziehen den Score nicht auf 0.

`FMP_API_KEY` und `FINNHUB_API_KEY` sind optional. Ohne Schlüssel bleiben die Analysten-Kennzahlen „wenig Daten“, der Lauf bricht nicht ab. Eine aktuelle Schätzung ohne Verlauf ist keine 90-Tage-Revision und bleibt leer. SIVE setzt `analysts` auf false, damit C_06 nicht aus einer US-Quelle geraten wird. API-Schlüssel stehen nicht in den Quell-URLs.

K.O., nur wenn die Eingaben wirklich da sind: Altman Z unter 1,8, negativer Free Cashflow bei Nettoverschuldung/EBITDA über 3, 8-K Item 4.02 in den letzten 24 Monaten. Ein Going-Concern-Hinweis wird nicht geraten.

Jeder Lauf schreibt Wert, Punkte, Quelle, Abrufdatum und Datenstand. Die Schwellen für C_01 bis C_05 sind lineare Rampen in der Konfiguration, abgeleitet aus dem Konzept (zum Beispiel Bruttomarge 0 Punkte bei 20 % und 10 Punkte bei 60 %, ROIC über zwei Jahre 0 bei 5 % und 10 bei 20 %). Gewinnfirmen in C_08 nutzen PEG (10 unter 1,0, 0 über 3,0). Verlustfirmen nutzen (EV/Umsatz) geteilt durch das Umsatzwachstum in Prozent (10 unter 0,2, 0 über 1,0). Unter fünf Analysten ist C_06 bei 7 gedeckelt.

Tests: `python3 -m unittest tests.test_scoring`.

Der Workflow `.github/workflows/scores.yml` läuft montags und donnerstags um 05:00 UTC und per manuellem Start. Er schreibt geänderte Dateien unter `data/` zurück. Rohabrufe liegen in `cache/` und bleiben außerhalb von Git.

## Deployment

Statische Dateien, kein Build. `vercel.json` setzt `cleanUrls`. Vercel nimmt das Repository-Root. GitHub Pages weiter aus `main` / Root; `.nojekyll` bleibt.

Die Domain hat früher eine Vite-PWA ausgeliefert: `registerSW.js` hat `/sw.js` mit Scope `/` registriert. Zurückkehrende Browser können die alte App aus dem Cache zeigen. `sw.js` und `service-worker.js` im Root sind derselbe Kill-Switch. Beim Install ruft er `skipWaiting` auf. Beim Activate löscht er alle Caches, hebt die Registrierung mit `self.registration.unregister()` auf und lädt danach jedes Fenster über `clients.matchAll()` neu auf seine URL. `vercel.json` setzt für beide Dateien `Cache-Control: no-cache`. `index.html` meldet vorhandene Service Worker ab.
