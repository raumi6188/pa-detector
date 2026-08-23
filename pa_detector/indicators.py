# -*- coding: utf-8 -*-
"""テクニカル指標とスイング点抽出。

重要な約束：
  - df の最終行は「当日の未完成足」である場合がある（intraday=True）。
  - ATR・スイング点・出来高平均は **確定足のみ** から計算する。
    未完成足を混ぜると基準線そのものが場中に動いてしまい、判定が不安定になる。
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_c = df["c"].shift(1)
    a = df["h"] - df["l"]
    b = (df["h"] - prev_c).abs()
    d = (df["l"] - prev_c).abs()
    return pd.concat([a, b, d], axis=1).max(axis=1)


def atr(df: pd.DataFrame, period: int = 14, exclude_last: bool = True) -> float:
    """確定足のみで ATR を計算して単一値を返す。"""
    d = df.iloc[:-1] if exclude_last else df
    if len(d) < period + 1:
        return float("nan")
    tr = true_range(d)
    return float(tr.iloc[-period:].mean())


def sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n, min_periods=n).mean()


def fractal_swings(df: pd.DataFrame, n: int = 3, exclude_last: bool = True):
    """前後 n 本のなかで最高値／最安値となる点をスイング点とする。

    戻り値: (swing_high_idx, swing_low_idx) — いずれも df の位置インデックスの昇順リスト。
    最後の n 本は「前後」が揃わないため確定しない（＝後から生えてくることはない）。
    """
    d = df.iloc[:-1] if exclude_last else df
    h = d["h"].to_numpy()
    l = d["l"].to_numpy()
    highs, lows = [], []
    for i in range(n, len(d) - n):
        w_h = h[i - n:i + n + 1]
        w_l = l[i - n:i + n + 1]
        if h[i] == w_h.max() and (w_h.argmax() == n):
            highs.append(i)
        if l[i] == w_l.min() and (w_l.argmin() == n):
            lows.append(i)
    return highs, lows


def last_swing_low(df, n=3):
    _, lows = fractal_swings(df, n)
    return (lows[-1], float(df["l"].iloc[lows[-1]])) if lows else (None, None)


def last_swing_high(df, n=3):
    highs, _ = fractal_swings(df, n)
    return (highs[-1], float(df["h"].iloc[highs[-1]])) if highs else (None, None)


def prev_swing_high(df, n=3):
    """直近スイング高値の「ひとつ前」の高値。型10で使う。"""
    highs, _ = fractal_swings(df, n)
    if len(highs) < 2:
        return None, None
    return highs[-2], float(df["h"].iloc[highs[-2]])


def vol_avg(df: pd.DataFrame, period: int = 20, exclude_last: bool = True) -> float:
    d = df.iloc[:-1] if exclude_last else df
    if len(d) < period:
        return float("nan")
    return float(d["v"].iloc[-period:].mean())


def rolling_high(df: pd.DataFrame, period: int = 20, exclude_last: bool = True) -> float:
    d = df.iloc[:-1] if exclude_last else df
    if len(d) < period:
        return float("nan")
    return float(d["h"].iloc[-period:].max())


def rolling_low(df: pd.DataFrame, period: int = 20, exclude_last: bool = True) -> float:
    d = df.iloc[:-1] if exclude_last else df
    if len(d) < period:
        return float("nan")
    return float(d["l"].iloc[-period:].min())


def turnover_median(df: pd.DataFrame, period: int = 20) -> float:
    d = df.iloc[:-1]
    if len(d) < period:
        return 0.0
    t = (d["c"] * d["v"]).iloc[-period:]
    return float(t.median())


def body(o, c):
    return abs(c - o)


def upper_shadow_ratio(o, h, l, c):
    rng = h - l
    return 0.0 if rng <= 0 else (h - max(o, c)) / rng


def lower_shadow_ratio(o, h, l, c):
    rng = h - l
    return 0.0 if rng <= 0 else (min(o, c) - l) / rng


def clv(h, l, c):
    rng = h - l
    return 0.5 if rng <= 0 else (c - l) / rng


class Context:
    """1銘柄ぶんの判定文脈。指標を一度だけ計算して使い回す。"""

    def __init__(self, code: str, df: pd.DataFrame, cfg, intraday: bool = True):
        self.code = code
        self.df = df.reset_index(drop=True)
        self.cfg = cfg
        self.intraday = intraday
        self.i = len(self.df) - 1              # 当日（未完成足）の位置

        self.atr = atr(self.df, cfg.atr_period, exclude_last=True)
        self.vol_avg20 = vol_avg(self.df, cfg.vol_avg_period, exclude_last=True)
        self.high20 = rolling_high(self.df, 20, exclude_last=True)
        self.low20 = rolling_low(self.df, 20, exclude_last=True)
        self.turnover_med = turnover_median(self.df, 20)

        c = self.df["c"]
        self.ma_short = sma(c, cfg.ma_short)
        self.ma_long = sma(c, cfg.ma_long)

        self.swing_highs, self.swing_lows = fractal_swings(
            self.df, cfg.swing_n, exclude_last=True)

        # 当日の出来高を「フル出来高相当」に引き伸ばした推定値
        v_today = float(self.df["v"].iloc[self.i])
        f = cfg.volume_fraction_1400 if intraday else 1.0
        self.v_est_full = v_today / max(f, 1e-9)
        self.v_today_raw = v_today

    # --- 当日足へのショートカット ---
    @property
    def o(self): return float(self.df["o"].iloc[self.i])

    @property
    def h(self): return float(self.df["h"].iloc[self.i])

    @property
    def l(self): return float(self.df["l"].iloc[self.i])

    @property
    def c(self): return float(self.df["c"].iloc[self.i])

    def row(self, j):
        r = self.df.iloc[j]
        return float(r["o"]), float(r["h"]), float(r["l"]), float(r["c"]), float(r["v"])
