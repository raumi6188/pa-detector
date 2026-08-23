# -*- coding: utf-8 -*-
"""日中出来高比率 f(t) の較正。

14:00 時点の出来高をそのまま20日平均と比べると、全銘柄が「出来高不足」になる。
当日フル出来高を V_est = V_partial ÷ f(t) で推定するための f を、
自分の記録から実測する。

使い方：
  1. 毎日 14:00 のスナップショット時に --record を走らせて出来高を保存する
     （run_intraday が自動で記録するので通常は不要）
  2. 数週間ぶん貯まったら --fit で f を算出し、PA_VOL_FRACTION に設定する

    python -m pa_detector.calibrate_volume --record   # 場中に実行
    python -m pa_detector.calibrate_volume --fit      # 引け後に実行
"""
from __future__ import annotations
import os
import sys
import sqlite3
import datetime as dt
import numpy as np
import pandas as pd

from .config import CFG
from . import store

CAL_DB = os.path.join(CFG.out_dir, "volume_calibration.db")


def _con():
    os.makedirs(CFG.out_dir, exist_ok=True)
    c = sqlite3.connect(CAL_DB)
    c.execute("""CREATE TABLE IF NOT EXISTS partial(
        date TEXT, hhmm TEXT, code TEXT, volume REAL,
        PRIMARY KEY(date, hhmm, code))""")
    return c


def record(snapshot: dict, when: dt.datetime | None = None):
    """14:00 時点の出来高を保存する。run_intraday から呼ばれる。"""
    when = when or dt.datetime.now()
    c = _con()
    c.executemany(
        "INSERT OR REPLACE INTO partial VALUES (?,?,?,?)",
        [(when.strftime("%Y-%m-%d"), when.strftime("%H:%M"), code, s["v"])
         for code, s in snapshot.items()])
    c.commit(); c.close()


def fit(min_days: int = 5):
    """保存した場中出来高と、確定した日次出来高を突き合わせて f を推定する。"""
    if not os.path.exists(CAL_DB):
        print("較正データがありません。まず場中に --record を走らせてください。")
        return None
    cc = _con()
    part = pd.read_sql_query("SELECT * FROM partial", cc)
    cc.close()
    if part.empty:
        print("較正データが空です。")
        return None

    con = store.connect(CFG.db_path)
    codes = sorted(part["code"].unique().tolist())
    bars = store.load_bars(con, codes, 90)
    con.close()

    recs = []
    for _, r in part.iterrows():
        b = bars.get(str(r["code"]))
        if b is None:
            continue
        m = b[b["date"].astype(str) == str(r["date"])]
        if m.empty:
            continue
        full = float(m["v"].iloc[0])
        if full <= 0 or r["volume"] <= 0:
            continue
        recs.append({"date": r["date"], "hhmm": r["hhmm"],
                     "ratio": float(r["volume"]) / full})

    if not recs:
        print("突合できるレコードがありません（確定足がまだ入っていない可能性）。")
        return None

    d = pd.DataFrame(recs)
    n_days = d["date"].nunique()
    print(f"突合 {len(d):,} レコード／{n_days} 営業日")
    if n_days < min_days:
        print(f"※ {min_days}営業日以上たまってから使うことを勧めます。")

    for hhmm, g in d.groupby("hhmm"):
        q = g["ratio"].quantile([0.25, 0.5, 0.75])
        print(f"  {hhmm}  中央値 f={q[0.5]:.3f}  "
              f"（四分位 {q[0.25]:.3f}〜{q[0.75]:.3f}／n={len(g)}）")
        print(f"        → set PA_VOL_FRACTION={q[0.5]:.3f}")
    return d


if __name__ == "__main__":
    if "--fit" in sys.argv:
        fit()
    else:
        print(__doc__)
