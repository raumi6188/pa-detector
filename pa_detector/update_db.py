# -*- coding: utf-8 -*-
"""J-Quants API **V2** から stock_data.db を更新する。

★ V1 と V2 はエンドポイントもカラム名も別物である ★

  上場銘柄一覧  /v1/listed/info        → /v2/equities/master
  株価四本値    /v1/prices/daily_quotes → /v2/equities/bars/daily
  財務情報      /v1/fins/statements     → /v2/fins/summary

  カラム名も短縮形になった：
    Open→O  High→H  Low→L  Close→C  Volume→Vo  TurnoverValue→Va
    AdjustmentFactor→AdjFactor
    調整済み四本値は AdjO / AdjH / AdjL / AdjC / AdjVo

  V2は調整済みOHLCVを全部くれるので、既存DBのように
  adj_close だけから比率を逆算する必要がない。
  そこで quotes テーブルに adj_open / adj_high / adj_low / adj_volume を
  追加して、そのまま保存する。

  配当は /v2/fins/summary に含まれる（DivAnn=実績年間, FDivAnn=予想年間）。
  Premiumプラン専用の /fins/dividend は不要。

  レスポンス形式：{"data": [...], "pagination_key": "..."}

使い方
------
  # まず疎通と実際のカラム名を確認する（これを最初に実行すること）
  python -m pa_detector.update_db --probe

  # 銘柄一覧
  python -m pa_detector.update_db --refresh-listed

  # 株価の差分更新
  python -m pa_detector.update_db --quotes

  # 財務（配当も同時に入る）
  python -m pa_detector.update_db --statements

  # 毎日の運用はこれ一発
  python -m pa_detector.update_db --all

  # 初期構築（期間指定）
  python -m pa_detector.update_db --quotes --from 2024-08-01

認証
----
  環境変数 JQUANTS_API_KEY に、ダッシュボードで発行したAPIキーを設定する。
  V1のリフレッシュトークン方式は廃止されている。
"""
from __future__ import annotations
import os
import sys
import json
import time
import sqlite3
import argparse
import datetime as dt
from typing import Optional, Any

import requests

BASE = "https://api.jquants.com/v2"

EP_MASTER = "/equities/master"
EP_BARS = "/equities/bars/daily"
EP_SUMMARY = "/fins/summary"


# ------------------------------------------------------------ API
class JQuants:
    def __init__(self, api_key: str, timeout: float = 30.0, sleep: float = 0.15):
        if not api_key:
            raise RuntimeError(
                "JQUANTS_API_KEY が未設定です。\n"
                "  set JQUANTS_API_KEY=（ダッシュボードで発行したAPIキー）")
        self.h = {"x-api-key": api_key}
        self.timeout = timeout
        self.sleep = sleep
        self.s = requests.Session()

    def get(self, path: str, params: dict | None = None,
            max_pages: int = 500) -> list:
        """ページネーションを辿って全件返す。V2は data キーに配列が入る。"""
        params = dict(params or {})
        rows, key, pages = [], None, 0
        while True:
            if key:
                params["pagination_key"] = key
            r = None
            for attempt in range(4):
                try:
                    r = self.s.get(f"{BASE}{path}", headers=self.h,
                                   params=params, timeout=self.timeout)
                    break
                except requests.RequestException:
                    if attempt == 3:
                        raise
                    time.sleep(2 ** attempt)
            if r.status_code == 429:                 # レートリミット
                time.sleep(10)
                continue
            if r.status_code in (400, 404):
                # 休場日や未提供期間。空扱いにする
                return rows
            if r.status_code == 403:
                raise PermissionError(
                    f"{path} → 403。プランで許可されていないか、パスが誤っている。\n"
                    f"  {r.text[:200]}")
            if r.status_code != 200:
                raise RuntimeError(f"{path} → HTTP {r.status_code}: {r.text[:200]}")

            j = r.json()
            body = j.get("data")
            if body is None:                          # 念のため旧形式にも対応
                body = next((v for v in j.values() if isinstance(v, list)), [])
            rows.extend(body)
            key = j.get("pagination_key")
            pages += 1
            time.sleep(self.sleep)
            if not key or pages >= max_pages:
                return rows


# ------------------------------------------------------------ 値の取り出し
def pick(d: dict, *names) -> Any:
    """候補名を順に試す。V2短縮名とV1名の両対応にするため。"""
    for n in names:
        if n in d and d[n] not in (None, "", "-", "－"):
            return d[n]
    return None


def fnum(x) -> Optional[float]:
    if x in (None, "", "-", "－"):
        return None
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def db_code(code) -> str:
    """5桁に揃える（J-Quantsは5桁で返す）。"""
    s = str(code).strip()
    return s + "0" if len(s) == 4 else s


def iso(d) -> str:
    s = str(d).strip()
    if len(s) == 8 and s.isdigit():          # 20260822 → 2026-08-22
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s[:10]


# ------------------------------------------------------------ スキーマ
def ensure_tables(con: sqlite3.Connection):
    con.execute("""CREATE TABLE IF NOT EXISTS quotes(
        code TEXT, date TEXT, open_p REAL, high_p REAL, low_p REAL,
        close_p REAL, volume REAL, adj_close REAL, adj_factor REAL,
        PRIMARY KEY(code, date))""")
    con.execute("""CREATE TABLE IF NOT EXISTS stocks(
        code TEXT PRIMARY KEY, name TEXT, sector_17_code TEXT,
        sector_17_name TEXT, sector_33_code TEXT, sector_33_name TEXT,
        market_code TEXT, registered_at TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS statements(
        code TEXT, disc_date TEXT, cur_per_type TEXT, np_value REAL,
        eq_value REAL, sales REAL, bps REAL, eps REAL, ta_value REAL,
        PRIMARY KEY(code, disc_date, cur_per_type))""")
    con.execute("""CREATE TABLE IF NOT EXISTS resync_log(
        code TEXT, split_date TEXT, resynced_at TEXT,
        PRIMARY KEY(code, split_date))""")
    con.execute("""CREATE TABLE IF NOT EXISTS dividends(
        code TEXT, disc_date TEXT, annual_dps REAL, forecast_flag TEXT,
        PRIMARY KEY(code, disc_date))""")

    # V2の調整済みOHLCVを保存する列を後付けする（既存DBを壊さない）
    have = {r[1] for r in con.execute("PRAGMA table_info('quotes')")}
    for col in ("adj_open", "adj_high", "adj_low", "adj_volume",
                "market_cap", "ex_right"):
        if col not in have:
            con.execute(f"ALTER TABLE quotes ADD COLUMN {col} REAL"
                        if col != "ex_right"
                        else "ALTER TABLE quotes ADD COLUMN ex_right TEXT")
            print(f"  quotes に {col} 列を追加")
    # 予想EPS（会社予想の通期）。PERを予想ベースで出せるようにする
    have_s = {r[1] for r in con.execute("PRAGMA table_info('statements')")}
    if "forecast_eps" not in have_s:
        con.execute("ALTER TABLE statements ADD COLUMN forecast_eps REAL")
        print("  statements に forecast_eps 列を追加")

    con.execute("CREATE INDEX IF NOT EXISTS ix_q_code_date ON quotes(code, date)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_q_date ON quotes(date)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_s_code ON statements(code)")
    con.commit()


def business_days(start: dt.date, end: dt.date):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += dt.timedelta(days=1)


# ------------------------------------------------------------ probe
def probe(api: JQuants):
    """疎通確認と、実際に返ってくるカラム名の表示。最初に必ず実行する。"""
    print("=" * 70)
    print("J-Quants V2 疎通確認")
    print("=" * 70)

    tests = [
        ("上場銘柄一覧", EP_MASTER, {"code": "72030"}),
        ("株価四本値", EP_BARS, {"code": "72030"}),
        ("財務情報", EP_SUMMARY, {"code": "72030"}),
    ]
    for label, ep, params in tests:
        print(f"\n■ {label}  GET {BASE}{ep}")
        try:
            rows = api.get(ep, params, max_pages=1)
        except Exception as e:
            print(f"  ✗ {e}")
            continue
        if not rows:
            print("  取得0件")
            continue
        print(f"  ✓ {len(rows)}件")
        keys = list(rows[0].keys())
        print(f"  カラム({len(keys)}): {keys}")
        print(f"  先頭レコード: "
              f"{json.dumps({k: rows[0][k] for k in keys[:10]}, ensure_ascii=False)}")
    print("\n" + "=" * 70)
    print("カラム名が上記と違う場合は、その一覧を伝えてもらえれば対応します。")


# ------------------------------------------------------------ 銘柄一覧
def refresh_listed(api: JQuants, con: sqlite3.Connection, prime_only: bool = False):
    """上場銘柄一覧。

    ★ V2の実際のカラム名（2026-08 に --probe で確認）★
      Code, CoName, CoNameEn, S17, S17Nm, S33, S33Nm, ScaleCat,
      Mkt, MktNm, Mrgn, MrgnNm, ProdCat, Date
      （V1の CompanyName / MarketCode / Sector17Code とは全部違う）
    """
    print("[銘柄一覧] 取得中…")
    rows = api.get(EP_MASTER, {"date": dt.date.today().isoformat()})
    if not rows:
        rows = api.get(EP_MASTER)          # date 指定が効かない場合
    recs = []
    for r in rows:
        mc = str(pick(r, "Mkt", "MktCode", "MarketCode") or "")
        if prime_only and mc != "0111":
            continue
        recs.append((
            db_code(pick(r, "Code")),
            pick(r, "CoName", "Name", "CompanyName"),
            pick(r, "S17", "Sec17Code", "Sector17Code"),
            pick(r, "S17Nm", "Sec17Name", "Sector17CodeName"),
            pick(r, "S33", "Sec33Code", "Sector33Code"),
            pick(r, "S33Nm", "Sec33Name", "Sector33CodeName"),
            mc, dt.date.today().isoformat(),
        ))
    if not recs:
        print("  ⚠ 0件。--probe でカラム名を確認してください")
        return
    con.executemany("INSERT OR REPLACE INTO stocks VALUES (?,?,?,?,?,?,?,?)", recs)
    con.commit()
    n_prime = con.execute(
        "SELECT COUNT(*) FROM stocks WHERE market_code='0111'").fetchone()[0]
    print(f"  {len(recs):,}銘柄を更新（うちプライム {n_prime:,}）")


# ------------------------------------------------------------ 株価
def update_quotes(api: JQuants, con: sqlite3.Connection,
                  since: str | None = None, until: str | None = None):
    """日付単位で取得する（銘柄単位より圧倒的に速い）。"""
    last = con.execute("SELECT MAX(date) FROM quotes").fetchone()[0]
    if since:
        start = dt.date.fromisoformat(since)
    elif last:
        start = dt.date.fromisoformat(iso(last)) + dt.timedelta(days=1)
    else:
        start = dt.date.today() - dt.timedelta(days=400)
    end = dt.date.fromisoformat(until) if until else dt.date.today()

    days = list(business_days(start, end))
    if not days:
        print("[株価] 更新対象なし（すでに最新）")
        return
    print(f"[株価] {start} 〜 {end}（{len(days)}営業日ぶん）")

    total, empty = 0, 0
    for i, d in enumerate(days, 1):
        ds = d.isoformat()
        try:
            rows = api.get(EP_BARS, {"date": ds})
        except PermissionError as e:
            print(f"  {e}")
            return
        except Exception as e:
            print(f"  {ds} 取得失敗: {e}")
            continue
        if not rows:
            empty += 1
            continue                       # 休場日
        recs = []
        for r in rows:
            o = fnum(pick(r, "O", "Open"))
            h = fnum(pick(r, "H", "High"))
            l = fnum(pick(r, "L", "Low"))
            c = fnum(pick(r, "C", "Close"))
            v = fnum(pick(r, "Vo", "Volume"))
            ao = fnum(pick(r, "AdjO", "AdjustmentOpen"))
            ah = fnum(pick(r, "AdjH", "AdjustmentHigh"))
            al = fnum(pick(r, "AdjL", "AdjustmentLow"))
            ac = fnum(pick(r, "AdjC", "AdjustmentClose"))
            av = fnum(pick(r, "AdjVo", "AdjustmentVolume"))
            af = fnum(pick(r, "AdjFactor", "AdjustmentFactor")) or 1.0
            mcap = fnum(pick(r, "MktCap"))          # 時価総額（V2で追加）
            exrt = pick(r, "ExRT")                  # 権利落種類 1分割/2併合/3ライツ
            if None in (o, h, l, c, v):
                continue
            recs.append((db_code(pick(r, "Code")), ds, o, h, l, c, v,
                         ac if ac is not None else c, af,
                         ao if ao is not None else o,
                         ah if ah is not None else h,
                         al if al is not None else l,
                         av if av is not None else v,
                         mcap, str(exrt) if exrt is not None else None))
        con.executemany(
            """INSERT OR REPLACE INTO quotes
               (code,date,open_p,high_p,low_p,close_p,volume,adj_close,adj_factor,
                adj_open,adj_high,adj_low,adj_volume,market_cap,ex_right)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", recs)
        con.commit()
        total += len(recs)
        if i % 10 == 0 or i == len(days):
            print(f"  {i}/{len(days)} 営業日  累計 {total:,}行")
    print(f"[株価] 完了 {total:,}行（休場 {empty}日）")

    _resync_adjustments(api, con)


def _resync_adjustments(api: JQuants, con: sqlite3.Connection, lookback: int = 120):
    """分割が起きた銘柄の過去の調整値を取り直す。

    分割は「発生日以前の全履歴の調整値が変わる」ため、
    差分更新だけでは古い行の調整値が古いままになる。

    検出は ExRT（権利落種類。1=分割 2=併合 3=ライツイシュー）と
    adj_factor≠1 の両方を見る。ExRTのほうが明示的だが、
    古い行には入っていないため併用する。
    """
    since = (dt.date.today() - dt.timedelta(days=lookback)).isoformat()
    # 分割等が起きた (銘柄, 日付) のうち、まだ再同期していないものだけを対象にする。
    # 毎回120日ぶんを取り直すと、同じ銘柄を何度も全履歴取得することになる。
    pending = con.execute(
        """SELECT DISTINCT q.code, q.date FROM quotes q
           WHERE q.date >= ?
             AND (q.adj_factor != 1.0
                  OR (q.ex_right IS NOT NULL AND q.ex_right NOT IN ('', '0')))
             AND NOT EXISTS (SELECT 1 FROM resync_log r
                             WHERE r.code = q.code AND r.split_date = q.date)""",
        (since,)).fetchall()
    if not pending:
        return
    codes = sorted({c for c, _ in pending})
    print(f"[調整] 未処理の分割等 {len(pending)}件／{len(codes)}銘柄の過去調整値を再取得")
    for i, code in enumerate(codes, 1):
        try:
            rows = api.get(EP_BARS, {"code": code})
        except Exception:
            continue
        recs = []
        for r in rows:
            d = pick(r, "Date")
            ac = fnum(pick(r, "AdjC", "AdjustmentClose"))
            if not d or ac is None:
                continue
            recs.append((
                fnum(pick(r, "AdjO")), fnum(pick(r, "AdjH")),
                fnum(pick(r, "AdjL")), ac, fnum(pick(r, "AdjVo")),
                fnum(pick(r, "AdjFactor")) or 1.0,
                db_code(pick(r, "Code")), iso(d)))
        con.executemany(
            """UPDATE quotes SET adj_open=?, adj_high=?, adj_low=?, adj_close=?,
               adj_volume=?, adj_factor=? WHERE code=? AND date=?""", recs)
        now = dt.datetime.now().isoformat(timespec="seconds")
        con.executemany(
            "INSERT OR REPLACE INTO resync_log VALUES (?,?,?)",
            [(c, d, now) for c, d in pending if c == code])
        con.commit()
        print(f"  {i}/{len(codes)}  {code}  {len(recs)}行")


# ------------------------------------------------------------ 財務・配当
def update_statements(api: JQuants, con: sqlite3.Connection, days_back: int = 7):
    """財務と配当を同時に取り込む。V2の /fins/summary は配当も含む。"""
    start = dt.date.today() - dt.timedelta(days=days_back)
    days = list(business_days(start, dt.date.today()))
    print(f"[財務・配当] 直近{days_back}日ぶん（{len(days)}営業日）")

    n_st, n_dv = 0, 0
    for i, d in enumerate(days, 1):
        ds = d.isoformat()
        try:
            rows = api.get(EP_SUMMARY, {"date": ds})
        except PermissionError as e:
            print(f"  {e}")
            return
        except Exception:
            continue
        if not rows:
            continue

        st, dv = [], []
        for r in rows:
            code = db_code(pick(r, "Code", "LocalCode"))
            disc = iso(pick(r, "DiscDate", "DisclosedDate") or ds)
            pt = pick(r, "CurPerType", "TypeOfCurrentPeriod")
            if not pt:
                continue
            st.append((
                code, disc, pt,
                fnum(pick(r, "NP", "Profit")),
                fnum(pick(r, "Eq", "Equity")),
                fnum(pick(r, "Sales", "NetSales")),
                fnum(pick(r, "BPS", "BookValuePerShare")),
                fnum(pick(r, "EPS", "EarningsPerShare")),
                fnum(pick(r, "TA", "TotalAssets")),
                fnum(pick(r, "FEPS", "ForecastEarningsPerShare")),
            ))
            # 配当：予想年間 → 実績年間 の順で採用
            dps = fnum(pick(r, "FDivAnn", "ForecastDividendPerShareAnnual"))
            flag = "F"
            if dps is None:
                dps = fnum(pick(r, "DivAnn", "ResultDividendPerShareAnnual"))
                flag = "R"
            if dps is not None:
                dv.append((code, disc, dps, flag))

        con.executemany(
            """INSERT OR REPLACE INTO statements
               (code,disc_date,cur_per_type,np_value,eq_value,sales,bps,eps,
                ta_value,forecast_eps)
               VALUES (?,?,?,?,?,?,?,?,?,?)""", st)
        con.executemany("INSERT OR REPLACE INTO dividends VALUES (?,?,?,?)", dv)
        con.commit()
        n_st += len(st); n_dv += len(dv)
        if i % 30 == 0 or i == len(days):
            print(f"  {i}/{len(days)} 営業日  財務{n_st:,}行 配当{n_dv:,}行")

    nd = con.execute("SELECT COUNT(DISTINCT code) FROM dividends").fetchone()[0]
    print(f"[財務・配当] 完了 財務{n_st:,}行／配当{n_dv:,}行（{nd:,}銘柄）")


# ------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("PA_DB_PATH", "stock_data.db"))
    ap.add_argument("--probe", action="store_true",
                    help="疎通確認とカラム名の表示。最初にこれを実行する")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--refresh-listed", action="store_true")
    ap.add_argument("--prime-only", action="store_true")
    ap.add_argument("--quotes", action="store_true")
    ap.add_argument("--statements", action="store_true")
    ap.add_argument("--dividends", action="store_true",
                    help="配当は --statements に含まれる（互換のため残置）")
    ap.add_argument("--stmt-days", type=int, default=7,
                    help="財務・配当を何日ぶん遡るか（日次運用は既定の7で十分。"
                         "初期構築時は400などを指定）")
    ap.add_argument("--from", dest="since", default=None)
    ap.add_argument("--to", dest="until", default=None)
    args = ap.parse_args()

    api = JQuants(os.environ.get("JQUANTS_API_KEY", ""))

    if args.probe:
        probe(api)
        return

    con = sqlite3.connect(args.db)
    ensure_tables(con)
    print(f"DB: {args.db}")

    do_all = args.all or not any(
        (args.refresh_listed, args.quotes, args.statements, args.dividends))

    if args.refresh_listed or do_all:
        refresh_listed(api, con, prime_only=args.prime_only)
    if args.quotes or do_all:
        update_quotes(api, con, since=args.since, until=args.until)
    if args.statements or args.dividends or do_all:
        update_statements(api, con, days_back=args.stmt_days)

    last = con.execute("SELECT MAX(date) FROM quotes").fetchone()[0]
    nq = con.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
    nc = con.execute("SELECT COUNT(DISTINCT code) FROM quotes").fetchone()[0]
    print(f"\n完了。最終営業日 {last}／{nq:,}行／{nc:,}銘柄")
    con.close()


if __name__ == "__main__":
    main()
