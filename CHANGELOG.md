# Changelog

## 2026-10-21

### Skala 0–10 und Firmenprofil

Der Aktien-Score ist der ungewichtete Mittelwert der sichtbaren Kriterien mit Daten, auf der Skala 0–10. Gewichte gehen nicht in den Schnitt ein. C_09 bleibt bei Gewicht 0 ausgeblendet. Ampel: ab 7 grün, ab 5 orange, darunter rot. Unter dem Score steht eine kurze Firmenbeschreibung aus `config/profiles.json` und, soweit Yahoo sie liefert, Sitz, Mitarbeiter und Börsenwert. Quelle und Abrufdatum stehen im Quellenblock.

### Impressum und Datenschutz

Im Footer, neben dem roten Hinweis, stehen die Links Impressum und Datenschutz. Die Seiten beschreiben das Angebot und die Verarbeitung, die der Browser tatsächlich auslöst. Der rote Hinweis sagt, dass die Scores regelbasierte Einschätzungen aus öffentlichen Daten sind und keine Kauf- oder Verkaufsempfehlung.

## 2026-10-20

### Runde 3, ein Gewichtssatz und nachvollziehbare Metriken

Alle Titel rechnen mit dem Satelliten-Gewichtssatz. Die Dividendenrolle und C_09 sind über `roles.dividendEnabled` aus; der Code bleibt. Die Seite zeigt keine Rolle und keine Gewichte. Im Kopf der Analyse steht, wie der Aktien-Score zustande kommt. Jede aufgeklappte Metrik zeigt Wert, Skala, Punkte, die tatsächlich genutzte Quelle mit Link und Stichtag oder Abrufdatum. Darunter steht, aus wie vielen Metriken mit Daten das Kriterium besteht. Die 12-1-Rendite zeigt ihre Stufen (≥ 30 Pp = 10, 0 Pp = 5, ≤ -50 Pp = 0). Die Bewertung zeigt die Schwelle der genutzten Formel, PEG oder (EV/Umsatz) / Umsatzwachstum.

C_02 enthält das Umsatzwachstum der letzten vergleichbaren Periode (TTM, sonst Halbjahr oder Quartal), das letzte Quartal gegen das Vorjahresquartal und die Analystenschätzung fürs nächste Jahr. Eine Metrik ohne Daten zählt nicht als 0. Ein abgeleitetes Umsatzquartal, das negativ würde, weil eine spätere Jahreszahl unter der älteren Neunmonatszahl liegt, fällt weg. Bilanz und Cashflow nutzen den letzten 10-Q- oder TTM-Stichtag. Fehlt der Wert oder ist er älter, springt der Lauf auf Yahoo und dann auf Nasdaq. Explizit gemeldete Finanzschulden von 0 bleiben 0. Ist das Schulden-Tag leer und die Ausweichquelle nahe 0, ist Netto-Cash gleich Cash. Netto-Cash gibt bei Nettoverschuldung/EBITDA 10 Punkte.

### Runde 2, Datenstand und Rollen

Der Score ist der Score der zugeordneten Rolle. Satellit ist der Standard, Dividendenaktie nur ab 2,5 % Rendite und fünf Jahren ohne Kürzung. Die Gewichte stehen in `config/scoring.json` und in `score.json`. Die Seite zeigt Rolle und Gewichte. Die Wortmarke ist nur „sidequest“. Unten steht: „Keine Anlageberatung. Die Bewertungen sind subjektive Interpretationen und spiegeln keine tatsächlichen Werte wider.“

Margen, Vorjahreswachstum, Cashflow, Bewertung und Verschuldung nutzen die letzten vier Quartale aus companyfacts, sonst den Vorjahresvergleich aus dem 10-Q. CAGRs bleiben Jahreswerte. Fehlt ein SEC-Wert, füllen Yahoo Finance und danach die Nasdaq-API nur diese Lücke, mit Quelle und Abrufzeit. Eine fehlende Zahl bleibt „wenig Daten“. ROIC mittelt nur zwei aufeinanderfolgende Jahre. Die Bruttomarge liest auch das IFRS-Feld `CostOfSales`. Finanzschulden sind verzinsliche Schulden ohne Leasing. Ein CAGR mit negativer Basis fällt auf zwei Jahre zurück, wenn beide Werte positiv sind. C_01 misst ROIC, Bruttomarge, ihren Trend, den Margenfortschritt aller Firmen und Netto-Cash oder Cash-Reichweite. Die Reichweite über 36 Monate steht als „> 36 Monate“. C_07 ist stückweise: 12-1 von −50 Pp bis +30 Pp, unter der 200-Tage-Linie 5 Punkte, Abstand zum Hoch nach der Fondsmanager-Regel, Untergrenze 3 solange C_02 mindestens 5 ist. C_06 ist wieder im Score, Quelle Yahoo, sonst Nasdaq. Unter drei Analysten zählt es nicht. Phase 1 nennt SEC EDGAR, Yahoo und Nasdaq. Für eine Bezahlversion sind lizenzierte Quellen nötig.

### Fachlicher Check, Scoring

Gewichte in `config/scoring.json`: C_02 = 1,5, C_07 und C_08 = 0,5. Verlustbringer bekommen bei C_02 die Veränderung der operativen Marge zum Vorjahr (−5 Pp = 0, +10 Pp = 10). Negativer Free Cashflow ersetzt die Eigenkapitalrendite durch die Cash Runway (unter 12 Monaten = 0, ab 36 Monaten = 10). Lemonade ist als Versicherer markiert: Current Ratio und Nettoverschuldung/EBITDA sind „nicht anwendbar“. Die Free-Cashflow-Rendite ist bei Verlustbringern „nicht anwendbar“. Fehlt das PEG bei einem Gewinnunternehmen, weil das EPS-Wachstum nicht positiv ist, steht (EV/Umsatz) / Umsatzwachstum (10 unter 0,2, 0 über 1,0); schrumpft das letzte Jahr, zählt der 3-Jahres-CAGR.

Form-4-Feld `aff10b5One` kennzeichnet 10b5-1-Planverkäufe. Sie zählen mit einem Viertel, offene Käufe (Code P) doppelt, mehrere verschiedene Käufer mit Bonus. Netto-Verkauf unter 0,1 % der Marktkapitalisierung ist neutral. C_06 ohne API-Schlüssel ist „ausgeblendet, zählt nicht“. SIVE zeigt „keine US-Meldepflicht“, das Momentum gegen OMXS30. SPCX zeigt „zu kurze Historie“, wo die Reihe strukturell fehlt. Die Übersicht heißt „Übersicht Aktienportfolio“.

## 2026-10-16

### Stand der Daten und Kachelfarbe

Der Stand ist das jüngste Kursdatum oder Berichtsende. Bei SPCX fehlte das Kursdatum in `asOf`, deshalb zeigte die Kachel das Insidergeschäft vom 22.09.2026 (zuvor 02.04.2026). Der Nasdaq-Schluss vom 15.10.2026 ist der Stand. Ohne Kurs- und Berichtsdatum steht „wenig Daten“. Der Ticker auf der Kachel ist neutral, die Ampelfarbe bleibt am Score.

### Kurszeile und C_05

Die Kurszeile zeigt den letzten Schlusskurs und das Datum aus der Kursquelle (`price` in `score.json`). C_05 zählt Insider-Anteil, den Sechs-Monats-Saldo offener Marktgeschäfte und den institutionellen Anteil. Schwellen und Beträge stehen in der Datei. Eine Form-4 mit Kennzeichen 10b5-1 wirft die Käufe und Verkäufe nicht mehr weg.

### Kacheln und Analyse unter #/TICKER

Die Startseite zeigt 13 Kacheln: Ticker, Name, Score 0–100 in der Ampelfarbe oder „wenig Daten“, und „Stand der Daten“. Keine Beträge, keine Gewichte. Ein Klick öffnet die Analyse unter `#/TICKER`, zum Beispiel `#/PLTR`. Wortmarke, Logo und „Alle Positionen“ führen zurück. Unten steht der Hinweis, dass es keine Anlageberatung ist.

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

Wortmarke im Kopf ist „sidequest“, das bisherige Logo bleibt. Titel und Meta folgen der Marke. StaticCrypt und das Passwort entfallen; die Seite ist die unverschlüsselte statische Datei. Sichtbar sind nur PLTR, OSS, AMZN, TSLA, HIMS, LMND, IBRX, MU, IREN, GRAB, SIVE, SPCX und PYPL, ohne Beträge, Gewichte oder Kaufdaten. Unten steht der Hinweis, dass es keine Anlageberatung ist. Jede Analyse nennt „Stand der Daten“; ohne Datum in der Quelle steht „unbekannt“. Daten kommen aus `data/<TICKER>/score.json` (Kriterium, Teilkennzahl, Wert, Quelle, Abrufdatum). C_01 bis C_08 sind in der Übersicht zugeklappt und per Klick zu öffnen. Deployment als statische Vercel-Seite mit `cleanUrls` und Basispfad `/`. Relative Pfade lassen GitHub Pages bis zum Umschalten weiterlaufen. Die Werte aus der verschlüsselten Seite waren ohne Passwort nicht lesbar und wurden nicht erfunden.

## 2026-10-13

### v3.2

Umzug aus tea-sommelier-poc. Aktien-Score aus 8 Kriterien je 0–10, gleich gewichtet; Rolle (Satellit, Tenbagger, Compounder, Dividenden-Aktie) als Ergebnis „passt am besten als“; ROIC-Deckel und -Punkte auf 2-Jahres-Schnitt; passwortgeschützt (StaticCrypt).

## 2026-10-09

### v2

Design: Defense-Tech-Look hell/dunkel.

### v1

Erster Prototyp.
