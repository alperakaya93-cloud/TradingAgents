"""The gold bot backtest follows the EA's rules without seeing later prices."""

import numpy as np
import pandas as pd
import pytest

from gold_bot.backtest import Params, add_signals, load_bars, lot_size, run, summary


def _bars(n: int = 6000, drift: float = 0.05, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    index = pd.date_range("2026-01-05", periods=n, freq="5min")
    wave = 6 * np.sin(np.arange(n) / 15)
    close = 4000 + drift * np.arange(n) + wave + rng.normal(0, 0.8, n).cumsum() * 0.3
    open_ = np.r_[close[0], close[:-1]]
    spread = np.abs(rng.normal(0, 0.6, n)) + 0.2
    return pd.DataFrame(
        {
            "open": open_,
            "high": np.maximum(open_, close) + spread,
            "low": np.minimum(open_, close) - spread,
            "close": close,
        },
        index=index,
    )


@pytest.mark.unit
def test_signals_do_not_change_when_later_bars_change():
    bars = _bars()
    cut = 4000
    altered = bars.copy()
    altered.iloc[cut + 1 :] *= 1.2
    p = Params()
    before = add_signals(bars, p)["signal"].iloc[: cut + 1]
    after = add_signals(altered, p)["signal"].iloc[: cut + 1]
    pd.testing.assert_series_equal(before, after)


@pytest.mark.unit
def test_uptrend_produces_long_trades_only_by_default():
    trades, equity = run(_bars())
    assert trades
    assert {t.direction for t in trades} == {1}
    assert len(equity) == 6000
    stats = summary(trades, equity)
    assert stats["trades"] == len(trades)
    assert stats["final_balance"] == pytest.approx(equity.iloc[-1])


@pytest.mark.unit
def test_downtrend_trades_only_when_shorts_are_allowed():
    bars = _bars(drift=-0.05)
    assert all(t.direction == 1 for t in run(bars)[0])
    shorts = [t for t in run(bars, Params(allow_shorts=True))[0] if t.direction == -1]
    assert shorts


@pytest.mark.unit
def test_lot_size_risks_the_given_percent_and_rounds_down():
    p = Params(risk_percent=1.0)
    # 1% of 100k = 1000; a $6 stop on 100 oz per lot loses 600 per lot.
    assert lot_size(100_000, 100_000, 6.0, 4000, p) == pytest.approx(1.66)
    assert lot_size(100, 100, 6.0, 4000, p) == 0.0
    # Free margin caps the size: 1 lot of gold at 4000 needs 4000 at 1:100.
    assert lot_size(100_000, 2_000, 0.5, 4000, p) == pytest.approx(0.45)
    assert lot_size(100_000, 30, 0.5, 4000, p) == 0.0


@pytest.mark.unit
def test_stop_is_taken_when_a_bar_touches_stop_and_target():
    bars = _bars()
    p = Params(max_positions=1, breakeven_atr=1e9)
    first = run(bars, p)[0][0]
    i = bars.index.get_loc(first.entry_time)
    huge = bars.copy()
    huge.iloc[i + 1, huge.columns.get_loc("high")] = first.tp + 50
    huge.iloc[i + 1, huge.columns.get_loc("low")] = first.sl - 50
    trade = run(huge, p)[0][0]
    assert trade.entry_time == first.entry_time
    assert trade.reason == "sl"
    assert trade.exit == pytest.approx(trade.sl)


@pytest.mark.unit
def test_load_bars_reads_a_metatrader_export(tmp_path):
    path = tmp_path / "XAUUSD_M5.csv"
    path.write_text(
        "<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\n"
        "2026.10.15\t03:00:00\t4100.1\t4101.0\t4099.5\t4100.8\t120\n"
        "2026.10.15\t03:05:00\t4100.8\t4102.2\t4100.2\t4101.9\t98\n"
    )
    bars = load_bars(path)
    assert list(bars.columns) == ["open", "high", "low", "close"]
    assert bars.index[1] == pd.Timestamp("2026-10-15 03:05")
    assert bars["close"].iloc[1] == pytest.approx(4101.9)
