"""Backtest for the gold EA (GoldTagebuchEA.mq5) on M5 bars.

Mirrors the EA's rules so a parameter set can be checked before it runs in
MetaTrader. Prices are bid prices, as MetaTrader charts and exports them. Entries
fill at the next bar's open (longs pay the spread), and when one bar touches both
the stop and the target the stop is taken first, so the result errs on the
pessimistic side. Swap and commission are not modelled.

Usage:
    python -m gold_bot.backtest XAUUSD_M5.csv          # MetaTrader CSV export
    python -m gold_bot.backtest --yahoo GC=F           # last ~60 days of 5m bars
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass
class Params:
    trend_tf: str = "1h"
    trend_ema: int = 200
    fast_ema: int = 21
    slow_ema: int = 50
    rsi_period: int = 14
    rsi_min: float = 45.0
    rsi_max: float = 70.0
    allow_shorts: bool = False
    risk_percent: float = 0.5
    fixed_lots: float = 0.0
    atr_period: int = 14
    sl_atr: float = 1.5
    tp_atr: float = 2.0
    breakeven_atr: float = 1.0
    trail_atr: float = 1.2
    max_positions: int = 2
    cooldown_bars: int = 2
    daily_loss_percent: float = 3.0
    max_drawdown_percent: float = 15.0
    start_hour: int = 1
    end_hour: int = 22
    friday_close_hour: int = 21
    spread: float = 0.30  # price units: 30 cents on XAUUSD
    contract_size: float = 100.0  # ounces per lot
    leverage: float = 100.0
    lot_step: float = 0.01
    min_lot: float = 0.01
    max_lot: float = 100.0
    deposit: float = 100_000.0


@dataclass
class Trade:
    direction: int  # +1 long, -1 short
    lots: float
    entry_time: pd.Timestamp
    entry: float
    sl: float
    tp: float
    exit_time: pd.Timestamp | None = None
    exit: float | None = None
    reason: str = ""

    @property
    def open_risk_moved(self) -> bool:
        return (self.sl - self.entry) * self.direction >= 0

    def pnl(self, price: float, contract_size: float) -> float:
        return (price - self.entry) * self.direction * self.lots * contract_size


def load_bars(path: str | Path) -> pd.DataFrame:
    """Read M5 bars from a MetaTrader export or a plain OHLC CSV."""
    raw = pd.read_csv(path, sep=None, engine="python")
    raw.columns = [c.strip().strip("<>").lower() for c in raw.columns]
    if "date" in raw.columns and "time" in raw.columns:
        stamp = pd.to_datetime(raw["date"].astype(str) + " " + raw["time"].astype(str))
    else:
        col = next(c for c in ("time", "datetime", "date", "timestamp") if c in raw.columns)
        stamp = pd.to_datetime(raw[col])
    bars = raw[["open", "high", "low", "close"]].astype(float)
    bars.index = pd.DatetimeIndex(stamp, name="time")
    return bars.sort_index()


def load_yahoo(symbol: str = "GC=F", period: str = "60d") -> pd.DataFrame:
    """Fetch 5-minute bars from Yahoo Finance (it serves about 60 days of them)."""
    import yfinance as yf

    data = yf.download(symbol, period=period, interval="5m", progress=False, auto_adjust=False)
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)
    bars = data[["Open", "High", "Low", "Close"]].dropna()
    if bars.empty:
        raise SystemExit(f"Yahoo Finance returned no 5m bars for {symbol}")
    bars.columns = ["open", "high", "low", "close"]
    index = bars.index if bars.index.tz is None else bars.index.tz_convert(None)
    bars.index = pd.DatetimeIndex(index, name="time")
    return bars


def _rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    return 100 - 100 / (1 + gain / loss.replace(0, np.nan))


def add_signals(bars: pd.DataFrame, p: Params) -> pd.DataFrame:
    """Indicators and the entry signal of each closed bar (+1 buy, -1 sell, 0 none).

    The higher-timeframe trend only uses trend bars that had closed by the end of
    the signal bar, as the EA does, so no signal sees a later price.
    """
    df = bars.copy()
    close = df["close"]
    df["fast"] = close.ewm(span=p.fast_ema, adjust=False).mean()
    df["slow"] = close.ewm(span=p.slow_ema, adjust=False).mean()
    df["rsi"] = _rsi(close, p.rsi_period)
    prev_close = close.shift()
    true_range = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    df["atr"] = true_range.rolling(p.atr_period).mean()

    step = df.index.to_series().diff().median()
    trend = close.resample(p.trend_tf).last().dropna().to_frame("trend_close")
    trend["trend_ema"] = trend["trend_close"].ewm(span=p.trend_ema, adjust=False).mean()
    trend["closed_at"] = trend.index + pd.Timedelta(p.trend_tf)
    decided_at = (df.index + step).to_frame(index=False, name="decided_at")
    merged = pd.merge_asof(
        decided_at, trend.reset_index(drop=True), left_on="decided_at", right_on="closed_at"
    )
    df["trend_close"] = merged["trend_close"].to_numpy()
    df["trend_ema"] = merged["trend_ema"].to_numpy()

    candle_up = df["close"] > df["open"]
    long_signal = (
        (df["trend_close"] > df["trend_ema"])
        & (df["fast"] > df["slow"])
        & (df["low"] <= df["fast"])
        & (df["close"] > df["fast"])
        & candle_up
        & df["rsi"].between(p.rsi_min, p.rsi_max)
    )
    short_signal = (
        (df["trend_close"] < df["trend_ema"])
        & (df["fast"] < df["slow"])
        & (df["high"] >= df["fast"])
        & (df["close"] < df["fast"])
        & ~candle_up
        & df["rsi"].between(100 - p.rsi_max, 100 - p.rsi_min)
    )
    df["signal"] = 0
    df.loc[long_signal, "signal"] = 1
    if p.allow_shorts:
        df.loc[short_signal, "signal"] = -1
    df.loc[df["atr"].isna() | df["trend_ema"].isna(), "signal"] = 0
    return df


def lot_size(equity: float, free_margin: float, sl_distance: float, price: float, p: Params) -> float:
    """Lots that lose ``risk_percent`` of equity at the stop, within free margin."""
    if p.fixed_lots > 0:
        lots = p.fixed_lots
    elif sl_distance <= 0:
        return 0.0
    else:
        lots = equity * p.risk_percent / 100 / (sl_distance * p.contract_size)
    margin_per_lot = p.contract_size * price / p.leverage
    lots = min(lots, 0.9 * free_margin / margin_per_lot, p.max_lot)
    lots = math.floor(lots / p.lot_step + 1e-9) * p.lot_step
    return round(lots, 8) if lots >= p.min_lot else 0.0


def _in_session(ts: pd.Timestamp, p: Params) -> bool:
    if p.start_hour < p.end_hour:
        return p.start_hour <= ts.hour < p.end_hour
    return ts.hour >= p.start_hour or ts.hour < p.end_hour


def run(bars: pd.DataFrame, p: Params | None = None) -> tuple[list[Trade], pd.Series]:
    """Simulate the EA; returns the closed trades and the equity at each bar's close."""
    p = p or Params()
    df = add_signals(bars, p)
    times = df.index
    o, h, lo, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    atr, signal = df["atr"].to_numpy(), df["signal"].to_numpy()

    balance = p.deposit
    peak = p.deposit
    halted = False
    day, day_start = None, p.deposit
    last_entry = -(10**9)
    open_trades: list[Trade] = []
    closed: list[Trade] = []
    equity = np.empty(len(df))

    def close(trade: Trade, price: float, when: pd.Timestamp, reason: str) -> None:
        nonlocal balance
        trade.exit, trade.exit_time, trade.reason = price, when, reason
        balance += trade.pnl(price, p.contract_size)
        open_trades.remove(trade)
        closed.append(trade)

    def floating(price: float) -> float:
        return sum(t.pnl(price if t.direction > 0 else price + p.spread, p.contract_size)
                   for t in open_trades)

    for i, ts in enumerate(times):
        if day != ts.date():
            day, day_start = ts.date(), balance + floating(o[i])

        if ts.weekday() == 4 and ts.hour >= p.friday_close_hour:
            for t in list(open_trades):
                close(t, o[i] if t.direction > 0 else o[i] + p.spread, ts, "friday")

        # Open at this bar's open on the previous bar's signal.
        direction = signal[i - 1] if i > 0 else 0
        current = balance + floating(o[i])
        if (
            direction
            and not halted
            and _in_session(ts, p)
            and len(open_trades) < p.max_positions
            and i - last_entry > p.cooldown_bars
            and current > day_start * (1 - p.daily_loss_percent / 100)
            and not (ts.weekday() == 4 and ts.hour >= p.friday_close_hour)
        ):
            entry = o[i] + p.spread if direction > 0 else o[i]
            sl_dist, tp_dist = p.sl_atr * atr[i - 1], p.tp_atr * atr[i - 1]
            used = sum(t.lots * p.contract_size * t.entry / p.leverage for t in open_trades)
            lots = lot_size(current, current - used, sl_dist, entry, p)
            if lots > 0:
                open_trades.append(
                    Trade(direction, lots, ts, entry,
                          entry - direction * sl_dist, entry + direction * tp_dist)
                )
                last_entry = i

        # Exits inside the bar; the stop wins when both levels were touched.
        for t in list(open_trades):
            if t.direction > 0:
                if lo[i] <= t.sl:
                    close(t, min(t.sl, o[i]), ts, "sl" if not t.open_risk_moved else "trail")
                elif h[i] >= t.tp:
                    close(t, max(t.tp, o[i]), ts, "tp")
            else:
                if h[i] + p.spread >= t.sl:
                    close(t, max(t.sl, o[i] + p.spread), ts,
                          "sl" if not t.open_risk_moved else "trail")
                elif lo[i] + p.spread <= t.tp:
                    close(t, min(t.tp, o[i] + p.spread), ts, "tp")

        # Break-even and trailing stop from the close, as the EA does on its ticks.
        for t in open_trades:
            price = c[i] if t.direction > 0 else c[i] + p.spread
            gained = (price - t.entry) * t.direction
            if np.isnan(atr[i]) or gained < p.breakeven_atr * atr[i]:
                continue
            target = max(t.entry, price - p.trail_atr * atr[i]) if t.direction > 0 else \
                min(t.entry, price + p.trail_atr * atr[i])
            if (target - t.sl) * t.direction > 0:
                t.sl = target

        equity[i] = balance + floating(c[i])
        peak = max(peak, equity[i])
        if equity[i] < peak * (1 - p.max_drawdown_percent / 100):
            halted = True

    if len(times):
        for t in list(open_trades):
            close(t, c[-1] if t.direction > 0 else c[-1] + p.spread, times[-1], "end")
        equity[-1] = balance
    return closed, pd.Series(equity, index=times, name="equity")


def summary(trades: list[Trade], equity: pd.Series, p: Params | None = None) -> dict[str, float]:
    p = p or Params()
    pnls = np.array([t.pnl(t.exit, p.contract_size) for t in trades]) if trades else np.array([])
    wins, losses = pnls[pnls > 0], pnls[pnls <= 0]
    daily = equity.resample("1D").last().dropna().pct_change().dropna()
    drawdown = (equity / equity.cummax() - 1).min() if len(equity) else 0.0
    return {
        "trades": len(trades),
        "win_rate_pct": 100 * len(wins) / len(pnls) if len(pnls) else 0.0,
        "net_profit": float(pnls.sum()),
        "profit_factor": float(wins.sum() / -losses.sum()) if losses.sum() < 0 else math.inf,
        "expected_payoff": float(pnls.mean()) if len(pnls) else 0.0,
        "max_drawdown_pct": float(-100 * drawdown),
        "sharpe": float(daily.mean() / daily.std() * math.sqrt(252)) if daily.std() > 0 else 0.0,
        "final_balance": float(p.deposit + pnls.sum()),
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("csv", nargs="?", help="M5 bars (MetaTrader export or OHLC CSV)")
    parser.add_argument("--yahoo", metavar="SYMBOL", help="download 5m bars instead, e.g. GC=F")
    parser.add_argument("--risk", type=float, default=Params.risk_percent, help="risk %% per trade")
    parser.add_argument("--shorts", action="store_true", help="also take sell signals")
    parser.add_argument("--spread", type=float, default=Params.spread, help="spread in price units")
    parser.add_argument("--trades-out", help="write the trade list to this CSV")
    args = parser.parse_args(argv)
    if not args.csv and not args.yahoo:
        parser.error("give a CSV file or --yahoo SYMBOL")

    bars = load_yahoo(args.yahoo) if args.yahoo else load_bars(args.csv)
    params = Params(risk_percent=args.risk, allow_shorts=args.shorts, spread=args.spread)
    trades, equity = run(bars, params)
    print(f"{len(bars)} bars, {bars.index[0]} -> {bars.index[-1]}")
    for key, value in summary(trades, equity, params).items():
        print(f"{key:>18}: {value:,.2f}")
    if args.trades_out:
        pd.DataFrame([t.__dict__ for t in trades]).to_csv(args.trades_out, index=False)


if __name__ == "__main__":
    main()
