# -*- coding: utf-8 -*-
"""stock_data.db からの読み込み。

★ このDBの重要な性質 ★
  quotes の open_p / high_p / low_p / close_p は **未調整** である。
  調整済みなのは adj_close だけ。分割銘柄では close_p が不連続に飛ぶ。

  例）9533 は 2026-03-30 に1:4分割
      close_p  : 4862 → 1243.5   （不連続。前日安値との比較が壊れる）
      adj_close: 1215.5 → 1243.5 （連続）

  そのまま使うと、分割日に該当銘柄が一斉に「安値を割った」と誤検知する。
  そこで比率 r = adj_close ÷ close_p を日ごとに求め、O/H/L に掛けて
  調整後系列を復元する。出来高は逆に r で割る（分割で株数が増えるため）。

  コードは5桁（13320 = 1332 + 末尾0）。kabuステーションAPIは4桁なので
  to_kabu_code() / to_db_code() で相互変換する。

  配当は dividends テーブル（update_db.py が作る）から読む。無ければ利回りは空欄。
"""
from __future__ import annotations
import sqlite3
import datetime as dt
import pandas as pd
import numpy as np

SCHEMA = {
    "quotes_table": "quotes",
    "col_code": "code",
    "col_date": "date",
    "col_open": "open_p",
    "col_high": "high_p",
    "col_low": "low_p",
    "col_close": "close_p",
    "col_adj_close": "adj_close",
    "col_volume": "volume",

    "listed_table": "stocks",
    "col_name": "name",
    "col_market": "market_code",
    "prime_code": "0111",

    "stmt_table": "statements",
    "col_stmt_code": "code",
    "col_stmt_date": "disc_date",
    "col_period": "cur_per_type",
    "col_eps": "eps",
    "col_bps": "bps",

    "div_table": "dividends",      # update_db.py が作成。無ければ利回りは空欄
    "col_div_code": "code",
    "col_div_date": "disc_date",
    "col_div_annual": "annual_dps",
}


# ------------------------------------------------------------ コード変換
def to_kabu_code(db_code) -> str:
    """5桁DBコード → 4桁kabuコード。13320 → 1332"""
    s = str(db_code).strip()
    return s[:4] if (len(s) == 5 and s.endswith("0")) else s


def to_db_code(kabu_code) -> str:
    """4桁kabuコード → 5桁DBコード。1332 → 13320"""
    s = str(kabu_code).strip()
    return s + "0" if len(s) == 4 else s


# ------------------------------------------------------------ 接続・点検
def connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def has_table(con, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,)).fetchone() is not None


def inspect(db_path: str):
    con = connect(db_path)
    for t in [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]:
        n = con.execute(f"SELECT COUNT(*) FROM '{t}'").fetchone()[0]
        cols = [r[1] for r in con.execute(f"PRAGMA table_info('{t}')")]
        print(f"{t} ({n:,} rows): {cols}")
    con.close()


def health_check(db_path: str, max_stale_days: int = 5) -> bool:
    """鮮度と充足を確認する。運用前に必ず実行すること。"""
    s = SCHEMA
    con = connect(db_path)
    last = con.execute(
        f"SELECT MAX({s['col_date']}) FROM {s['quotes_table']}").fetchone()[0]
    n_code = con.execute(
        f"SELECT COUNT(DISTINCT {s['col_code']}) FROM {s['quotes_table']}").fetchone()[0]
    n_prime = con.execute(
        f"SELECT COUNT(*) FROM {s['listed_table']} WHERE {s['col_market']}=?",
        (s["prime_code"],)).fetchone()[0]
    has_div = has_table(con, s["div_table"])
    n_div = con.execute(f"SELECT COUNT(*) FROM {s['div_table']}").fetchone()[0] \
        if has_div else 0
    con.close()

    try:
        gap = (dt.date.today() - dt.date.fromisoformat(str(last))).days
    except Exception:
        gap = None

    print(f"  最終営業日 : {last}" + (f"（{gap}日前）" if gap is not None else ""))
    print(f"  収録銘柄数 : {n_code}（stocks のプライム {n_prime}）")
    print(f"  配当テーブル: "
          f"{'あり ' + format(n_div, ',') + '行' if has_div else 'なし（利回りは空欄）'}")

    ok = True
    if gap is not None and gap > max_stale_days:
        print(f"  ⚠ データが{gap}日古い。update_db.py で更新すること")
        ok = False
    if n_prime < 1000:
        print(f"  ⚠ プライム{n_prime}銘柄のみ（東証プライムは約1,600）。"
              f"update_db.py --refresh-listed で銘柄一覧を更新できる")
        ok = False
    if ok:
        print("  ✓ 問題なし")
    return ok


# ------------------------------------------------------------ ユニバース
def prime_universe(con) -> pd.DataFrame:
    s = SCHEMA
    q = f"""SELECT {s['col_code']} AS code, {s['col_name']} AS name
            FROM {s['listed_table']} WHERE {s['col_market']} = ?"""
    df = pd.read_sql_query(q, con, params=(s["prime_code"],))
    df["code"] = df["code"].astype(str)
    return df.drop_duplicates("code")


# ------------------------------------------------------------ 株価
def load_bars(con, codes, days: int = 180) -> dict:
    """{code: DataFrame(date,o,h,l,c,v)}。分割調整済み・日付昇順。

    update_db.py（V2）で入れた行には adj_open/adj_high/adj_low/adj_volume が
    そのまま入っているので、それを使う。
    無い行（旧データ）は adj_close からの比率で復元する。
    """
    s = SCHEMA
    have = {r[1] for r in con.execute(f"PRAGMA table_info('{s['quotes_table']}')")}
    has_adj_ohlc = {"adj_open", "adj_high", "adj_low", "adj_volume"} <= have

    extra = (", adj_open AS o_adj, adj_high AS h_adj, adj_low AS l_adj, "
             "adj_volume AS v_adj") if has_adj_ohlc else ""
    ph = ",".join("?" * len(codes))
    q = f"""SELECT {s['col_code']} AS code, {s['col_date']} AS date,
                   {s['col_open']} AS o_raw, {s['col_high']} AS h_raw,
                   {s['col_low']} AS l_raw, {s['col_close']} AS c_raw,
                   {s['col_adj_close']} AS c_adj, {s['col_volume']} AS v_raw{extra}
            FROM {s['quotes_table']}
            WHERE {s['col_code']} IN ({ph})
            ORDER BY {s['col_code']}, {s['col_date']}"""
    df = pd.read_sql_query(q, con, params=[str(c) for c in codes])
    df = df.dropna(subset=["o_raw", "h_raw", "l_raw", "c_raw", "v_raw"])
    df = df[df["c_raw"] > 0]
    if df.empty:
        return {}

    # ---- 調整済み系列の決定 ----
    # 比率 r = 調整後終値 ÷ 未調整終値。調整済みOHLCVが無い行の復元に使う。
    r = df["c_adj"] / df["c_raw"]
    r = r.where(np.isfinite(r) & (r > 0), 1.0)
    df["c"] = df["c_adj"].where(df["c_adj"].notna() & (df["c_adj"] > 0), df["c_raw"])

    if has_adj_ohlc:
        df["o"] = df["o_adj"].where(df["o_adj"].notna(), df["o_raw"] * r)
        df["h"] = df["h_adj"].where(df["h_adj"].notna(), df["h_raw"] * r)
        df["l"] = df["l_adj"].where(df["l_adj"].notna(), df["l_raw"] * r)
        df["v"] = df["v_adj"].where(df["v_adj"].notna(), df["v_raw"] / r)
    else:
        df["o"] = df["o_raw"] * r
        df["h"] = df["h_raw"] * r
        df["l"] = df["l_raw"] * r
        df["v"] = df["v_raw"] / r

    out = {}
    for code, g in df.groupby("code"):
        g = g.tail(days)
        if len(g) < 40:
            continue
        out[str(code)] = g[["date", "o", "h", "l", "c", "v"]].reset_index(drop=True)
    return out


# ------------------------------------------------------------ 財務・配当
PERIOD_ORDER = {"1Q": 1, "2Q": 2, "3Q": 3, "FY": 4}


def _ttm_eps(g: pd.DataFrame) -> float:
    """1銘柄の開示履歴から TTM（直近12か月）EPS を求める。

    ★ このDBの eps は「その期首からの累計」であって通期の値ではない ★
      例）中外製薬 2026-04-24 の 1Q eps = 70.13
          これをそのまま使うと PER が約4倍に出る（実際に112.5倍と誤表示された）

    TTM = 直近の累計 ＋ 前期のFY − 前年同期の累計
      中外製薬なら 70.13 + 263.73 − 59.09 = 274.77 → PER 28.7倍（妥当）

    最新が FY ならそれがそのまま通期値なので、その値を返す。
    前年同期が取れない場合は期種で年率換算する（季節性があるため精度は落ちる）。
    """
    g = g[g["eps"].notna()].sort_values("disclosed")
    if g.empty:
        return float("nan")

    last = g.iloc[-1]
    p = str(last["period"])
    if p == "FY":
        return float(last["eps"])

    # 直近のFY（＝前期の通期実績）
    fy = g[g["period"] == "FY"]
    prev_fy = float(fy.iloc[-1]["eps"]) if len(fy) else None

    # 前年同期の累計（同じ期種で、直近より前のもの）
    same = g[(g["period"] == p) & (g["disclosed"] < last["disclosed"])]
    prev_same = float(same.iloc[-1]["eps"]) if len(same) else None

    if prev_fy is not None and prev_same is not None:
        return float(last["eps"]) + prev_fy - prev_same

    # 補完：期種で年率換算
    mult = {"1Q": 4.0, "2Q": 2.0, "3Q": 4.0 / 3.0}.get(p)
    if mult:
        return float(last["eps"]) * mult
    return float("nan")


def load_valuation_inputs(con, codes) -> pd.DataFrame:
    """PER/PBR/利回り用の EPS・BPS・DPS。

    EPSの優先順位：
      1. forecast_eps（会社予想の通期EPS。V2の FEPS。update_db.py が入れる）
      2. TTM（_ttm_eps を参照。予想が無い銘柄向けの代替）

    予想が取れるなら予想PERのほうが実務的に有用なので優先する。
    BPSはこのDBでは四半期開示に入っていないことが多いため、本決算(FY)の最新から取る。
    """
    s = SCHEMA
    have = {r[1] for r in con.execute(f"PRAGMA table_info('{s['stmt_table']}')")}
    has_feps = "forecast_eps" in have
    feps_col = ", forecast_eps AS feps" if has_feps else ""

    ph = ",".join("?" * len(codes))
    codes_s = [str(c) for c in codes]
    q = f"""SELECT {s['col_stmt_code']} AS code, {s['col_stmt_date']} AS disclosed,
                   {s['col_period']} AS period,
                   {s['col_eps']} AS eps, {s['col_bps']} AS bps{feps_col}
            FROM {s['stmt_table']}
            WHERE {s['col_stmt_code']} IN ({ph})
            ORDER BY {s['col_stmt_code']}, {s['col_stmt_date']}"""
    try:
        df = pd.read_sql_query(q, con, params=codes_s)
    except Exception:
        return pd.DataFrame(columns=["code", "eps", "bps", "dps"])
    if df.empty:
        return pd.DataFrame(columns=["code", "eps", "bps", "dps"])

    df["code"] = df["code"].astype(str)
    for c in (["eps", "bps"] + (["feps"] if has_feps else [])):
        df[c] = pd.to_numeric(df[c], errors="coerce")

    ttm = (df.groupby("code").apply(_ttm_eps, include_groups=False)
             .rename("eps_ttm").reset_index())

    if has_feps:
        fc = (df[df["feps"].notna()].sort_values("disclosed")
                .groupby("code").tail(1)[["code", "feps"]])
        eps = ttm.merge(fc, on="code", how="outer")
        eps["eps"] = eps["feps"].where(eps["feps"].notna(), eps["eps_ttm"])
        eps = eps[["code", "eps"]]
    else:
        eps = ttm.rename(columns={"eps_ttm": "eps"})

    fy = df[(df["period"] == "FY") & df["bps"].notna()]
    bps = fy.sort_values("disclosed").groupby("code").tail(1)[["code", "bps"]]
    out = eps.merge(bps, on="code", how="outer")

    # ---- 配当（あれば） ----
    out["dps"] = np.nan
    if has_table(con, s["div_table"]):
        try:
            dq = f"""SELECT {s['col_div_code']} AS code, {s['col_div_date']} AS d,
                            {s['col_div_annual']} AS dps
                     FROM {s['div_table']}
                     WHERE {s['col_div_code']} IN ({ph})
                     ORDER BY {s['col_div_code']}, {s['col_div_date']}"""
            dv = pd.read_sql_query(dq, con, params=codes_s)
            if not dv.empty:
                dv["code"] = dv["code"].astype(str)
                dv["dps"] = pd.to_numeric(dv["dps"], errors="coerce")
                dv = (dv[dv["dps"].notna()].sort_values("d")
                        .groupby("code").tail(1)[["code", "dps"]])
                out = out.drop(columns=["dps"]).merge(dv, on="code", how="left")
        except Exception:
            pass

    return out.reset_index(drop=True)


def recent_earnings_offsets(con, codes, bars: dict, k: int = 15) -> dict:
    """{code: {何営業日前が決算反応日か}}。型18で使う。"""
    s = SCHEMA
    ph = ",".join("?" * len(codes))
    q = f"""SELECT {s['col_stmt_code']} AS code, {s['col_stmt_date']} AS disclosed
            FROM {s['stmt_table']} WHERE {s['col_stmt_code']} IN ({ph})"""
    try:
        df = pd.read_sql_query(q, con, params=[str(c) for c in codes])
    except Exception:
        return {}
    out = {}
    for code, g in df.groupby("code"):
        code = str(code)
        b = bars.get(code)
        if b is None:
            continue
        dates = list(b["date"].astype(str))
        offs = set()
        for d in g["disclosed"].astype(str):
            after = [x for x in dates if x > d]
            if not after:
                continue
            off = len(dates) - 1 - dates.index(after[0])
            if 1 <= off <= k:
                offs.add(off)
        if offs:
            out[code] = offs
    return out


if __name__ == "__main__":
    import sys
    from .config import CFG
    if "--inspect" in sys.argv:
        inspect(CFG.db_path)
    elif "--health" in sys.argv:
        health_check(CFG.db_path)
    else:
        print("使い方: python -m pa_detector.store --inspect | --health")
