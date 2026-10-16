# Changelog

## 2026-10-16

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
