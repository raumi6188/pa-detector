# -*- coding: utf-8 -*-
"""翌日の寄り→引けで検証する。

    python -m pa_detector.nextday

検証する取引
------------
  当日 t の大引けで型が成立（17:15にメールが届く）
      ↓
  翌日 t+1 の **寄り成行** でエントリー
      ↓
  同じ日 t+1 の **引け成行** でイグジット

  保有時間は1日の立会時間だけ。オーバーナイトのリスクを負わない。

なぜ2つに分けるか
-----------------
  当日終値から翌日寄り付きまでの窓は、**寄りで買う以上どうやっても取れない**。
  反応の大部分がそこで起きているなら、シグナルに情報があっても
  実際には取りに行けないことになる。そこで

      窓   = 翌日始値 ÷ 当日終値 − 1     ← 取れない
      日中 = 翌日終値 ÷ 翌日始値 − 1     ← 実際に取れる
      合計 = 翌日終値 ÷ 当日終値 − 1

  を分けて測る。判断すべきは「日中」のほうである。

超過リターン
------------
  同じ日の全銘柄の中央値を引く。市場全体が寄り安・引け高だった日に
  上がったからといって、その銘柄が強かったことにはならない。
"""
from __future__ import annotations
import os
import re
import numpy as np
import pandas as pd
from math import erfc, sqrt

from .config import CFG
from . import store, patterns
from .backtest import benjamini_hochberg

COST_ROUNDTRIP = 0.20      # 往復コストの想定（%）。手数料＋スプレッド


def find_panel():
    for d in (os.path.join(CFG.out_dir, "backtest"), CFG.out_dir):
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d), reverse=True):
            if f.startswith("backtest_panel_"):
                return os.path.join(d, f)
    return None


def attach_nextday(panel: pd.DataFrame, bars: dict) -> pd.DataFrame:
    """翌日の始値・終値を引き当てて、窓・日中・合計のリターンを付ける。

    ★ 行番号 t で突き合わせてはいけない ★
      パネルを作ったときの load_bars と、ここでの load_bars で
      読み込む日数が違えば、同じ t でも別の日付を指す。
      実際にこれで 2024年の行と 2025年の行を比べ、
      窓リターン +200% という無意味な値を出していた。
      必ず **日付** で突き合わせること。
    """
    # (code, date) -> その翌営業日の始値・終値
    nxt = {}
    for code, df in bars.items():
        code = str(code)
        dates = df["date"].astype(str).to_numpy()
        o = df["o"].to_numpy(float)
        c = df["c"].to_numpy(float)
        for i in range(len(df) - 1):
            nxt[(code, dates[i])] = (c[i], o[i + 1], c[i + 1])

    gap, oc, tot = [], [], []
    for code, date in zip(panel["code"].astype(str), panel["date"].astype(str)):
        v = nxt.get((code, date))
        if v is None:
            gap.append(np.nan); oc.append(np.nan); tot.append(np.nan)
            continue
        c0, o1, c1 = v
        if not (np.isfinite(o1) and np.isfinite(c1) and np.isfinite(c0)
                and o1 > 0 and c0 > 0):
            gap.append(np.nan); oc.append(np.nan); tot.append(np.nan)
            continue
        gap.append(o1 / c0 - 1.0)
        oc.append(c1 / o1 - 1.0)
        tot.append(c1 / c0 - 1.0)

    panel = panel.copy()
    panel["r_gap"] = gap
    panel["r_oc"] = oc
    panel["r_tot"] = tot

    # 異常値の点検（分割の取りこぼし等）
    for col in ("r_gap", "r_oc"):
        n_ext = int((panel[col].abs() > 0.30).sum())
        if n_ext:
            print(f"      注意: |{col}|>30% が {n_ext}件。分割の取りこぼしの可能性")
    for col in ("r_gap", "r_oc", "r_tot"):
        med = panel.groupby("date")[col].transform("median")
        panel["x" + col[1:]] = panel[col] - med
    return panel


def stat(d: pd.DataFrame, mask, col: str):
    hit = d.loc[mask, col].dropna()
    base = d.loc[~mask, col].dropna()
    if len(hit) < 30 or len(base) < 30:
        return None
    se = sqrt(hit.var(ddof=1) / len(hit) + base.var(ddof=1) / len(base))
    t = (hit.mean() - base.mean()) / se if se > 0 else np.nan
    p = erfc(abs(t) / sqrt(2)) if np.isfinite(t) else np.nan
    return {"n": len(hit),
            "mean_pct": hit.mean() * 100,
            "base_pct": base.mean() * 100,
            "excess_pct": (hit.mean() - base.mean()) * 100,
            "win": (hit > 0).mean() * 100,
            "base_win": (base > 0).mean() * 100,
            "t": t, "p": p}


def main():
    pp = find_panel()
    if not pp:
        print("先に backtest.py を実行してください")
        return
    print(f"パネル: {pp}")
    panel = pd.read_csv(pp, dtype={"code": str}, low_memory=False)
    panel["ids"] = panel["pids"].astype(str).apply(
        lambda s: {int(x) for x in re.findall(r"\d+", s)})

    con = store.connect(CFG.db_path)
    uni = store.prime_universe(con)
    bars = store.load_bars(con, list(uni["code"].astype(str)), 600)
    con.close()

    panel = attach_nextday(panel, bars)
    d = panel[panel["x_oc"].notna()]
    print(f"評価セル {len(d):,}／{d['code'].nunique()}銘柄／{d['date'].nunique()}営業日")

    print("\n" + "=" * 96)
    print("翌日 寄り→引け（保有は立会時間のみ）")
    print("=" * 96)
    print("窓 = 翌日始値÷当日終値−1（寄りで買う以上、取れない部分）")
    print("日中 = 翌日終値÷翌日始値−1（実際に取れる部分）　いずれも同日中央値を控除\n")

    print(f"{'順':>3}{'型':>4} {'名称':<24}{'件数':>7}"
          f"{'窓%':>8}{'日中%':>8}{'日中勝率':>9}{'基準':>7}{'t':>7}{'p':>8}  BH")
    print("-" * 96)

    rows = []
    for pid in sorted(patterns.DETECTORS, key=lambda p: patterns.RANK[p]):
        if pid in (17, 18):
            continue
        m = d["ids"].apply(lambda s: pid in s)
        if m.sum() < 30:
            continue
        g = stat(d, m, "x_gap")
        o = stat(d, m, "x_oc")
        if not o:
            continue
        rows.append({"pattern_id": pid, "rank": patterns.RANK[pid],
                     "name": patterns.NAMES[pid], "n": o["n"],
                     "gap_excess": g["excess_pct"] if g else np.nan,
                     "oc_excess": o["excess_pct"], "oc_win": o["win"],
                     "oc_base_win": o["base_win"], "t": o["t"], "p": o["p"]})

    res = pd.DataFrame(rows)
    res["sig_BH"] = benjamini_hochberg(res["p"].to_numpy(), q=0.10)
    for _, x in res.iterrows():
        print(f"{x['rank']:>3}{x['pattern_id']:>4} {x['name'][:24]:<24}{x['n']:>7,}"
              f"{x['gap_excess']:>+8.2f}{x['oc_excess']:>+8.2f}"
              f"{x['oc_win']:>8.1f}%{x['oc_base_win']:>6.1f}%"
              f"{x['t']:>+7.2f}{x['p']:>8.4f}  {'★' if x['sig_BH'] else ''}")

    # ---- 条件付き ----
    if "pbr" in panel.columns or True:
        from .conditional import load_pit_bps, attach_conditions
        panel2 = attach_conditions(panel, load_pit_bps(CFG.db_path), bars)
        d2 = panel2[panel2["x_oc"].notna()]
        print("\n" + "=" * 96)
        print("条件付き（日中リターンのみ）")
        print("=" * 96)
        subs = {
            "全体": d2,
            "バリュー(PBR<1)": d2[d2["pbr"] < 1.0],
            "底値圏(下位25%)": d2[d2["range_pos"] < 0.25],
            "高値圏(上位25%)": d2[d2["range_pos"] > 0.75],
        }
        print(f"{'条件':<18}{'型':>5}{'件数':>8}{'日中%':>9}{'勝率':>8}{'基準':>8}{'t':>7}{'p':>8}")
        print("-" * 72)
        for lab, sub in subs.items():
            for pid in (9, 10, 11, "ANY"):
                if pid == "ANY":
                    m = sub["ids"].apply(lambda s: len(s) > 0)
                else:
                    m = sub["ids"].apply(lambda s: pid in s)
                if m.sum() < 30:
                    continue
                s = stat(sub, m, "x_oc")
                if not s:
                    continue
                print(f"{lab:<18}{str(pid):>5}{s['n']:>8,}{s['excess_pct']:>+9.2f}"
                      f"{s['win']:>7.1f}%{s['base_win']:>7.1f}%"
                      f"{s['t']:>+7.2f}{s['p']:>8.4f}")

    print("\n" + "=" * 96)
    print(f"売買コストの目安：往復 {COST_ROUNDTRIP:.2f}%")
    print("日中の超過リターンがこれを上回らなければ、実行しても手数料負けする")
    best = res.loc[res["oc_excess"].idxmax()] if not res.empty else None
    if best is not None:
        print(f"最良でも 型{best['pattern_id']} の {best['oc_excess']:+.2f}%")

    out = os.path.join(CFG.out_dir, "backtest", "nextday.csv")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    res.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n保存: {out}")


if __name__ == "__main__":
    main()
