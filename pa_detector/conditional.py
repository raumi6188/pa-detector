# -*- coding: utf-8 -*-
"""バリュー株・底値圏という条件を付けた場合の検証。

    python -m pa_detector.conditional

★ 事前に決めたことを先に書く ★
  検定を回してから条件を探すと必ず何かが「有意」になる。
  以下はデータを見る前に確定させた定義と検定計画である。

  条件の定義
    バリュー … PBR < 1.0（解散価値割れ。恣意性の少ない線）
    底値圏  … (終値 − 250日安値) / (250日高値 − 250日安値) < 0.25

  検定計画
    型 …… 9（スプリング）, 10（高値切り下げの否定）, 11（フォールスブレイクダウン）,
           および ANY（いずれかの型が発火）
    条件 … 全体 / バリュー / 底値圏 / バリュー かつ 底値圏
    期間 … 5日, 20日
    合計 4型 × 4条件 × 2期間 = 32検定 → Benjamini-Hochberg で FDR 制御

★ 先読みの防止 ★
  PBR はその時点で **開示済み** の BPS だけを使う（disc_date <= 判定日）。
  最新のBPSを全期間に適用すると未来の情報が混ざる。
  250日高安も判定日の前日までで計算する。
"""
from __future__ import annotations
import os
import re
import sqlite3
import numpy as np
import pandas as pd
from math import erfc, sqrt

from .config import CFG
from . import store, patterns
from .backtest import benjamini_hochberg

PATTERNS = [9, 10, 11]
HORIZONS = (5, 20)
PBR_MAX = 1.0
BOTTOM_Q = 0.25


def load_pit_bps(db_path: str) -> pd.DataFrame:
    """本決算のBPSを開示日つきで返す（point-in-time 用）。"""
    con = sqlite3.connect(db_path)
    s = store.SCHEMA
    q = f"""SELECT {s['col_stmt_code']} AS code, {s['col_stmt_date']} AS disc,
                   {s['col_bps']} AS bps
            FROM {s['stmt_table']}
            WHERE {s['col_period']}='FY' AND {s['col_bps']} IS NOT NULL
            ORDER BY {s['col_stmt_code']}, {s['col_stmt_date']}"""
    d = pd.read_sql_query(q, con)
    con.close()
    d["code"] = d["code"].astype(str)
    d["bps"] = pd.to_numeric(d["bps"], errors="coerce")
    return d.dropna(subset=["bps"])


def attach_conditions(panel: pd.DataFrame, bps: pd.DataFrame,
                      bars: dict) -> pd.DataFrame:
    """パネルに PBR と レンジ内位置 を付ける。すべて point-in-time。"""
    panel = panel.copy()
    panel["code"] = panel["code"].astype(str)

    # ---- PBR：判定日までに開示済みの最新BPS ----
    # merge_asof は左右とも key でソートされている必要がある
    left = panel[["code", "date"]].copy()
    left["d"] = pd.to_datetime(left["date"])
    left = left.sort_values("d")
    right = bps.copy()
    right["d"] = pd.to_datetime(right["disc"])
    right = right.sort_values("d")

    merged = pd.merge_asof(left, right[["code", "d", "bps"]],
                           on="d", by="code", direction="backward")
    merged = merged.sort_index()
    panel = panel.merge(
        merged[["code", "date", "bps"]].drop_duplicates(["code", "date"]),
        on=["code", "date"], how="left")
    panel["pbr"] = panel["close"] / panel["bps"]
    panel.loc[~np.isfinite(panel["pbr"]) | (panel["pbr"] <= 0), "pbr"] = np.nan

    # ---- 250日レンジ内の位置（判定日の前日まで）----
    pos = {}
    for code, df in bars.items():
        h = df["h"].to_numpy(float)
        l = df["l"].to_numpy(float)
        c = df["c"].to_numpy(float)
        dates = df["date"].astype(str).to_numpy()
        for t in range(250, len(df)):
            hi = h[t - 250:t].max()
            lo = l[t - 250:t].min()
            if hi > lo:
                pos[(str(code), dates[t])] = (c[t] - lo) / (hi - lo)
    panel["range_pos"] = [pos.get((c, d), np.nan)
                          for c, d in zip(panel["code"], panel["date"])]
    return panel


def test_one(sub: pd.DataFrame, pid, k: int, label: str):
    col = f"x{k}"
    d = sub[sub[col].notna()]
    if len(d) < 200:
        return None
    if pid == "ANY":
        det = d["ids"].apply(lambda s: len(s) > 0)
    else:
        det = d["ids"].apply(lambda s: pid in s)
    n_hit = int(det.sum())
    if n_hit < 30:
        return None

    hit = d.loc[det, col]
    base = d.loc[~det, col]
    up = d[col] > 0
    a = int((det & up).sum()); b = n_hit - a
    c_ = int((~det & up).sum()); dd = int((~det).sum()) - c_
    lr = ((a / (a + c_)) / (b / (b + dd))) if (a + c_) and (b + dd) and b else np.nan

    n1, n2 = len(hit), len(base)
    se = sqrt(hit.var(ddof=1) / n1 + base.var(ddof=1) / n2) if n1 > 1 and n2 > 1 else np.nan
    t = (hit.mean() - base.mean()) / se if se and se > 0 else np.nan
    p = erfc(abs(t) / sqrt(2)) if np.isfinite(t) else np.nan

    return {"条件": label, "型": pid, "期間": k, "件数": n_hit,
            "母数": len(d),
            "勝率": det[det].index.size and float(up[det].mean()) * 100,
            "基準勝率": float(up[~det].mean()) * 100,
            "超過%": (hit.mean() - base.mean()) * 100,
            "LR": lr, "t": t, "p": p}


def main():
    print("=" * 92)
    print("条件付き検証：バリュー株（PBR<1.0）・底値圏（250日レンジ下位25%）")
    print("=" * 92)
    print("※ 事前に定義を確定し、行った32検定をすべて報告する（都合のよいものだけ見せない）")

    panel_path = None
    for d in (os.path.join(CFG.out_dir, "backtest"), CFG.out_dir):
        for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            if f.startswith("backtest_panel_"):
                panel_path = os.path.join(d, f)
    if not panel_path:
        print("先に backtest.py を実行してください")
        return
    print(f"\nパネル: {panel_path}")
    panel = pd.read_csv(panel_path, dtype={"code": str}, low_memory=False)
    panel["ids"] = panel["pids"].astype(str).apply(
        lambda s: {int(x) for x in re.findall(r"\d+", s)})

    con = store.connect(CFG.db_path)
    uni = store.prime_universe(con)
    codes = list(uni["code"].astype(str))
    bars = store.load_bars(con, codes, 600)
    con.close()

    bps = load_pit_bps(CFG.db_path)
    print(f"BPS開示 {len(bps):,}件（{bps['code'].nunique()}銘柄）")

    panel = attach_conditions(panel, bps, bars)
    print(f"PBR算出できたセル {panel['pbr'].notna().sum():,}/{len(panel):,}")
    print(f"レンジ位置算出できたセル {panel['range_pos'].notna().sum():,}/{len(panel):,}")

    subsets = {
        "全体": panel,
        "バリュー": panel[panel["pbr"] < PBR_MAX],
        "底値圏": panel[panel["range_pos"] < BOTTOM_Q],
        "バリュー×底値圏": panel[(panel["pbr"] < PBR_MAX)
                                 & (panel["range_pos"] < BOTTOM_Q)],
    }
    print("\n部分集合のセル数")
    for k, v in subsets.items():
        print(f"  {k:<16} {len(v):>8,}")

    rows = []
    for label, sub in subsets.items():
        for pid in PATTERNS + ["ANY"]:
            for k in HORIZONS:
                r = test_one(sub, pid, k, label)
                if r:
                    rows.append(r)
    res = pd.DataFrame(rows)
    if res.empty:
        print("標本が足りません")
        return
    res["有意BH"] = benjamini_hochberg(res["p"].to_numpy(), q=0.10)

    for k in HORIZONS:
        print(f"\n■ {k}営業日後")
        print(f"{'条件':<16}{'型':>5}{'件数':>7}{'勝率':>7}{'基準':>7}"
              f"{'超過%':>8}{'LR':>7}{'t':>7}{'p':>8}  BH")
        print("-" * 82)
        for _, x in res[res["期間"] == k].iterrows():
            print(f"{x['条件']:<16}{str(x['型']):>5}{x['件数']:>7}"
                  f"{x['勝率']:>6.1f}%{x['基準勝率']:>6.1f}%"
                  f"{x['超過%']:>+8.2f}{x['LR']:>7.2f}{x['t']:>+7.2f}"
                  f"{x['p']:>8.4f}  {'★' if x['有意BH'] else ''}")

    out = os.path.join(CFG.out_dir, "backtest", "conditional.csv")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    res.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n保存: {out}")
    print(f"\n32検定中 BH補正後に有意: {int(res['有意BH'].sum())}件")


if __name__ == "__main__":
    main()
