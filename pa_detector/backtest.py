# -*- coding: utf-8 -*-
"""過去データで18型の優位性を検証する。

    python -m pa_detector.backtest --days 250
    python -m pa_detector.backtest --days 500 --workers 4

考え方
------
全 (銘柄, 営業日) をセルとして扱い、各セルについて

    ・その日にどの型が成立したか
    ・その後 5日／20日の超過リターン

を記録する。超過リターンは同日の全銘柄の中央値を引いたもので、
市場全体の上下（ベータ）を除いた個別要因だけを見る。

これで型ごとに 2×2 の分割表が作れる。

              超過 > 0    超過 <= 0
    検知あり      a           b
    検知なし      c           d

    LR = P(検知 | 上昇) / P(検知 | 上昇せず)
       = [a/(a+c)] / [b/(b+d)]

LR が 1 に近ければ「もっともらしいが情報量がない」。
ベイズ更新が何も起こらないということである。

★ 先読みバイアスについて ★
  判定は fastctx.Precomputed 経由で行う。指標はすべて rolling、
  スイング点は確定までの遅延を考慮済みで、live 版と全営業日一致することを
  照合済み（fastctx.verify_against_live）。
  超過リターンは判定日の *翌日以降* の終値からしか計算しない。

★ 多重比較について ★
  18型 × 2期間 = 36回の検定を行うので、有意水準5%なら
  何も効いていなくても2個程度は「有意」になる。
  Benjamini-Hochberg で偽発見率を制御した結果も併記する。
"""
from __future__ import annotations
import os
import sys
import argparse
import datetime as dt
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import pandas as pd

from .config import CFG
from . import store, patterns
from .fastctx import Precomputed

HORIZONS = (5, 20)


# ------------------------------------------------------------ 1銘柄の処理
def scan_stock(args):
    """1銘柄について、全営業日の検知結果と先行きリターンを返す。"""
    code, df, excluded, min_start = args
    try:
        pre = Precomputed(code, df, CFG)
    except Exception:
        return []

    c = pre.c
    n = len(df)
    dates = df["date"].astype(str).to_numpy()
    rows = []

    prev_ctx = None
    for t in range(min_start, n):
        ctx = pre.context(t)
        if not np.isfinite(ctx.atr) or ctx.atr <= 0:
            continue
        if ctx.turnover_med < CFG.min_turnover_median:
            continue

        if prev_ctx is None or prev_ctx.i != t - 1:
            prev_ctx = pre.context(t - 1)
        hits = patterns.detect_all(ctx, excluded=excluded, prev_ctx=prev_ctx)
        prev_ctx = ctx

        rec = {"code": code, "date": dates[t], "t": t, "close": c[t]}
        # 先行きリターン（判定日の終値から k 営業日後の終値まで）
        for k in HORIZONS:
            rec[f"r{k}"] = (c[t + k] / c[t] - 1.0) if t + k < n else np.nan
        rec["pids"] = tuple(h.pattern_id for h in hits)
        rec["margins"] = tuple(round(h.margin_atr, 3) for h in hits)
        rows.append(rec)
    return rows


# ------------------------------------------------------------ 統計
def two_by_two(panel: pd.DataFrame, pid: int, k: int):
    """型 pid・期間 k の 2×2 分割表と諸統計を返す。"""
    col = f"x{k}"                     # 超過リターン
    d = panel[panel[col].notna()]
    if len(d) == 0:
        return None
    det = d["pids"].apply(lambda p: pid in p)
    up = d[col] > 0

    a = int((det & up).sum())
    b = int((det & ~up).sum())
    c_ = int((~det & up).sum())
    dd = int((~det & ~up).sum())
    if a + b == 0:
        return None

    p_det_up = a / (a + c_) if (a + c_) else np.nan
    p_det_dn = b / (b + dd) if (b + dd) else np.nan
    lr = p_det_up / p_det_dn if p_det_dn else np.nan

    hit = d.loc[det, col]
    base = d.loc[~det, col]
    mean_hit = float(hit.mean())
    mean_base = float(base.mean())

    # Welch の t 検定（等分散を仮定しない）
    n1, n2 = len(hit), len(base)
    s1, s2 = hit.var(ddof=1), base.var(ddof=1)
    se = np.sqrt(s1 / n1 + s2 / n2) if n1 > 1 and n2 > 1 else np.nan
    tstat = (mean_hit - mean_base) / se if se and se > 0 else np.nan
    # 自由度が大きいので正規近似で両側p値
    from math import erfc
    pval = erfc(abs(tstat) / np.sqrt(2)) if np.isfinite(tstat) else np.nan

    return {
        "pattern_id": pid, "rank": patterns.RANK[pid], "name": patterns.NAMES[pid],
        "horizon": k, "n_hit": a + b,
        "a": a, "b": b, "c": c_, "d": dd,
        "win_hit": a / (a + b),
        "win_base": c_ / (c_ + dd) if (c_ + dd) else np.nan,
        "LR": lr,
        "mean_hit_pct": mean_hit * 100,
        "mean_base_pct": mean_base * 100,
        "excess_pct": (mean_hit - mean_base) * 100,
        "t": tstat, "p": pval,
    }


def benjamini_hochberg(pvals, q=0.10):
    """偽発見率 q で有意と判定されるものに True を立てる。"""
    p = np.asarray(pvals, dtype=float)
    ok = np.isfinite(p)
    out = np.zeros(len(p), dtype=bool)
    idx = np.where(ok)[0]
    if len(idx) == 0:
        return out
    order = idx[np.argsort(p[idx])]
    m = len(order)
    thresh = 0
    for i, j in enumerate(order, 1):
        if p[j] <= q * i / m:
            thresh = i
    if thresh:
        out[order[:thresh]] = True
    return out


# ------------------------------------------------------------ 本体
def _ckpt_path(out_dir): return os.path.join(out_dir, "_scan_checkpoint.csv")


def run(days: int, workers: int, excluded: set, out_dir: str, limit: int = 0,
        resume: bool = True, budget: float = 0.0):
    print(f"[1/5] DB読み込み {CFG.db_path}")
    con = store.connect(CFG.db_path)
    uni = store.prime_universe(con)
    if limit:
        uni = uni.head(limit)
    codes = list(uni["code"].astype(str))
    names = dict(zip(uni["code"].astype(str), uni["name"]))
    # 判定に必要な助走期間 + 評価期間
    bars = store.load_bars(con, codes, days + CFG.ma_long + 60)
    con.close()
    print(f"      {len(bars)} 銘柄")

    min_start = CFG.ma_long + 32
    targets = [c for c, df in bars.items() if len(df) > min_start + 30]
    n_cpu = os.cpu_count() or 1
    if workers > n_cpu:
        print(f"      CPU {n_cpu}コアのため並列度を {n_cpu} に下げます")
        workers = n_cpu
    print(f"[2/5] 判定（{len(targets)}銘柄・{workers}並列）")

    os.makedirs(out_dir, exist_ok=True)
    ck = _ckpt_path(out_dir)

    # 中断からの再開。済んだ銘柄は飛ばす。
    rows = []
    done_codes = set()
    if resume and os.path.exists(ck):
        prev = pd.read_csv(ck)
        prev["pids"] = prev["pids"].apply(
            lambda x: tuple(int(v) for v in str(x).split("|") if v != "" and v != "nan"))
        rows = prev.to_dict("records")
        done_codes = set(prev["code"].astype(str))
        print(f"      チェックポイントから再開：{len(done_codes)}銘柄・"
              f"{len(rows):,}セルを読み込み")
        targets = [c for c in targets if c not in done_codes]
        print(f"      残り {len(targets)} 銘柄")

    t0 = dt.datetime.now()

    def save_ckpt():
        if not rows:
            return
        d = pd.DataFrame(rows)
        d["pids"] = d["pids"].apply(lambda p: "|".join(str(x) for x in p))
        d.drop(columns=[c for c in ("margins",) if c in d.columns]).to_csv(
            ck, index=False, encoding="utf-8")

    def progress(i):
        el = (dt.datetime.now() - t0).total_seconds()
        rate = i / el if el > 0 else 0
        eta = (len(targets) - i) / rate if rate > 0 else 0
        print(f"      {i}/{len(targets)}  {el:.0f}秒経過  残り約{eta:.0f}秒"
              f"  セル{len(rows):,}", flush=True)

    if workers > 1:
        # まとめて submit するとタスクと結果を同時に抱えてメモリを食う。
        # バッチに区切って逐次回収する。
        BATCH = 60
        done = 0
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for s0 in range(0, len(targets), BATCH):
                chunk = targets[s0:s0 + BATCH]
                futs = [ex.submit(scan_stock,
                                  (c, bars[c], excluded, min_start)) for c in chunk]
                for f in as_completed(futs):
                    rows.extend(f.result())
                    done += 1
                progress(done)
                save_ckpt()
                if budget and (dt.datetime.now() - t0).total_seconds() > budget:
                    print(f"      時間予算に達したので中断。再実行で続きから走ります。")
                    return None
    else:
        for i, c in enumerate(targets, 1):
            rows.extend(scan_stock((c, bars[c], excluded, min_start)))
            if i % 50 == 0 or i == len(targets):
                progress(i)
                save_ckpt()
            if budget and (dt.datetime.now() - t0).total_seconds() > budget:
                save_ckpt()
                print(f"      時間予算 {budget:.0f}秒に達したので中断しました。"
                      f"同じコマンドで再実行すると続きから走ります。")
                return None

    save_ckpt()
    panel = pd.DataFrame(rows)
    print(f"      セル数 {len(panel):,}（銘柄×営業日）")
    if panel.empty:
        print("データがありません")
        return

    print("[3/5] 超過リターンの計算")
    # 同日の全銘柄の中央値を引く＝市場全体の動きを除く
    for k in HORIZONS:
        med = panel.groupby("date")[f"r{k}"].transform("median")
        panel[f"x{k}"] = panel[f"r{k}"] - med

    n_days = panel["date"].nunique()
    print(f"      対象 {n_days} 営業日／{panel['code'].nunique()} 銘柄")

    print("[4/5] 型ごとの統計")
    stats = []
    for pid in sorted(patterns.DETECTORS, key=lambda p: patterns.RANK[p]):
        if pid in excluded:
            continue
        for k in HORIZONS:
            s = two_by_two(panel, pid, k)
            if s:
                stats.append(s)
    res = pd.DataFrame(stats)
    if res.empty:
        print("ヒットがありません")
        return
    res["significant_BH"] = benjamini_hochberg(res["p"].to_numpy(), q=0.10)

    print("[5/5] 出力")
    os.makedirs(out_dir, exist_ok=True)
    stamp = dt.date.today().isoformat()
    res.to_csv(os.path.join(out_dir, f"backtest_stats_{stamp}.csv"),
               index=False, encoding="utf-8-sig")
    panel.drop(columns=["margins"]).to_csv(
        os.path.join(out_dir, f"backtest_panel_{stamp}.csv"),
        index=False, encoding="utf-8-sig")

    report(res, panel, n_days)
    return res, panel


def report(res: pd.DataFrame, panel: pd.DataFrame, n_days: int):
    print("\n" + "=" * 88)
    print(f"バックテスト結果（{n_days}営業日・{panel['code'].nunique()}銘柄・"
          f"{len(panel):,}セル）")
    print("=" * 88)
    print("超過リターン = 個別リターン − 同日の全銘柄中央値（市場全体の動きを除いたもの）")

    for k in HORIZONS:
        r = res[res.horizon == k].sort_values("rank")
        print(f"\n■ {k}営業日後")
        print(f"{'順':>3} {'型':>3} {'名称':<26} {'件数':>6} {'勝率':>6} "
              f"{'基準':>6} {'超過%':>7} {'LR':>6} {'t':>6} {'p':>7} {'BH'}")
        print("-" * 88)
        for _, x in r.iterrows():
            sig = "★" if x["significant_BH"] else ""
            print(f"{x['rank']:>3} {x['pattern_id']:>3} {x['name'][:26]:<26} "
                  f"{x['n_hit']:>6} {x['win_hit']*100:>5.1f}% "
                  f"{x['win_base']*100:>5.1f}% {x['excess_pct']:>+7.2f} "
                  f"{x['LR']:>6.2f} {x['t']:>+6.2f} {x['p']:>7.4f} {sig}")

    # 事前の否定度ランクと実測の順位相関
    print("\n" + "=" * 88)
    print("事前ランク（解説書の5基準）と実測の順位相関")
    print("=" * 88)
    for k in HORIZONS:
        r = res[(res.horizon == k) & res["excess_pct"].notna()]
        if len(r) < 4:
            continue
        rho = spearman(r["rank"].to_numpy(), (-r["excess_pct"]).to_numpy())
        rho_lr = spearman(r["rank"].to_numpy(), (-r["LR"]).to_numpy())
        print(f"  {k}日後  超過リターンとの相関 ρ={rho:+.3f}／LRとの相関 ρ={rho_lr:+.3f}")
    print("  ρ が正なら、事前に立てた否定度の順位が実測を捉えていたことになる")
    print("  ρ が 0 付近なら、あの心理的なフレームワークは的外れだったということ")


def spearman(x, y):
    rx = pd.Series(x).rank().to_numpy()
    ry = pd.Series(y).rank().to_numpy()
    if len(rx) < 2:
        return np.nan
    return float(np.corrcoef(rx, ry)[0, 1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=250, help="評価する営業日数")
    ap.add_argument("--workers", type=int,
                    default=max(1, min(4, (os.cpu_count() or 1))))
    ap.add_argument("--limit", type=int, default=0, help="銘柄数を絞る（動作確認用）")
    ap.add_argument("--out", default=os.path.join(CFG.out_dir, "backtest"))
    ap.add_argument("--budget", type=float, default=0.0,
                    help="この秒数を超えたら中断して保存（再実行で続きから）")
    ap.add_argument("--fresh", action="store_true",
                    help="チェックポイントを無視して最初からやり直す")
    ap.add_argument("--exclude", default="",
                    help="除外する型（既定は無し。全型を検証する）")
    args = ap.parse_args()

    excluded = {int(x) for x in args.exclude.split(",") if x.strip()}
    # 型17は擬似指数、型18は決算日が要るのでバックテストからは外す
    excluded |= {17, 18}
    print(f"除外: {sorted(excluded)}（17=逆行高・18=決算売りは別途対応）")

    if args.fresh:
        ck = _ckpt_path(args.out)
        if os.path.exists(ck):
            os.remove(ck)
            print("チェックポイントを削除しました")
    run(args.days, args.workers, excluded, args.out, args.limit,
        resume=not args.fresh, budget=args.budget)


if __name__ == "__main__":
    main()
