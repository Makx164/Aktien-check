# sidequest

Öffentliche Seite der Marke sidequest. Der bisherige Aktien-Check (Projekt 2) bleibt im Aufbau: acht Kriterien je 0–10, gleich gewichtet, Ergebnis „passt am besten als“ Satellit, Tenbagger, Compounder oder Dividenden-Aktie. ROIC-Deckel und ROIC-Punkte gelten für den Zweijahres-Schnitt.

Ich halte diese Aktien selbst. Keine Anlageberatung.

Zieladresse: https://yoursidequest.org (Vercel, Basispfad `/`).

Bis zum Umschalten bleibt GitHub Pages unter https://makx164.github.io/Aktien-check/ erreichbar. Daten und Schrift werden relativ zur Seite geladen, nicht unter dem festen Präfix `/Aktien-check/`. Dadurch funktioniert dieselbe Datei auf beiden Hosts.

## Was die Seite zeigt

Nur diese Ticker, in dieser Reihenfolge: PLTR, OSS, AMZN, TSLA, HIMS, LMND, IBRX, MU, IREN, GRAB, SIVE, SPCX, PYPL.

Keine Beträge, Gewichte oder Kaufdaten. Oben stehen die Hinweise „Ich halte diese Aktien selbst.“ und „Keine Anlageberatung.“ Je Analyse steht „Stand der Daten: …“. Fehlt ein Datum in der Quelldatei, steht dort „unbekannt“.

In der Übersicht sind die Kriterien C_01 bis C_08 zugeklappt. Ein Klick öffnet Teilkennzahlen, Werte, Quellenlink und Abrufdatum.

## Daten

Pro Ticker eine Datei `data/<TICKER>/score.json`. Die Seite lädt nur die Liste oben und ignoriert Betrag, Gewicht und Kaufdatum, falls sie in einer Datei stehen.

```json
{
  "ticker": "PLTR",
  "name": "Palantir Technologies",
  "asOf": "2026-10-13",
  "role": "Compounder",
  "score": 7,
  "criteria": [
    {
      "id": "C_01",
      "label": "Bezeichnung aus der Quelle",
      "score": 7,
      "metrics": [
        {
          "label": "Teilkennzahl",
          "value": "Wert aus der Quelle",
          "sourceLabel": "Name der Quelle",
          "sourceUrl": "https://quelle.example/bericht",
          "fetchedAt": "2026-10-13"
        }
      ]
    }
  ]
}
```

Das Beispiel zeigt nur die Felder. Die ausgelieferten Dateien haben `null` und leere `metrics`, siehe unten. `asOf` und `fetchedAt` kommen aus der Quelle, als `JJJJ-MM-TT` oder als schon formuliertes Datum. Die Seite erfindet kein Datum. `sourceUrl` wird nur verlinkt, wenn es mit `http://` oder `https://` beginnt. `role` ist Satellit, Tenbagger, Compounder oder Dividenden-Aktie. `score` ist 0–10. Es gibt acht Kriterien, C_01 bis C_08.

Der bisherige Inhalt lag nur in der StaticCrypt-Datei. Das Passwort steht nicht im Repository, in den erreichbaren Notizen oder in den früheren Agent-Läufen dieses Repos, und ein Abgleich mit üblichen Passwörtern hat die Datei nicht geöffnet. Die mitgelieferten `score.json` enthalten deshalb die acht Kriterien ohne Werte. Die Seite zeigt dafür „unbekannt“. Firmennamen sind die öffentlichen Bezeichnungen der Ticker, keine Schätzungen von Kennzahlen.

## Deployment

Statische Dateien, kein Build. `vercel.json` setzt `cleanUrls`. Vercel nimmt das Repository-Root. GitHub Pages weiter aus `main` / Root; `.nojekyll` bleibt, damit Jekyll die Seite nicht anfasst.
