# -*- coding: utf-8 -*-
"""ヒットに確度ランクを付ける。

余裕 = (現在値 − 基準線) ÷ ATR

意味は intraday かどうかで変わる。

【場中（intraday=True）】
  14:00 以降に動きうるのは現在値 C だけである。高値 H・安値 L・出来高 V は
  単調にしか動かないため、それらを使う条件は一度満たせば剥落しない。
  残る不確実性は「引けまでに C が基準線 B を割り込むか」に集約される。
  余裕はその剥落しにくさの代理変数になる。

  monotone_ok=False の型（型4など、当日の H/L に非単調に依存する）は
  引けまでの挙動が読めないため C 扱いにする。

【確定足（intraday=False）】
  すべて確定済みなので剥落は起こらない。単調性の議論も不要。
  余裕は「基準線をどれだけ明確に奪回したか」という強さの指標になる。

ATR で正規化するのは、値がさ株と低位株を同じ尺度で比べるためである。
"""
from __future__ import annotations
import numpy as np


def rate(hit, cfg, intraday: bool = True) -> str:
    m = hit.margin_atr
    if not np.isfinite(m):
        return "C"
    # 単調性は「引けまでに剥落しうるか」の話なので、確定足では問わない
    if intraday and not hit.monotone_ok:
        return "C"
    if m >= cfg.conf_a_margin:
        return "A"
    if m >= cfg.conf_b_margin:
        return "B"
    return "C"


def annotate(hits, cfg, intraday: bool = True):
    for h in hits:
        h.confidence = rate(h, cfg, intraday)
    return hits
