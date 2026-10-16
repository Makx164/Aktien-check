# Changelog

## 2026-10-16

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
