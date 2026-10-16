# sidequest

Öffentliche Seite der Marke sidequest. Acht Kriterien, jedes 0–10 als Mittelwert seiner Teilkennzahlen. Der Aktien-Score ist der gewichtete Mittelwert mal 10, also 0–100. C_07 zählt mit Gewicht 0,5, die übrigen Kriterien mit 1. Ab 70 grün, von 50 bis 69 orange, darunter rot. K.O.-Hinweise stehen getrennt vom Schnitt. Fehlende Werte bleiben leer und erscheinen als „unbekannt“.

Ich halte diese Aktien selbst. Keine Anlageberatung.

Zieladresse: https://yoursidequest.org (Vercel, Basispfad `/`).

Bis zum Umschalten bleibt GitHub Pages unter https://makx164.github.io/Aktien-check/ erreichbar. Daten und Schrift werden relativ zur Seite geladen.

## Was die Seite zeigt

Nur diese Ticker, in dieser Reihenfolge: PLTR, OSS, AMZN, TSLA, HIMS, LMND, IBRX, MU, IREN, GRAB, SIVE, SPCX, PYPL.

Keine Beträge, Gewichte oder Kaufdaten. Oben stehen die Hinweise „Ich halte diese Aktien selbst.“ und „Keine Anlageberatung.“ „Stand der Daten“ ist das Datum des jüngsten Datenpunkts (Kursdatum oder Ende der Berichtsperiode), nicht der Tag des Abrufs. Fehlt er, steht „unbekannt“.

In der Übersicht sind C_01 bis C_08 zugeklappt. Ein Klick öffnet Teilkennzahlen mit Wert, Punkten, Quelle, Abrufdatum und Datenstand. C_07 zeigt das Gewicht 0,5.

## Daten

Pro Ticker eine Datei `data/<TICKER>/score.json`. Schwellen stehen in `config/scoring.json`, die Ticker in `config/tickers.json`. Die Rechnung macht `python3 scripts/build_scores.py`.

Phase 1 holt Jahreszahlen aus der SEC-EDGAR-API `companyfacts` (10-K, 20-F, 40-F) und Kurse zuerst als Stooq-CSV. Lehnt Stooq den Abruf ab, springt der Lauf auf die öffentliche Nasdaq-Historie und schreibt das in die Datenqualität. Der S&P-500-Vergleich nutzt dann den ETF SPY und benennt ihn so. SIVE hat keine SEC-Meldung: nur Kurse, mit dem Hinweis auf die dünne Datenbasis. SPCX bleibt im Datensatz, auch wenn die Historie kurz ist.

`FMP_API_KEY` und `FINNHUB_API_KEY` sind optional. Ohne Schlüssel bleiben die Analysten-Kennzahlen leer, der Lauf bricht nicht ab. Eine aktuelle Schätzung ohne Verlauf ist keine 90-Tage-Revision und bleibt leer. API-Schlüssel stehen nicht in den Quell-URLs.

K.O., nur wenn die Eingaben wirklich da sind: Altman Z unter 1,8, negativer Free Cashflow bei Nettoverschuldung/EBITDA über 3, 8-K Item 4.02 in den letzten 24 Monaten. Ein Going-Concern-Hinweis wird nicht geraten.

Jeder Lauf schreibt Wert, Punkte, Quelle, Abrufdatum und Datenstand. Die Schwellen für C_01 bis C_05 sind lineare Rampen in der Konfiguration, abgeleitet aus dem Konzept (zum Beispiel Bruttomarge 0 Punkte bei 20 % und 10 Punkte bei 60 %, ROIC über zwei Jahre 0 bei 5 % und 10 bei 20 %). Gewinnfirmen in C_08 nutzen PEG (10 unter 1,0, 0 über 3,0). Verlustfirmen nutzen (EV/Umsatz) geteilt durch das Umsatzwachstum in Prozent (10 unter 0,2, 0 über 1,0). Unter fünf Analysten ist C_06 bei 7 gedeckelt.

Tests: `python3 -m unittest tests.test_scoring`.

Der Workflow `.github/workflows/scores.yml` läuft montags und donnerstags um 05:00 UTC und per manuellem Start. Er schreibt geänderte Dateien unter `data/` zurück. Rohabrufe liegen in `cache/` und bleiben außerhalb von Git.

## Deployment

Statische Dateien, kein Build. `vercel.json` setzt `cleanUrls`. Vercel nimmt das Repository-Root. GitHub Pages weiter aus `main` / Root; `.nojekyll` bleibt.
