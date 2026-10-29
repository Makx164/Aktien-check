# sidequest

Öffentliche Seite der Marke sidequest. Pro Aktie sechs Kennzahlen mit Quelle und Stand, plus ein These-Satz. Keine Scores, keine Ampeln, keine Kriterien C_01 bis C_08. Keine Depotbeträge und keine Gewichte.

Keine Anlageberatung und keine Kauf- oder Verkaufsempfehlung. Die Kennzahlen sind öffentliche Unternehmensdaten mit Quelle und Stand. Sie können unvollständig oder veraltet sein und ersetzen keine eigene Prüfung.

Zieladresse: https://yoursidequest.org (Vercel, Basispfad `/`). Ein Merge auf `main` geht live. Vorher nur eine Vorschau.

GitHub Pages unter https://makx164.github.io/Aktien-check/ leitet auf yoursidequest.org. Die Seiten liegen in `docs/` und werden von Vercel nicht ausgeliefert (`.vercelignore`).

## Was die Seite zeigt

Nur diese Ticker, in dieser Reihenfolge: PLTR, OSS, AMZN, TSLA, HIMS, LMND, IBRX, MU, IREN, GRAB.

Nicht dabei: PYPL, SIVE, SPCX.

Die Startseite ist ein Kachelraster. Eine Kachel zeigt Ticker, Namen und den Stand der Daten. Keine Leitkennzahl, kein Score. Ein Klick öffnet `#/TICKER`, zum Beispiel `#/PLTR`.

Die Aktie zeigt den Schlusskurs, den These-Satz, die sechs Kennzahlen und, soweit vorhanden, Sitz, Mitarbeiter und Börsenwert. Fehlt ein Wert, steht „—“. Nichts wird geschätzt. Der These-Satz ist „These folgt“, solange kein Satz aus dem Depotbericht eingetragen ist.

| Kennzahl | Definition |
| --- | --- |
| Umsatzwachstum y/y | Umsatz des letzten berichteten Quartals gegen das Vorjahresquartal |
| Bruttomarge | Bruttogewinn ÷ Umsatz, TTM aus vier Quartalen, sonst das letzte Quartal oder das Yahoo-Feld `grossMargins` |
| Free Cashflow | Operating Cash Flow − Capital Expenditure, TTM, GAAP. Kein adjusted FCF |
| Cash vs. Schulden | Cash einschließlich kurzfristiger Anlagen neben den Schulden, letzte Bilanz |
| Verwässerung | Basic Average Shares des letzten Quartals gegen das Vorjahr, sonst diluted |
| Nächster Zahlentermin | Nächstes Datum aus dem Yahoo-Kalender, Uhrzeit in Europe/Berlin, wenn sie vorliegt |

Primärquelle ist Yahoo Finance über `yfinance`. Liegt bei einem US-Titel in SEC companyfacts ein neueres Quartal, kommt das Umsatzwachstum dorther. Stimmen Yahoo und SEC überein, nennt die Quelle beide. Finnhub wird nicht benutzt. `FINNHUB_API_KEY` bleibt ungenutzt im Repo, bis er gelöscht wird.

## Daten

Pro Ticker eine Datei `data/<TICKER>/metrics.json`. Die Ticker stehen in `config/tickers.json`. `config/profiles.json` hält alte Firmenbeschreibungen und wird von der Seite nicht geladen.

Der Browser ruft Yahoo und SEC nicht auf. Er lädt nur die schon geschriebenen Dateien.

## Monatlich aktualisieren

Am 13. mit dem Depotbericht, oder zwischendurch von Hand. These nur ändern, wenn sich die Story ändert. Ein leerer oder fehlender Satz bleibt „These folgt“.

Lokal, ohne neue Schlüssel:

```bash
pip install -r requirements-metrics.txt
python3 scripts/build_metrics.py
```

Danach die geänderten `data/<TICKER>/metrics.json` auf einen Branch legen und eine Vorschau-PR öffnen. Nicht direkt auf `main` committen.

Alternativ in GitHub: Actions → **metrics** → Run workflow, Branch `main`. Der Lauf schreibt die Dateien auf einen neuen Branch `metrics/JJJJ-MM-TT-<Lauf>` und öffnet eine Pull Request. Er pusht nicht auf `main`. Es ist kein neues Secret nötig. `FINNHUB_API_KEY` wird nicht gelesen.

## Score-Lauf

`.github/workflows/scores.yml` ist entfernt. Montag und Donnerstag laufen keine Scores mehr. `python3 scripts/build_scores.py` bricht ab. Der alte Rechenkern unter `scoring/` bleibt im Repo und hängt nicht an der Seite. Die Sicherungen davor liegen auf dem Tag `backup/vor-kennzahlenseite-2026-10-29`.

## Deployment

Statische Dateien, kein Build. `vercel.json` setzt `cleanUrls`. Vercel nimmt das Repository-Root.

`sw.js` und `service-worker.js` melden einen alten Service Worker ab. `index.html` tut dasselbe.
