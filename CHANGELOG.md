# Changelog

## 2026-10-20

### Fachlicher Check, Scoring

Gewichte in `config/scoring.json`: C_02 = 1,5, C_07 und C_08 = 0,5. Verlustbringer bekommen bei C_02 die Veränderung der operativen Marge zum Vorjahr (−5 Pp = 0, +10 Pp = 10). Negativer Free Cashflow ersetzt die Eigenkapitalrendite durch die Cash Runway (unter 12 Monaten = 0, ab 36 Monaten = 10). Lemonade ist als Versicherer markiert: Current Ratio und Nettoverschuldung/EBITDA sind „nicht anwendbar“. Die Free-Cashflow-Rendite ist bei Verlustbringern „nicht anwendbar“. Fehlt das PEG bei einem Gewinnunternehmen, weil das EPS-Wachstum nicht positiv ist, steht (EV/Umsatz) / Umsatzwachstum (10 unter 0,2, 0 über 1,0); schrumpft das letzte Jahr, zählt der 3-Jahres-CAGR.

Form-4-Feld `aff10b5One` kennzeichnet 10b5-1-Planverkäufe. Sie zählen mit einem Viertel, offene Käufe (Code P) doppelt, mehrere verschiedene Käufer mit Bonus. Netto-Verkauf unter 0,1 % der Marktkapitalisierung ist neutral. C_06 ohne API-Schlüssel ist „ausgeblendet, zählt nicht“. SIVE zeigt „keine US-Meldepflicht“, das Momentum gegen OMXS30. SPCX zeigt „zu kurze Historie“, wo die Reihe strukturell fehlt. Die Übersicht heißt „Übersicht Aktienportfolio“.

## 2026-10-16

### Stand der Daten und Kachelfarbe

Der Stand ist das jüngste Kursdatum oder Berichtsende. Bei SPCX fehlte das Kursdatum in `asOf`, deshalb zeigte die Kachel das Insidergeschäft vom 22.09.2026 (zuvor 02.04.2026). Der Nasdaq-Schluss vom 15.10.2026 ist der Stand. Ohne Kurs- und Berichtsdatum steht „wenig Daten“. Der Ticker auf der Kachel ist neutral, die Ampelfarbe bleibt am Score.

### Kurszeile und C_05

Die Kurszeile zeigt den letzten Schlusskurs und das Datum aus der Kursquelle (`price` in `score.json`). C_05 zählt Insider-Anteil, den Sechs-Monats-Saldo offener Marktgeschäfte und den institutionellen Anteil. Schwellen und Beträge stehen in der Datei. Eine Form-4 mit Kennzeichen 10b5-1 wirft die Käufe und Verkäufe nicht mehr weg.

### Kacheln und Analyse unter #/TICKER

Die Startseite zeigt 13 Kacheln: Ticker, Name, Score 0–100 in der Ampelfarbe oder „wenig Daten“, und „Stand der Daten“. Keine Beträge, keine Gewichte. Ein Klick öffnet die Analyse unter `#/TICKER`, zum Beispiel `#/PLTR`. Wortmarke, Logo und „Alle Positionen“ führen zurück. „Ich halte diese Aktien selbst.“ und „Keine Anlageberatung.“ stehen auf der Startseite und auf jeder Analyse.

Die Analyse übernimmt Aufbau, Schrift und Farben der Version 3.2: rote Ticker-Zeile, runde Score-Skala, Rollen-Einordnung, Score-Matrix und Kriterienkarten. Der Aktien-Score kommt aus `score.json`. Die Rollen-Scores rechnen die Kriterien 0–10 mit den Gewichten der Version 3.2. Prototyp-Stempel, Wasserzeichen und Blöcke ohne Datengrundlage (KI-Einschätzung, Kurzfazit, X-Stimmung) entfallen. Die Wortmarke lautet „sidequest“.

### Wenig Daten, SIVE-Kurse, Service Worker

Fehlt einer Kennzahl die Basis, ist `status` „wenig Daten“ und `points` bleibt leer. Eine fehlende Kennzahl zählt nicht als 0 und fällt aus dem Mittel. Der Aktien-Score normiert die Gewichte auf die Kriterien mit Daten. Unter `minCriteriaWithData` (3) bleibt `score` leer, `status` ist „wenig Daten“, und die Karte zeigt `missingDataNote`. Dieselbe Bezeichnung steht im zugeklappten Kriterium.

Lauf am 16.10.2026 ohne `FMP_API_KEY` und `FINNHUB_API_KEY`, deshalb ist C_06 bei jedem Ticker „wenig Daten“ und nicht im Score. Stooq hat den CSV-Abruf abgelehnt; US-Kurse kommen von Nasdaq, der Vergleich vom ETF SPY.

- PLTR 62,8, orange, 7 Kriterien
- OSS 41,9, rot, 6 Kriterien (C_08 zusätzlich leer)
- AMZN 57,0, orange, 7 Kriterien
- TSLA 30,1, rot, 7 Kriterien
- HIMS 49,0, rot, 6 Kriterien (C_08 zusätzlich leer)
- LMND 33,3, rot, 7 Kriterien
- IBRX 37,2, rot, 7 Kriterien
- MU 80,8, grün, 7 Kriterien
- IREN 33,7, rot, 7 Kriterien
- GRAB 46,2, rot, 6 Kriterien (C_08 zusätzlich leer)
- SIVE „wenig Daten“ (1 Kriterium). Kurse von der Yahoo-Finance-Chart-API, Symbol SIVE.ST, 1257 Tage, Quelle in `data/SIVE/score.json`. Keine SEC-Daten. Fundamentalkennzahlen und C_06 sind „wenig Daten“. C_07 steht bei 3,3; der Markt-Teilscore daraus ist 33,0. Die Headline bleibt „wenig Daten“, weil weniger als drei Kriterien Daten haben.
- SPCX „wenig Daten“ (1 Kriterium), vorher 0,0. 87 Kurse, keine 12-1-Rendite und keine 200-Tage-Linie, C_07 ist „wenig Daten“. C_05 ist 0,0 aus dem Insider-Kaufanteil 0 %. Das zieht die Headline nicht mehr auf 0.
- PYPL 67,5, orange, 7 Kriterien

`sw.js` und `service-worker.js` ersetzen die frühere Vite-PWA (`registerSW.js`, Scope `/`). Install ruft `skipWaiting`. Activate löscht alle Caches, hebt die Registrierung auf und lädt offene Fenster neu. `vercel.json` setzt `Cache-Control: no-cache`. `index.html` meldet vorhandene Service Worker ab.

### Phase 1, echte Kennzahlen

`scripts/build_scores.py` füllt `data/<TICKER>/score.json` aus SEC EDGAR (companyfacts, submissions, eine begrenzte Form-4-Stichprobe) und Tageskursen. Schwellen stehen in `config/scoring.json`. Der Aktien-Score ist 0–100, C_07 hat Gewicht 0,5. Ab 70 grün, 50–69 orange, darunter rot. Jede Teilkennzahl trägt Wert, Punkte, Quelle, Abrufdatum und das Datum des Datenpunkts. Fehlt eine Zahl, bleibt das Feld leer und die Seite zeigt „unbekannt“.

Stooq hat den CSV-Abruf nach dem Browser-Check mit „Access denied“ beantwortet. Die Kurse in diesem Lauf kommen von der öffentlichen Nasdaq-Historie, der S&P-500-Vergleich vom ETF SPY; beides steht in Quelle und Datenqualität. Ohne `FMP_API_KEY` und `FINNHUB_API_KEY` bleibt C_06 leer. SIVE hat keine SEC-Daten, und Stooq hat auch dort keinen Kurs geliefert: Score leer, Hinweis auf die dünne Basis.

Erster Lauf, Amazon: Score 57,0 (orange). Kriterien C_01 bis C_08: 6,3 / 5,7 / 5,2 / 5,0 / 8,1 / unbekannt / 7,9 / 2,8. Stand der Daten 15.10.2026. Der Workflow `.github/workflows/scores.yml` wiederholt das montags und donnerstags um 05:00 UTC und auf Zuruf.

### sidequest, öffentlich

Wortmarke im Kopf ist „sidequest“, das bisherige Logo bleibt. Titel und Meta folgen der Marke. StaticCrypt und das Passwort entfallen; die Seite ist die unverschlüsselte statische Datei. Sichtbar sind nur PLTR, OSS, AMZN, TSLA, HIMS, LMND, IBRX, MU, IREN, GRAB, SIVE, SPCX und PYPL, ohne Beträge, Gewichte oder Kaufdaten. Die Hinweise „Ich halte diese Aktien selbst.“ und „Keine Anlageberatung.“ stehen im Kopf. Jede Analyse nennt „Stand der Daten“; ohne Datum in der Quelle steht „unbekannt“. Daten kommen aus `data/<TICKER>/score.json` (Kriterium, Teilkennzahl, Wert, Quelle, Abrufdatum). C_01 bis C_08 sind in der Übersicht zugeklappt und per Klick zu öffnen. Deployment als statische Vercel-Seite mit `cleanUrls` und Basispfad `/`. Relative Pfade lassen GitHub Pages bis zum Umschalten weiterlaufen. Die Werte aus der verschlüsselten Seite waren ohne Passwort nicht lesbar und wurden nicht erfunden.

## 2026-10-13

### v3.2

Umzug aus tea-sommelier-poc. Aktien-Score aus 8 Kriterien je 0–10, gleich gewichtet; Rolle (Satellit, Tenbagger, Compounder, Dividenden-Aktie) als Ergebnis „passt am besten als“; ROIC-Deckel und -Punkte auf 2-Jahres-Schnitt; passwortgeschützt (StaticCrypt).

## 2026-10-09

### v2

Design: Defense-Tech-Look hell/dunkel.

### v1

Erster Prototyp.
