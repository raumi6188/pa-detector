# -*- coding: utf-8 -*-
"""18の型の検知器。

共通構造：
    弱いPA = 逸脱イベント E（基準線 B を下に破る／勢いを失う）
    否定PA = 回復イベント R（k日以内に B を終値で奪回する）

各検知器は「本日 R が成立したか」を判定する。逸脱 E は過去 k 日以内に探す。

戻り値 Hit の意味：
    baseline      … 当日終値が上回っている必要のある水準 B
    monotone_ok   … C 以外の当日条件（H・V）が既に満たされているか。
                    True なら残る不確実性は現在値 C の位置だけになる。
    margin_atr    … (C - B) / ATR。引けまでに剥落する余地の大きさ。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, List
import numpy as np

from .indicators import (
    Context, upper_shadow_ratio, lower_shadow_ratio, clv, body,
)

# 解説書 第9章の否定度ランク（1が最上位）
RANK = {9: 1, 10: 2, 11: 3, 15: 4, 18: 5, 1: 6, 12: 7, 5: 8,
        17: 9, 2: 10, 3: 11, 13: 12, 6: 13, 16: 14, 8: 15, 7: 16, 4: 17, 14: 18}

NAMES = {
    1: "ウップス（前日安値割れの否定）",
    2: "寄り天大陰線の否定（陽の包み線）",
    3: "Fill and Go（窓埋めの否定）",
    4: "強い引け（売り叩きの否定）",
    5: "ヒゲ高値の終値抜け（流れ星の否定）",
    6: "マルボウズの否定（陽の包み線）",
    7: "母線高値ブレイク（はらみの否定）",
    8: "首吊り線の否定（出来高を伴う高値更新）",
    9: "スプリング（安値切り下げの否定）",
    10: "前回高値の終値更新（高値切り下げの否定）",
    11: "フォールスブレイクダウン（支持帯割れの否定）",
    12: "ネックライン奪回（三尊崩れの否定）",
    13: "移動平均への復帰",
    14: "ウィップソー（デッドクロスの否定）",
    15: "分散の否定（薄商いの押し目→大商いの高値更新）",
    16: "出来高逓減の解消",
    17: "逆行高（相対的な弱さの否定）",
    18: "決算売りの奪回",
}

GROUPS = {1: "寄り付き", 2: "寄り付き", 3: "寄り付き", 4: "寄り付き",
          5: "単体足", 6: "単体足", 7: "単体足", 8: "単体足",
          9: "スイング", 10: "スイング", 11: "スイング", 12: "スイング",
          13: "トレンド", 14: "トレンド",
          15: "出来高", 16: "出来高", 17: "出来高", 18: "出来高"}


@dataclass
class Hit:
    pattern_id: int
    baseline: float
    monotone_ok: bool = True
    dev_offset: Optional[int] = None      # 逸脱が何営業日前に起きたか
    note: str = ""
    margin_atr: float = float("nan")

    @property
    def name(self): return NAMES[self.pattern_id]

    @property
    def rank(self): return RANK[self.pattern_id]

    @property
    def group(self): return GROUPS[self.pattern_id]


def _margin(ctx: Context, b: float) -> float:
    if not np.isfinite(ctx.atr) or ctx.atr <= 0:
        return float("nan")
    return (ctx.c - b) / ctx.atr


def _fin(*vals) -> bool:
    return all(np.isfinite(v) for v in vals)


# ---------------------------------------------------------------- 型1
def p01_oops(ctx: Context) -> Optional[Hit]:
    """O < 前日安値 かつ C > 前日安値。同日完結（k=0）。"""
    i = ctx.i
    if i < 1:
        return None
    prev_low = float(ctx.df["l"].iloc[i - 1])
    if not (ctx.o < prev_low and ctx.c > prev_low):
        return None
    # 始値は確定済み・基準線も確定済み → 残る不確実性は C のみ
    return Hit(1, prev_low, monotone_ok=True, dev_offset=0,
               note=f"始値{ctx.o:.0f} < 前日安値{prev_low:.0f}",
               margin_atr=_margin(ctx, prev_low))


# ---------------------------------------------------------------- 型2
def p02_yoriten_engulf(ctx: Context, k: int = 3) -> Optional[Hit]:
    """寄り天大陰線を、その始値を終値で上回って否定。"""
    i = ctx.i
    for back in range(1, k + 1):
        j = i - back
        if j < 1:
            break
        o, h, l, c, v = ctx.row(j)
        if h - l <= 0 or c >= o:
            continue
        if (h - o) / (h - l) >= 0.15:
            continue
        if not _fin(ctx.atr) or body(o, c) / ctx.atr <= 1.0:
            continue
        if ctx.c > o:
            return Hit(2, o, monotone_ok=True, dev_offset=back,
                       note=f"{back}日前の寄り天大陰線の始値{o:.0f}を奪回",
                       margin_atr=_margin(ctx, o))
    return None


# ---------------------------------------------------------------- 型3
def p03_fill_and_go(ctx: Context, k: int = 5, lookback: int = 25) -> Optional[Hit]:
    """上放れ窓 → 窓埋め → 窓下限を終値で奪回。"""
    i = ctx.i
    df = ctx.df
    for g in range(max(1, i - lookback), i):
        gap_floor = float(df["h"].iloc[g - 1])
        if float(df["l"].iloc[g]) <= gap_floor:
            continue                       # 窓ではない
        # 窓発生後に窓下限を終値で割った日を探す
        for j in range(g + 1, i):
            if float(df["c"].iloc[j]) < gap_floor and (i - j) <= k:
                if ctx.c > gap_floor:
                    return Hit(3, gap_floor, monotone_ok=True, dev_offset=i - j,
                               note=f"窓下限{gap_floor:.0f}を再奪回",
                               margin_atr=_margin(ctx, gap_floor))
    return None


# ---------------------------------------------------------------- 型4
def p04_strong_close(ctx: Context) -> Optional[Hit]:
    """引け際の売り叩き（CLV低）が2日続いた後、強い引け（CLV高）で否定。

    ※ 14:00 時点では判定不能（CLVは引け際の値動きそのもの）。確定足専用。

    基準線は「弱かった2日間の終値の高いほう」とする。
    当日レンジの75%地点を基準にすると、条件成立時に余裕が必ず微小になり、
    確度ランクが意味を持たなくなるため。
    弱い引けが続いた水準を終値で上回ったかどうかが、実質的な奪回である。
    """
    i = ctx.i
    if i < 3:
        return None
    w = [clv(*ctx.row(j)[1:4]) for j in (i - 2, i - 1)]
    if not all(x < 0.25 for x in w):
        return None
    今 = clv(ctx.h, ctx.l, ctx.c)
    if 今 <= 0.75 or ctx.v_est_full < ctx.vol_avg20:
        return None
    base = max(float(ctx.df["c"].iloc[i - 2]), float(ctx.df["c"].iloc[i - 1]))
    if ctx.c <= base:
        return None
    return Hit(4, base, monotone_ok=False, dev_offset=1,
               note=f"弱い引け2日（CLV {w[0]:.2f}, {w[1]:.2f}）→ CLV {今:.2f} で"
                    f"{base:.0f}を奪回",
               margin_atr=_margin(ctx, base))


# ---------------------------------------------------------------- 型5
def p05_shooting_star(ctx: Context, k: int = 5) -> Optional[Hit]:
    """上ヒゲ陰線のヒゲ高値を終値で上抜く。"""
    i = ctx.i
    for back in range(1, k + 1):
        j = i - back
        if j < 1:
            break
        o, h, l, c, v = ctx.row(j)
        if c >= o or upper_shadow_ratio(o, h, l, c) <= 0.55:
            continue
        if ctx.c > h:
            return Hit(5, h, monotone_ok=True, dev_offset=back,
                       note=f"{back}日前のヒゲ高値{h:.0f}を終値で突破",
                       margin_atr=_margin(ctx, h))
    return None


# ---------------------------------------------------------------- 型6
def p06_marubozu(ctx: Context, k: int = 3) -> Optional[Hit]:
    i = ctx.i
    for back in range(1, k + 1):
        j = i - back
        if j < 1:
            break
        o, h, l, c, v = ctx.row(j)
        rng = h - l
        if rng <= 0 or c >= o:
            continue
        if (h - o) / rng >= 0.05 or (c - l) / rng >= 0.05:
            continue
        if not _fin(ctx.atr) or body(o, c) / ctx.atr <= 1.2:
            continue
        if ctx.c > o:
            return Hit(6, o, monotone_ok=True, dev_offset=back,
                       note=f"大陰線マルボウズの始値{o:.0f}を奪回",
                       margin_atr=_margin(ctx, o))
    return None


# ---------------------------------------------------------------- 型7
def p07_harami(ctx: Context, k: int = 5) -> Optional[Hit]:
    i = ctx.i
    for back in range(1, k + 1):
        j = i - back
        if j < 2:
            break
        o, h, l, c, v = ctx.row(j)
        po, ph, pl, pc, pv = ctx.row(j - 1)
        if not (h < ph and l > pl):
            continue                      # はらみでない
        if pc <= po:
            continue                      # 母線が陽線でない
        if not _fin(ctx.atr) or body(po, pc) / ctx.atr <= 1.0:
            continue
        if ctx.c > ph:
            return Hit(7, ph, monotone_ok=True, dev_offset=back,
                       note=f"母線高値{ph:.0f}をブレイク",
                       margin_atr=_margin(ctx, ph))
    return None


# ---------------------------------------------------------------- 型8
def p08_hanging_man(ctx: Context, k: int = 3) -> Optional[Hit]:
    i = ctx.i
    if not _fin(ctx.high20, ctx.vol_avg20):
        return None
    for back in range(1, k + 1):
        j = i - back
        if j < 1:
            break
        o, h, l, c, v = ctx.row(j)
        if lower_shadow_ratio(o, h, l, c) <= 0.55:
            continue
        if c <= ctx.high20 * 0.97:
            continue                      # 高値圏でない
        vol_ok = ctx.v_est_full > ctx.vol_avg20 * 1.3
        if ctx.c > h and vol_ok:
            # 出来高条件は単調増加なので、満たした時点で剥落しない
            return Hit(8, h, monotone_ok=True, dev_offset=back,
                       note=f"首吊り線の高値{h:.0f}を出来高伴い更新",
                       margin_atr=_margin(ctx, h))
    return None


# ---------------------------------------------------------------- 型9
def p09_spring(ctx: Context, k: int = 3) -> Optional[Hit]:
    """直近スイング安値を終値で割った後、k日以内に奪回。"""
    i = ctx.i
    if not ctx.swing_lows:
        return None
    sl_idx = ctx.swing_lows[-1]
    sl = float(ctx.df["l"].iloc[sl_idx])
    dev = None
    for back in range(1, k + 1):
        j = i - back
        if j <= sl_idx:
            break
        if float(ctx.df["c"].iloc[j]) < sl:
            dev = back
            break
    if dev is None:
        return None
    if ctx.c <= sl:
        return None
    note = f"スイング安値{sl:.0f}を{dev}日前に割り込み→奪回"
    # 完全否定：直近スイング高値も更新しているか
    if ctx.swing_highs:
        sh = float(ctx.df["h"].iloc[ctx.swing_highs[-1]])
        if ctx.c > sh:
            note += f"／高値{sh:.0f}も更新（完全否定）"
    return Hit(9, sl, monotone_ok=True, dev_offset=dev, note=note,
               margin_atr=_margin(ctx, sl))


# ---------------------------------------------------------------- 型10
def p10_lower_high_negated(ctx: Context) -> Optional[Hit]:
    """高値切り下げの後、その前の高値を終値で更新。"""
    if len(ctx.swing_highs) < 2:
        return None
    h_last = float(ctx.df["h"].iloc[ctx.swing_highs[-1]])
    h_prev = float(ctx.df["h"].iloc[ctx.swing_highs[-2]])
    if h_last >= h_prev:
        return None                       # 切り下げていない
    if ctx.c <= h_prev:
        return None
    return Hit(10, h_prev, monotone_ok=True,
               dev_offset=ctx.i - ctx.swing_highs[-1],
               note=f"切り下げ高値{h_last:.0f}→前回高値{h_prev:.0f}を更新",
               margin_atr=_margin(ctx, h_prev))


# ---------------------------------------------------------------- 型11
def p11_false_breakdown(ctx: Context, k: int = 5, window: int = 40) -> Optional[Hit]:
    """複数回試された支持帯を割り、k日以内に終値で回復。"""
    i = ctx.i
    df = ctx.df
    for back in range(1, k + 1):
        j = i - back
        if j < window + 2:
            break
        # 割れる直前までの支持帯下限
        base_slice = df["l"].iloc[j - window:j]
        support = float(base_slice.min())
        # 「複数回試された」＝支持帯±1%に触れた日が3日以上
        touches = int((base_slice <= support * 1.01).sum())
        if touches < 3:
            continue
        if float(df["c"].iloc[j]) >= support:
            continue
        if ctx.c <= support:
            continue
        v_dev = float(df["v"].iloc[j])
        vol_ratio = ctx.v_est_full / v_dev if v_dev > 0 else 0.0
        if vol_ratio < 1.0:
            continue                      # 回復日の商いが薄い＝自律反発の疑い
        return Hit(11, support, monotone_ok=True, dev_offset=back,
                   note=f"支持帯{support:.0f}（{touches}回接触）を割れ→回復／出来高比{vol_ratio:.2f}",
                   margin_atr=_margin(ctx, support))
    return None


# ---------------------------------------------------------------- 型12
def p12_head_shoulders(ctx: Context, k: int = 5, max_age: int = 60) -> Optional[Hit]:
    """三尊のネックライン割れを奪回。

    ★ 直近3つのスイング高値に限定すると、右肩の後に別のスイングが立った時点で
       見逃す。連続する三つ組をすべて走査する。
    """
    i = ctx.i
    hs, ls = ctx.swing_highs, ctx.swing_lows
    if len(hs) < 3 or len(ls) < 2:
        return None

    best = None
    for t in range(len(hs) - 2):
        lh, head, rh = hs[t], hs[t + 1], hs[t + 2]
        if i - rh > max_age:              # 古すぎる形は無視
            continue
        v_lh = float(ctx.df["h"].iloc[lh])
        v_head = float(ctx.df["h"].iloc[head])
        v_rh = float(ctx.df["h"].iloc[rh])
        if not (v_head > v_lh and v_head > v_rh):
            continue
        if abs(v_lh - v_rh) / max(v_lh, v_rh) > 0.08:   # 両肩の高さが近いこと
            continue
        troughs = [x for x in ls if lh < x < rh]
        if len(troughs) < 2:
            continue
        neck = float(min(ctx.df["l"].iloc[troughs[-2]],
                         ctx.df["l"].iloc[troughs[-1]]))
        if ctx.c <= neck:
            continue
        for back in range(1, k + 1):
            j = i - back
            if j <= rh:
                break
            if float(ctx.df["c"].iloc[j]) < neck:
                cand = Hit(12, neck, monotone_ok=True, dev_offset=back,
                           note=f"ネックライン{neck:.0f}を割れ→奪回"
                                f"（頭{v_head:.0f}／右肩{i-rh}日前）",
                           margin_atr=_margin(ctx, neck))
                if best is None or cand.margin_atr < best.margin_atr:
                    best = cand           # 最も際どい＝最も直近の形を採る
                break
    return best


# ---------------------------------------------------------------- 型13
def p13_ma_recover(ctx: Context, k: int = 5) -> Optional[Hit]:
    i = ctx.i
    ma = ctx.ma_long
    if not np.isfinite(ma.iloc[i]) or i < ctx.cfg.ma_long + 6:
        return None
    slope = float(ma.iloc[i]) - float(ma.iloc[i - 5])
    if slope <= 0:
        return None                       # 傾きが上向きでない
    dev = None
    for back in range(1, k + 1):
        j = i - back
        if j < 0:
            break
        if float(ctx.df["c"].iloc[j]) < float(ma.iloc[j]):
            dev = back
            break
    if dev is None:
        return None
    b = float(ma.iloc[i])
    if ctx.c <= b:
        return None
    return Hit(13, b, monotone_ok=True, dev_offset=dev,
               note=f"MA{ctx.cfg.ma_long}={b:.0f}へ復帰（傾き+{slope:.1f}）",
               margin_atr=_margin(ctx, b))


# ---------------------------------------------------------------- 型14
def p14_whipsaw(ctx: Context, k: int = 10) -> Optional[Hit]:
    i = ctx.i
    s, l = ctx.ma_short, ctx.ma_long
    if not (np.isfinite(s.iloc[i]) and np.isfinite(l.iloc[i])):
        return None
    if not (s.iloc[i] > l.iloc[i] and s.iloc[i - 1] <= l.iloc[i - 1]):
        return None                       # 本日GCしていない
    for back in range(2, k + 2):
        j = i - back
        if j < 1:
            break
        if np.isfinite(s.iloc[j]) and np.isfinite(l.iloc[j]) \
           and s.iloc[j] < l.iloc[j] and s.iloc[j - 1] >= l.iloc[j - 1]:
            b = float(l.iloc[i])
            return Hit(14, b, monotone_ok=True, dev_offset=back,
                       note=f"{back}日前のDCを{back}日で解消",
                       margin_atr=_margin(ctx, b))
    return None


# ---------------------------------------------------------------- 型15
def p15_distribution_negated(ctx: Context, lookback: int = 15) -> Optional[Hit]:
    """高値圏の大商い陰線 → 薄商いの押し目 → 出来高を伴う高値更新。"""
    i = ctx.i
    if not _fin(ctx.vol_avg20, ctx.high20):
        return None
    if ctx.c <= ctx.high20:
        return None                       # 高値更新していない
    if ctx.v_est_full < ctx.vol_avg20 * 1.5:
        return None
    dist_j = None
    for back in range(2, lookback + 1):
        j = i - back
        if j < 1:
            break
        o, h, l, c, v = ctx.row(j)
        if c < o and v > ctx.vol_avg20 * 2.0:
            hi20_at_j = float(ctx.df["h"].iloc[max(0, j - 20):j].max())
            if c >= hi20_at_j * 0.97:
                dist_j = j
                break
    if dist_j is None:
        return None
    # 分散日と本日のあいだに、薄商いの押し目があるか
    mid = ctx.df["v"].iloc[dist_j + 1:i]
    if len(mid) == 0 or float(mid.min()) >= ctx.vol_avg20 * 0.8:
        return None
    return Hit(15, ctx.high20, monotone_ok=True, dev_offset=i - dist_j,
               note=f"分散疑い（{i-dist_j}日前）→薄商いの押し目→大商いで高値{ctx.high20:.0f}更新",
               margin_atr=_margin(ctx, ctx.high20))


# ---------------------------------------------------------------- 型16
def p16_volume_revival(ctx: Context) -> Optional[Hit]:
    i = ctx.i
    if not _fin(ctx.vol_avg20, ctx.high20):
        return None
    if ctx.c <= ctx.high20 or ctx.v_est_full < ctx.vol_avg20 * 1.5:
        return None
    v5 = ctx.df["v"].rolling(5).mean()
    if i < 10 or not np.isfinite(v5.iloc[i - 1]):
        return None
    # 直前の3日間、出来高5日平均が連続低下していたか
    dec = all(float(v5.iloc[i - t]) < float(v5.iloc[i - t - 1]) for t in (1, 2, 3))
    if not dec:
        return None
    ret5 = float(ctx.df["c"].iloc[i - 1]) / float(ctx.df["c"].iloc[i - 6]) - 1.0
    if ret5 <= 0:
        return None
    return Hit(16, ctx.high20, monotone_ok=True, dev_offset=1,
               note=f"出来高逓減（5日平均3日連続低下）を解消し高値{ctx.high20:.0f}更新",
               margin_atr=_margin(ctx, ctx.high20))


# ---------------------------------------------------------------- 型17
def p17_counter_trend_strength(ctx: Context, idx_ret=None, beta=None,
                               window: int = 10) -> Optional[Hit]:
    """指数下落日の逆行高。idx_ret は df と同じ長さの指数日次リターン。"""
    if idx_ret is None or beta is None or not np.isfinite(beta):
        return None
    i = ctx.i
    c = ctx.df["c"]
    if i < window + 2:
        return None
    r_today = ctx.c / float(c.iloc[i - 1]) - 1.0
    ri_today = float(idx_ret[i])
    if ri_today >= 0:
        return None                       # 指数が下げていない
    alpha_today = r_today - beta * ri_today
    if alpha_today <= 0 or ctx.v_est_full < ctx.vol_avg20:
        return None
    weak = 0
    for back in range(1, window + 1):
        j = i - back
        if float(idx_ret[j]) > 0:
            r = float(c.iloc[j]) / float(c.iloc[j - 1]) - 1.0
            if r - beta * float(idx_ret[j]) < 0:
                weak += 1
    if weak < 3:
        return None
    return Hit(17, float(c.iloc[i - 1]), monotone_ok=True, dev_offset=1,
               note=f"指数{ri_today*100:+.2f}%に対し逆行高（α={alpha_today*100:+.2f}%／弱さ{weak}日）",
               margin_atr=_margin(ctx, float(c.iloc[i - 1])))


# ---------------------------------------------------------------- 型18
def p18_earnings_recovery(ctx: Context, earnings_offsets=None,
                          k: int = 15) -> Optional[Hit]:
    """決算反応日の高値を奪回。earnings_offsets は「何日前が決算反応日か」の集合。"""
    if not earnings_offsets:
        return None
    i = ctx.i
    for back in sorted(earnings_offsets):
        if back < 1 or back > k:
            continue
        j = i - back
        if j < 1:
            continue
        o, h, l, c, v = ctx.row(j)
        prev_c = float(ctx.df["c"].iloc[j - 1])
        if not (o > prev_c and c < o):
            continue                      # 上放れ後に売られた形でない
        if ctx.c > h:
            return Hit(18, h, monotone_ok=True, dev_offset=back,
                       note=f"{back}日前の決算反応日の高値{h:.0f}を奪回",
                       margin_atr=_margin(ctx, h))
    return None


DETECTORS = {
    1: p01_oops, 2: p02_yoriten_engulf, 3: p03_fill_and_go, 4: p04_strong_close,
    5: p05_shooting_star, 6: p06_marubozu, 7: p07_harami, 8: p08_hanging_man,
    9: p09_spring, 10: p10_lower_high_negated, 11: p11_false_breakdown,
    12: p12_head_shoulders, 13: p13_ma_recover, 14: p14_whipsaw,
    15: p15_distribution_negated, 16: p16_volume_revival,
    17: p17_counter_trend_strength, 18: p18_earnings_recovery,
}


def _run_detectors(ctx: Context, excluded=(), idx_ret=None, beta=None,
                   earnings_offsets=None) -> List[Hit]:
    hits = []
    for pid, fn in DETECTORS.items():
        if pid in excluded:
            continue
        try:
            if pid == 17:
                h = fn(ctx, idx_ret=idx_ret, beta=beta)
            elif pid == 18:
                h = fn(ctx, earnings_offsets=earnings_offsets)
            else:
                h = fn(ctx)
        except Exception:
            h = None
        if h is not None:
            hits.append(h)
    return hits


def detect_all(ctx: Context, excluded=(), idx_ret=None, beta=None,
               earnings_offsets=None, prev_ctx: Context | None = None,
               prev_idx_ret=None, prev_earnings_offsets=None) -> List[Hit]:
    """本日成立した否定PAを返す。

    ★ 初回のみを報告する ★
      各型の回復条件（終値 > 基準線）は、いったん成立すると
      その後も何日にもわたって真であり続ける。逸脱側にも k 日の猶予があるため、
      同じ銘柄・同じ型が連続して報告されてしまう。
      実測では型10が最長22営業日、型3・型12・型13も10日超にわたり出続けていた。

      否定PAは「その日に起きたイベント」であって「継続する状態」ではないので、
      前日の時点でも成立していた型は除外する。
      prev_ctx（前営業日までのデータで作った Context）を渡すと有効になる。

      なお除外は報告からの除外であって、シグナルの否定ではない。
      継続を追いたい場合は prev_ctx を渡さなければ従来どおりの挙動になる。
    """
    hits = _run_detectors(ctx, excluded, idx_ret, beta, earnings_offsets)
    if prev_ctx is None or not hits:
        return sorted(hits, key=lambda x: x.rank)

    prev_ids = {h.pattern_id for h in _run_detectors(
        prev_ctx, excluded,
        prev_idx_ret if prev_idx_ret is not None else idx_ret,
        beta, prev_earnings_offsets)}
    hits = [h for h in hits if h.pattern_id not in prev_ids]
    return sorted(hits, key=lambda x: x.rank)
