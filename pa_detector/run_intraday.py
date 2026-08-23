# -*- coding: utf-8 -*-
"""14:00 の候補検知メール。

    python -m pa_detector.run_intraday             # 通常実行（メール送信）
    python -m pa_detector.run_intraday --dry-run   # 送信せずHTMLを保存
    python -m pa_detector.run_intraday --eod       # 引け後の確定足で実行
"""
from __future__ import annotations
import os
import sys
import argparse
import datetime as dt
import numpy as np
import pandas as pd

from .config import CFG
from . import store, patterns, confidence, valuation, mailer
from .indicators import Context
from .kabus import KabuClient
from . import calibrate_volume


def merge_snapshot(bars: dict, snap: dict, today: str) -> dict:
    """過去足の末尾に当日の未完成足を1本足す。"""
    out = {}
    for code, df in bars.items():
        s = snap.get(code)
        if s is None:
            continue
        if str(df["date"].iloc[-1]) == today:
            df = df.iloc[:-1]              # 既に当日行があるなら差し替える
        row = pd.DataFrame([{"date": today, "o": s["o"], "h": s["h"],
                             "l": s["l"], "c": s["c"], "v": s["v"]}])
        out[code] = pd.concat([df, row], ignore_index=True)
    return out


def index_returns(bars: dict, codes) -> pd.Series | None:
    """等加重の擬似指数リターン。TOPIXが手元にない場合の代替。

    ★ 日付でソートしてから pct_change すること ★
      pd.concat(axis=1) が返すインデックスは銘柄ごとの日付の和集合で、
      昇順である保証がない。未ソートのまま pct_change すると、
      隣り合わない日付どうしの差分を取ってしまい、リターンが無意味になる。
      （実際にこれで型17が誤った判定をしていた）

    各銘柄の日次リターンを先に求めてから平均する。
    価格の平均から差分を取ると、値がさ株の影響が過大になるため。
    """
    rets = []
    for c in codes[:400]:
        d = bars.get(c)
        if d is not None and len(d) > 60:
            s = d.set_index("date")["c"].astype(float).sort_index()
            rets.append(s.pct_change())
    if not rets:
        return None
    m = pd.concat(rets, axis=1).sort_index()
    r = m.mean(axis=1, skipna=True)
    # 極端な値（分割の取りこぼし等）を除く
    return r.where(r.abs() < 0.25, 0.0)


def beta_of(df: pd.DataFrame, idx: pd.Series, window: int = 60):
    try:
        s = df.set_index("date")["c"].astype(float).pct_change()
        j = pd.concat([s, idx], axis=1, join="inner").dropna().tail(window)
        if len(j) < 30:
            return None
        x = j.iloc[:, 1].to_numpy()
        y = j.iloc[:, 0].to_numpy()
        vx = x.var()
        return float(np.cov(x, y)[0, 1] / vx) if vx > 0 else None
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--eod", action="store_true", help="引け後の確定足で実行")
    ap.add_argument("--limit", type=int, default=0, help="銘柄数を絞る（動作確認用）")
    ap.add_argument("--skip-if-not-today", action="store_true",
                    help="DBの最終営業日が本日でなければ送信せず終了する（休場日対策）")
    args = ap.parse_args()

    now = dt.datetime.now()
    today = now.strftime("%Y-%m-%d")
    intraday = not args.eod

    print(f"[1/6] DB接続 {CFG.db_path}")
    con = store.connect(CFG.db_path)

    # 休場日（祝日・年末年始）はDBの最終営業日が本日にならない。
    # クラウドで毎日走らせると空メールが届くので、その場合は何もせず終了する。
    if args.skip_if_not_today:
        last = con.execute(
            f"SELECT MAX({store.SCHEMA['col_date']}) "
            f"FROM {store.SCHEMA['quotes_table']}").fetchone()[0]
        if str(last)[:10] != today:
            print(f"      DB最終営業日 {last} ≠ 本日 {today} → 休場日と判断して終了")
            con.close()
            return
    uni = store.prime_universe(con)
    if args.limit:
        uni = uni.head(args.limit)
    codes = list(uni["code"].astype(str))
    names = dict(zip(uni["code"].astype(str), uni["name"]))
    print(f"      ユニバース {len(codes)} 銘柄")

    print(f"[2/6] 過去足の読み込み（{CFG.history_days}日）")
    bars = store.load_bars(con, codes, CFG.history_days)

    if intraday:
        print("[3/6] kabuステーションAPI からスナップショット取得")
        kc = KabuClient(CFG)
        kc.authenticate()
        snap = kc.snapshot(codes)
        print(f"      取得 {len(snap)} 銘柄")
        try:
            calibrate_volume.record(snap, now)   # 出来高比率の較正用に保存
        except Exception as e:
            print(f"      （較正記録スキップ: {e}）")
        bars = merge_snapshot(bars, snap, today)
        snapshot_time = now.strftime("%H:%M")
    else:
        snapshot_time = "大引け"

    print("[4/6] 補助データ")
    vals = store.load_valuation_inputs(con, codes).set_index("code")
    earn = store.recent_earnings_offsets(con, codes, bars)
    idx = index_returns(bars, codes)
    con.close()

    # 型4（引け際の売り叩きの否定）はCLVが引け際の値動きそのものなので
    # 場中は判定不能。確定足なら判定できるので除外を解除する。
    excluded = set(CFG.excluded_patterns)
    if not intraday and 4 in excluded:
        excluded.discard(4)
        print("      （確定足のため型4を判定対象に含めます）")

    print("[5/6] 判定")
    rows, passed = [], 0
    for code, df in bars.items():
        if len(df) < CFG.ma_long + 30:
            continue
        try:
            ctx = Context(code, df, CFG, intraday=intraday)
        except Exception:
            continue
        if not np.isfinite(ctx.atr) or ctx.atr <= 0:
            continue
        if ctx.turnover_med < CFG.min_turnover_median:
            continue
        passed += 1

        b = beta_of(df, idx) if idx is not None else None
        ir = None
        if idx is not None:
            aligned = idx.reindex(df["date"].astype(str)).fillna(0.0).to_numpy()
            ir = aligned

        # 前営業日までのデータで作った Context。
        # 「前日も成立していた型」を除外し、否定が成立した初日だけを報告する。
        prev_ctx = None
        prev_ir = None
        prev_earn = None
        if len(df) > CFG.ma_long + 31:
            try:
                prev_ctx = Context(code, df.iloc[:-1], CFG, intraday=False)
                if ir is not None:
                    prev_ir = ir[:-1]
                eo = earn.get(code)
                if eo:
                    # オフセットは「何営業日前か」なので、1日戻すと1減る
                    prev_earn = {o - 1 for o in eo if o - 1 >= 1}
            except Exception:
                prev_ctx = None

        hits = patterns.detect_all(
            ctx, excluded=excluded,
            idx_ret=ir, beta=b, earnings_offsets=earn.get(code),
            prev_ctx=prev_ctx, prev_idx_ret=prev_ir,
            prev_earnings_offsets=prev_earn)
        if not hits:
            continue
        hits = confidence.annotate(hits, CFG, intraday=intraday)

        v = vals.loc[code] if code in vals.index else None
        met = valuation.compute(ctx.c,
                                v["eps"] if v is not None else None,
                                v["bps"] if v is not None else None,
                                v["dps"] if v is not None else None)
        for h in hits:
            if h.confidence not in CFG.report_confidences:
                continue
            rows.append({
                "code": code, "name": names.get(code, ""),
                "pattern_id": h.pattern_id, "pattern": h.name,
                "rank": h.rank, "group": h.group,
                "conf": h.confidence, "margin": h.margin_atr,
                "baseline": h.baseline, "price": ctx.c,
                "note": h.note, **met,
            })

    print(f"      流動性通過 {passed} 銘柄／ヒット {len(rows)} 件")

    print("[6/6] レポート")
    stats = {"universe": len(codes), "passed": passed}
    html = mailer.build_html(rows, today, snapshot_time, stats,
                             intraday=intraday)

    os.makedirs(CFG.out_dir, exist_ok=True)
    hp = os.path.join(CFG.out_dir, f"pa_{today}.html")
    with open(hp, "w", encoding="utf-8") as f:
        f.write(html)
    if CFG.save_csv and rows:
        pd.DataFrame(rows).to_csv(
            os.path.join(CFG.out_dir, f"pa_{today}.csv"),
            index=False, encoding="utf-8-sig")

    if args.dry_run:
        print(f"      dry-run: {hp}")
    else:
        tag = "場中候補" if intraday else "確定"
        mailer.send(html, f"[PA検知/{tag}] {today} — {len(rows)}件", CFG)
        print("      送信完了")


if __name__ == "__main__":
    main()
