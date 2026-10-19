# Gold-Bot (MT5) – Anleitung

Nachbau des Gold-Bots aus dem „Tagebuch: Million“-Reel: ein Expert Advisor für
MetaTrader 5, der Gold (XAUUSD oder XAUEUR) auf dem **M5-Chart** handelt und – wie im
Video – standardmäßig **nur Käufe** eröffnet.

> **Nur Demo. Keine Finanzberatung.** Erst wochenlang auf einem Demokonto laufen
> lassen. Ein Backtest mit „Keine Latenz, ideale Ausführung“ (wie im Reel) sieht
> immer besser aus als die Realität.

## Dateien

| Datei | Zweck |
|---|---|
| `GoldTagebuchEA.mq5` | Der Bot (Quellcode für MetaTrader 5) |
| `backtest.py` | Python-Backtest mit denselben Regeln, zum Prüfen von Einstellungen |
| `ANLEITUNG.md` | Diese Anleitung |

## Strategie

1. **Trend (H1):** Der letzte geschlossene H1-Schlusskurs liegt über der EMA 200.
2. **Trend (M5):** EMA 21 liegt über EMA 50.
3. **Einstieg:** Die letzte M5-Kerze ist in die EMA 21 zurückgelaufen (Tief ≤ EMA 21),
   hat darüber und grün geschlossen, und der RSI(14) liegt zwischen 45 und 70.
4. **Ausstieg:** Stop-Loss 1,5 × ATR, Take-Profit 2,0 × ATR. Ab 1 ATR Gewinn wird der
   Stop auf Einstand gezogen und danach mit 1,2 × ATR nachgezogen (Trailing).

Mit `InpAllowShorts = true` handelt der Bot das Spiegelbild auch im Abwärtstrend.

## Schutzmechanismen

- **Risiko pro Trade** in % vom Kapital (Standard 0,5 %) – die Lotgröße wird aus dem
  Abstand zum Stop berechnet und nie über das Minimum aufgerundet.
- **Max. Positionen** (2) und **Pause** zwischen Einstiegen (2 Kerzen).
- **Handelszeit** 01–22 Uhr Serverzeit, **max. Spread** 60 Punkte.
- **Tagesverlust-Limit** 3 %: danach keine neuen Trades bis zum nächsten Tag.
- **Max. Drawdown** 15 %: der Bot schließt alles und hört auf (Neustart nötig).
- **Freitag ab 21 Uhr** werden alle Positionen geschlossen (kein Wochenend-Gap).

## Installation

1. MetaTrader 5 öffnen → **Datei → Dateiordner öffnen** → `MQL5/Experts/`.
2. `GoldTagebuchEA.mq5` dort hineinkopieren.
3. MetaEditor öffnen (F4), Datei öffnen und **Kompilieren** (F7). Es darf kein Fehler
   erscheinen.
4. In MT5 einen **XAUUSD- (oder XAUEUR-) Chart auf M5** öffnen.
5. Im Navigator unter *Expert Advisors* den Bot auf den Chart ziehen.
6. Im Tab *Allgemein* **Algo Trading erlauben** aktivieren, oben in der Leiste den
   Button **Algo Trading** einschalten.
7. Oben links erscheint das Status-Panel (Kontostand, Tages-P&L, Drawdown, Status).

## Backtest im Strategietester

**Ansicht → Strategietester** (Strg+R):

| Feld | Wert |
|---|---|
| Expert | `GoldTagebuchEA` |
| Symbol / Zeitrahmen | XAUUSD, M5 |
| Datum | z. B. 2026.01.01 – heute |
| Vorwärtstest | Nein (später: 1/3, um Überanpassung zu prüfen) |
| Verzögerung | **Zufällige Verzögerung** (realistischer als „ideale Ausführung“) |
| Modellierung | Jeder Tick anhand realer Ticks |
| Einlage | 100000 EUR, Hebel 1:100 |

Wichtig sind nicht nur der Gewinn, sondern **Max. Drawdown**, **Profitfaktor**
(> 1,3 ist ordentlich) und ob der Vorwärtstest ähnlich aussieht wie der Rückwärtstest.

## Backtest in Python

Daten in MT5 exportieren: **Ansicht → Symbole → Balken**, XAUUSD/M5 wählen,
*Anfragen*, dann *Balken exportieren* (CSV). Danach im Repo:

```bash
python -m gold_bot.backtest XAUUSD_M5.csv --risk 0.5
python -m gold_bot.backtest XAUUSD_M5.csv --shorts --trades-out trades.csv
python -m gold_bot.backtest --yahoo GC=F          # ~60 Tage Gold-Futures, 5 Minuten
```

Der Python-Backtest ist bewusst pessimistisch: Einstieg zum Eröffnungskurs der
nächsten Kerze plus Spread, und wenn eine Kerze Stop und Ziel berührt, zählt der Stop.
Swap und Kommission werden nicht berücksichtigt.

## Einstellungen (Auswahl)

| Parameter | Standard | Bedeutung |
|---|---|---|
| `InpRiskPercent` | 0.5 | Risiko pro Trade in % |
| `InpFixedLots` | 0 | Feste Lotgröße statt Risiko-% (0 = aus) |
| `InpSLATR` / `InpTPATR` | 1.5 / 2.0 | Stop / Ziel in ATR |
| `InpBreakEvenATR` / `InpTrailATR` | 1.0 / 1.2 | Einstand / Trailing in ATR |
| `InpMaxPositions` | 2 | Gleichzeitige Positionen |
| `InpAllowShorts` | false | Auch Verkäufe |
| `InpStartHour` / `InpEndHour` | 1 / 22 | Handelszeit (Serverzeit) |
| `InpMagic` | 102102 | Kennung – bei mehreren Charts unterschiedlich wählen |

## Ehrliche Hinweise

- Die Trades im Reel sind Käufe in einem starken Gold-Aufwärtstrend. Jeder
  Long-only-Bot sieht in so einer Phase gut aus; im Seitwärts- oder Abwärtsmarkt
  verliert er. Deshalb: Trendfilter, Tageslimit und Drawdown-Stopp nicht abschalten.
- Ein Sharpe-Ratio von über 4 im Backtest deutet meist auf Überanpassung oder
  ideale Ausführung hin, nicht auf einen echten Vorteil.
- Erst nach mehreren Wochen Demo mit stabilen Ergebnissen überhaupt über echtes Geld
  nachdenken – und dann nur mit Geld, dessen Verlust du verkraften kannst.
