# sidequest

Öffentliche Seite der Marke sidequest. Jedes Kriterium ist 0–10 als Mittelwert seiner Teilkennzahlen. Der Aktien-Score ist der ungewichtete Mittelwert der sichtbaren Kriterien mit Daten, ebenfalls 0–10. C_09 bleibt aus, solange sein Gewicht in der Satelliten-Rolle 0 ist. Die Dividendenrolle bleibt im Code und ist über `roles.dividendEnabled` abschaltbar; der Schalter steht auf aus. Die Seite zeigt keine Rolle und keine Gewichtung. Ab 7 grün, von 5 bis unter 7 orange, darunter rot. K.O.-Hinweise stehen getrennt vom Schnitt. Fehlt einer Kennzahl die Datenbasis, bleibt der Wert leer, das Feld `status` ist „wenig Daten“ und die Punkte sind nicht 0. Dieselbe Bezeichnung steht im zugeklappten Kriterium. Der Score zählt nur Kriterien mit Daten. Liegen für weniger als drei Kriterien Daten vor (`minCriteriaWithData` in `config/scoring.json`), ist `score` leer und `status` „wenig Daten“. Die Karte zeigt dann den Hinweis `missingDataNote`, sobald mindestens ein Kriterium fehlt. Ein Datum ohne Quelle bleibt „unbekannt“.

Keine Anlageberatung und keine Kauf- oder Verkaufsempfehlung. Die Scores sind eigene, regelbasierte Einschätzungen: öffentliche Unternehmens- und Marktdaten (u. a. SEC, Yahoo Finance, Nasdaq) werden nach festen Kriterien in Punkte von 0 bis 10 umgerechnet und gemittelt. Sie können unvollständig oder veraltet sein und ersetzen keine eigene Prüfung.

Zieladresse: https://yoursidequest.org (Vercel, Basispfad `/`).

Bis zum Umschalten bleibt GitHub Pages unter https://makx164.github.io/Aktien-check/ erreichbar. Daten und Schrift werden relativ zur Seite geladen.

## Was die Seite zeigt

Nur diese Ticker, in dieser Reihenfolge: PLTR, OSS, AMZN, TSLA, HIMS, LMND, IBRX, MU, IREN, GRAB, SIVE, SPCX, PYPL.

Die Startseite ist ein Kachelraster, eine Kachel je Position: Ticker, Name, Score von 0 bis 10 in der Ampelfarbe (oder „wenig Daten“) und „Stand der Daten“. Keine Beträge. Ein Klick öffnet die Analyse unter `#/TICKER`, zum Beispiel `#/PLTR`. „Alle Positionen“ und die Wortmarke führen zurück zum Raster. Unten steht ein kurzer roter Hinweis: keine Anlageberatung, die Bewertungen sind subjektive Interpretationen.

„Stand der Daten“ ist das jüngste Kursdatum oder das Ende einer Berichtsperiode, nicht der Tag des Abrufs und nicht das Datum eines einzelnen Insidergeschäfts. Fehlt beides, steht „wenig Daten“.

Die Analyse folgt dem Layout der Version 3.2: rote Ticker-Zeile, Aktien-Score mit Ampel, runde Skala, Score-Matrix und zugeklappte Kriterien. Unter dem Score steht ein Kurztext aus `config/profiles.json` und eine Zeile mit Sitz, Mitarbeitern, Börsenwert und Segmenten, soweit die Angaben vorliegen. Ein Klick öffnet Teilkennzahlen mit Wert, Skala, Punkten, Quelle, Link und Stichtag oder Abrufdatum. Darüber steht, aus wie vielen Metriken mit Daten das Kriterium gemittelt ist. Die Wortmarke im Kopf ist nur „sidequest“. Die Kurszeile zeigt den letzten Schlusskurs und sein Datum aus der Kursquelle. Die Branche steht nur, wenn sie in den Daten liegt.

C_05 summiert drei Teile auf 10: Insider-Anteil (über 10 % = 5, 3–10 % = 3, darunter 1), Insider-Saldo der letzten 6 Monate aus offenen Marktgeschäften (Netto-Käufe = 3, neutral = 2, Netto-Verkäufe = 0) und institutioneller Anteil (über 40 % = 2). Die Anteile kommen von Yahoo Finance, der Saldo aus SEC-Form-4 (Codes P und S). Das Feld `aff10b5One` kennzeichnet 10b5-1-Planverkäufe; sie zählen mit einem Viertel. Offene Käufe (Code P) zählen doppelt, mehrere verschiedene Käufer geben einen Bonus. Netto-Verkäufe unter 0,1 % der Marktkapitalisierung sind neutral. Fehlt ein Teil, fällt er aus der Summe.

## Daten

Pro Ticker eine Datei `data/<TICKER>/score.json`. Schwellen stehen in `config/scoring.json`, die Ticker in `config/tickers.json`. Die Rechnung macht `python3 scripts/build_scores.py`.

Phase 1 holt die Zahlen aus SEC EDGAR + Yahoo + Nasdaq. Für eine Bezahlversion sind lizenzierte Quellen nötig. Jahreszahlen und CAGRs kommen aus der SEC-EDGAR-API `companyfacts` (10-K, 20-F, 40-F). Margen, Wachstum zum Vorjahr, Cashflow, Bewertung und Verschuldung nutzen die letzten vier Quartale, sonst den längsten Vorjahresvergleich aus dem 10-Q. Fehlt ein Wert, springt der Lauf auf Yahoo Finance (quoteSummary, Cookie und Crumb) und danach auf die Nasdaq-API. Jeder Wert nennt Quelle, URL und Abrufdatum. Ein fehlender Abruf bleibt „wenig Daten“ und bricht den Lauf nicht ab. Kurse zuerst als Stooq-CSV. Lehnt Stooq den Abruf ab, springt der Lauf auf die öffentliche Nasdaq-Historie und schreibt das in die Datenqualität. Der S&P-500-Vergleich nutzt dann den ETF SPY und benennt ihn so. SIVE (Sivers Semiconductors, Stockholm) hat keine US-Meldepflicht. Fundamentalkennzahlen kommen von Yahoo, Symbol `SIVE.ST`, das Momentum gegen OMXS30. SPCX ist frisch gelistet: Halbjahreszahlen aus dem 10-Q zählen, 12-1-Momentum, 200-Tage-Linie und mehrjährige CAGR ohne Historie stehen als „zu kurze Historie“ und zählen nicht als 0.

Beim Kopfraum von Micron bleibt der Trailing-Umsatz gegen den US-Pool. Zusatz auf der MU-Seite und in dieser Methodik: Halbleiterwachstum 11,7 % (Damodaran, Stand Januar 2026); Pool nur US-Melder (ohne TSMC, ASML, Samsung, SK Hynix); zyklischer Spitzenumsatz drückt den Kopfraum.

Die Branche kommt aus dem Geschäftsmodell und den Segmenten im Geschäftsbericht. Sie wird nicht aus der Börse und nicht aus dem Ticker abgeleitet. Ein Damodaran-Eintrag, der nur an der Börse oder am Tickersymbol hängt, wird nicht verwendet. Die Zuordnung steht in `config/industry_overrides.json`, die Segmentmischung in `config/segments.json`.

„n/a (Aufbauphase)“ gilt nur, wenn M1 n/a ist (noch kein aussagekräftiger Umsatz). Ist M1 bewertet (auch bei M1 ≥ 5) und liegt ein operativer Verlust vor, wird M3 normal als ROIC minus Kapitalkosten gerechnet. Ein negativer Abstand gibt 0 (Untergrenze).

Ist der Umsatz eines Segments größer als der US-Pool seiner Branche, ist der Pool nicht repräsentativ. Dann wird die nächstpassende Damodaran-Branche verwendet und auf der Seite genannt.

`FMP_API_KEY` und `FINNHUB_API_KEY` bleiben optional. C_06 kommt zuerst von Yahoo (`recommendationTrend`, Kursziel, EPS-Trend über 90 Tage), sonst von der Nasdaq-Analysten-API. Unter drei Analysten zählt das Kriterium nicht. Unter fünf Analysten ist es bei 7 gedeckelt. API-Schlüssel stehen nicht in den Quell-URLs.

K.O., nur wenn die Eingaben wirklich da sind: Altman Z unter 1,8, negativer Free Cashflow bei Nettoverschuldung/EBITDA über 3, 8-K Item 4.02 in den letzten 24 Monaten. Ein Going-Concern-Hinweis wird nicht geraten.

Jeder Lauf schreibt Wert, Punkte, Quelle, Abrufdatum und Datenstand. Die Schwellen für C_01 bis C_05 sind lineare Rampen in der Konfiguration, abgeleitet aus dem Konzept (zum Beispiel Bruttomarge 0 Punkte bei 20 % und 10 Punkte bei 60 %, ROIC über zwei Jahre 0 bei 5 % und 10 bei 20 %). Gewinnfirmen in C_08 nutzen PEG (10 unter 1,0, 0 über 3,0). Verlustfirmen nutzen (EV/Umsatz) geteilt durch das Umsatzwachstum in Prozent (10 unter 0,2, 0 über 1,0). Unter fünf Analysten ist C_06 bei 7 gedeckelt.

Tests: `python3 -m unittest tests.test_scoring`.

Der Workflow `.github/workflows/scores.yml` läuft montags und donnerstags um 05:00 UTC und per manuellem Start. Er schreibt geänderte Dateien unter `data/` zurück. Rohabrufe liegen in `cache/` und bleiben außerhalb von Git.

## Deployment

Statische Dateien, kein Build. `vercel.json` setzt `cleanUrls`. Vercel nimmt das Repository-Root. GitHub Pages weiter aus `main` / Root; `.nojekyll` bleibt.

Die Domain hat früher eine Vite-PWA ausgeliefert: `registerSW.js` hat `/sw.js` mit Scope `/` registriert. Zurückkehrende Browser können die alte App aus dem Cache zeigen. `sw.js` und `service-worker.js` im Root sind derselbe Kill-Switch. Beim Install ruft er `skipWaiting` auf. Beim Activate löscht er alle Caches, hebt die Registrierung mit `self.registration.unregister()` auf und lädt danach jedes Fenster über `clients.matchAll()` neu auf seine URL. `vercel.json` setzt für beide Dateien `Cache-Control: no-cache`. `index.html` meldet vorhandene Service Worker ab.
