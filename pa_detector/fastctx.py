# -*- coding: utf-8 -*-
"""バックテスト用の高速版 Context。

素朴に Context を毎日作り直すと、ATR・移動平均・スイング点を
のべ100万回以上計算することになり現実的な時間で終わらない。
銘柄ごとに一度だけ計算した配列を、日付インデックスで切り出す形にする。

★ 先読みバイアスを絶対に入れないこと ★
  この設計の唯一の危険は「未来の情報が混ざること」である。
  以下を厳密に守る。

  1. ATR・移動平均・出来高平均・20日高安は rolling で計算する。
     rolling は定義上その時点までしか見ないので安全。

  2. スイング点（fractal）は「前後 n 本のなかで最高値／最安値」なので、
     位置 j の点は j+n 日目にならないと確定しない。
     判定日 t では **j <= t - n - 1 の点だけ**を使う。
     （さらに live 版と揃えるため、確定足のみを見る条件も課す）

  3. すべての指標は「t 日目の終値までで確定するもの」に限る。

  live 版（indicators.Context）と同じ値を返すことを
  verify_against_live() で全営業日・全指標について照合する。
"""
from __future__ import annotations
import numpy as np
import pandas as pd

from .indicators import true_range, fractal_swings
from .config import CFG


class Precomputed:
    """1銘柄ぶんの指標を一度だけ計算して保持する。"""

    def __init__(self, code: str, df: pd.DataFrame, cfg=CFG):
        self.code = code
        self.df = df.reset_index(drop=True)
        self.cfg = cfg
        n = len(self.df)

        o = self.df["o"].to_numpy(float)
        h = self.df["h"].to_numpy(float)
        l = self.df["l"].to_numpy(float)
        c = self.df["c"].to_numpy(float)
        v = self.df["v"].to_numpy(float)
        self.o, self.h, self.l, self.c, self.v = o, h, l, c, v

        # ---- ATR：t 日目の Context は「t-1 までの確定足」で計算する ----
        # live 版は atr(df, period, exclude_last=True) なので、
        # 位置 t での値は TR[t-period : t] の平均に一致する。
        tr = true_range(self.df).to_numpy(float)
        atr = np.full(n, np.nan)
        p = cfg.atr_period
        for t in range(n):
            s = t - p
            if s >= 1:                      # TR[0] は前日終値が無いので使わない
                atr[t] = np.nanmean(tr[s:t])
        self.atr = atr

        # ---- 出来高20日平均（t-1 まで）----
        vol_avg = np.full(n, np.nan)
        q = cfg.vol_avg_period
        for t in range(n):
            if t - q >= 0:
                vol_avg[t] = v[t - q:t].mean()
        self.vol_avg20 = vol_avg

        # ---- 20日高値・安値（t-1 まで）----
        hi20 = np.full(n, np.nan)
        lo20 = np.full(n, np.nan)
        for t in range(n):
            if t - 20 >= 0:
                hi20[t] = h[t - 20:t].max()
                lo20[t] = l[t - 20:t].min()
        self.high20, self.low20 = hi20, lo20

        # ---- 売買代金の20日中央値（t-1 まで）----
        to = c * v
        tmed = np.full(n, 0.0)
        for t in range(n):
            if t - 20 >= 0:
                tmed[t] = float(np.median(to[t - 20:t]))
        self.turnover_med = tmed

        # ---- 移動平均（終値ベース。t 日目を含む）----
        cs = pd.Series(c)
        self.ma_short = cs.rolling(cfg.ma_short, min_periods=cfg.ma_short).mean()
        self.ma_long = cs.rolling(cfg.ma_long, min_periods=cfg.ma_long).mean()

        # ---- スイング点 ----
        # 全期間で一度だけ求め、判定日ごとに「確定済みのもの」だけを切り出す。
        # fractal は位置 j の前後 n 本を見るので、j + n 日目に確定する。
        hs, ls = fractal_swings(self.df, cfg.swing_n, exclude_last=False)
        self.all_swing_highs = np.array(hs, dtype=int)
        self.all_swing_lows = np.array(ls, dtype=int)

    def context(self, t: int):
        """t 日目を「当日」とする判定文脈を返す。"""
        return FastContext(self, t)


class FastContext:
    """indicators.Context と同じインタフェースを持つ軽量版。"""

    __slots__ = ("_p", "i", "df", "cfg", "atr", "vol_avg20", "high20", "low20",
                 "turnover_med", "ma_short", "ma_long",
                 "swing_highs", "swing_lows", "v_est_full", "v_today_raw",
                 "intraday", "code")

    def __init__(self, p: Precomputed, t: int):
        self._p = p
        self.i = t
        self.code = p.code
        self.cfg = p.cfg
        self.intraday = False
        # detect 側は df.iloc[j] で過去を参照するだけなので、
        # t 日目までに切り詰めた view を渡せば未来は見えない。
        self.df = p.df.iloc[:t + 1]

        self.atr = p.atr[t]
        self.vol_avg20 = p.vol_avg20[t]
        self.high20 = p.high20[t]
        self.low20 = p.low20[t]
        self.turnover_med = p.turnover_med[t]
        self.ma_short = p.ma_short.iloc[:t + 1]
        self.ma_long = p.ma_long.iloc[:t + 1]

        # ★ 先読み防止の要 ★
        # 位置 j のスイング点は j + swing_n 日目に確定する。
        # さらに live 版は exclude_last=True（当日を除く）なので、
        # j <= t - 1 - swing_n を満たすものだけを使う。
        n = p.cfg.swing_n
        lim = t - 1 - n
        self.swing_highs = [int(x) for x in p.all_swing_highs if x <= lim]
        self.swing_lows = [int(x) for x in p.all_swing_lows if x <= lim]

        self.v_today_raw = p.v[t]
        self.v_est_full = p.v[t]        # 確定足なので補正なし

    @property
    def o(self): return float(self._p.o[self.i])

    @property
    def h(self): return float(self._p.h[self.i])

    @property
    def l(self): return float(self._p.l[self.i])

    @property
    def c(self): return float(self._p.c[self.i])

    def row(self, j):
        p = self._p
        return (float(p.o[j]), float(p.h[j]), float(p.l[j]),
                float(p.c[j]), float(p.v[j]))


def verify_against_live(df: pd.DataFrame, cfg=CFG, code="TEST", tol=1e-6):
    """live 版 Context と全営業日で値が一致するかを照合する。

    バックテストと実運用で違う値が出ていたら検証は無意味になるので、
    この照合を必ず通してから使うこと。
    """
    from .indicators import Context

    pre = Precomputed(code, df, cfg)
    bad = []
    start = cfg.ma_long + 31
    for t in range(start, len(df)):
        fast = pre.context(t)
        live = Context(code, df.iloc[:t + 1], cfg, intraday=False)

        checks = [
            ("atr", fast.atr, live.atr),
            ("vol_avg20", fast.vol_avg20, live.vol_avg20),
            ("high20", fast.high20, live.high20),
            ("low20", fast.low20, live.low20),
            ("turnover_med", fast.turnover_med, live.turnover_med),
            ("c", fast.c, live.c),
            ("o", fast.o, live.o),
            ("ma_long", float(fast.ma_long.iloc[-1]), float(live.ma_long.iloc[-1])),
            ("ma_short", float(fast.ma_short.iloc[-1]), float(live.ma_short.iloc[-1])),
        ]
        for name, a, b in checks:
            if np.isnan(a) and np.isnan(b):
                continue
            if not (abs(a - b) <= tol * max(1.0, abs(b))):
                bad.append((t, name, a, b))

        if fast.swing_highs != live.swing_highs:
            bad.append((t, "swing_highs", fast.swing_highs[-3:], live.swing_highs[-3:]))
        if fast.swing_lows != live.swing_lows:
            bad.append((t, "swing_lows", fast.swing_lows[-3:], live.swing_lows[-3:]))

        if len(bad) > 20:
            break
    return bad
