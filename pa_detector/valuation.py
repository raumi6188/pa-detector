# -*- coding: utf-8 -*-
"""PER / PBR / 配当利回りを現在値から算出する。

PER = 現在値 ÷ 1株当たり利益（会社予想）
PBR = 現在値 ÷ 1株当たり純資産
利回り = 1株当たり配当（会社予想） ÷ 現在値

EPS が負のときは PER を出さない（意味を持たないため）。
"""
from __future__ import annotations
import numpy as np


def compute(price: float, eps, bps, dps) -> dict:
    def f(x):
        try:
            x = float(x)
            return x if np.isfinite(x) else None
        except (TypeError, ValueError):
            return None

    eps, bps, dps = f(eps), f(bps), f(dps)
    per = price / eps if (eps and eps > 0) else None
    pbr = price / bps if (bps and bps > 0) else None
    yld = (dps / price * 100.0) if (dps is not None and price > 0) else None
    return {"per": per, "pbr": pbr, "yield_pct": yld}


def fmt(v, digits=1, suffix=""):
    return "—" if v is None else f"{v:.{digits}f}{suffix}"
