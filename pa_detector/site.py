# -*- coding: utf-8 -*-
"""検知結果と検証結果を1枚のWebページにまとめる。

    python -m pa_detector.site

results/*.csv を読んで docs/index.html を生成する。
GitHub Pages が docs/ を配信する設定になっていれば、そのまま公開される。

このページの立場
----------------
検知結果を「有望銘柄リスト」として出すページではない。
バックテストで優位性が確認できなかったという事実を先に示し、
検知結果はその観測記録として並べる。

シグナル配信ではなく、検証の記録として公開する。
"""
from __future__ import annotations
import os
import re
import glob
import json
import html
import datetime as dt

import pandas as pd
import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(HERE, "results")
VERIFY = os.path.join(HERE, "verification")
DOCS = os.path.join(HERE, "docs")

DETAIL_DAYS = 60          # 明細を載せる直近営業日数
COST = 0.20               # 往復コストの想定（%）


# ------------------------------------------------------------ データ収集
def load_daily() -> pd.DataFrame:
    files = sorted(glob.glob(os.path.join(RESULTS, "pa_*.csv")))
    frames = []
    for f in files:
        m = re.search(r"pa_(\d{4}-\d{2}-\d{2})", os.path.basename(f))
        if not m:
            continue
        try:
            d = pd.read_csv(f, dtype={"code": str})
        except Exception:
            continue
        if d.empty:
            continue
        d["date"] = m.group(1)
        frames.append(d)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["code4"] = df["code"].astype(str).str.slice(0, 4)
    return df


def load_verification() -> dict:
    """バックテストの要約。verification/ に置いた csv から読む。"""
    out = {"nextday": [], "horizon": [], "available": False}
    p = os.path.join(VERIFY, "nextday.csv")
    if os.path.exists(p):
        d = pd.read_csv(p)
        out["nextday"] = d.to_dict("records")
        out["available"] = True
    p2 = glob.glob(os.path.join(VERIFY, "backtest_stats_*.csv"))
    if p2:
        d = pd.read_csv(sorted(p2)[-1])
        out["horizon"] = d.to_dict("records")
    return out


# ------------------------------------------------------------ HTML
CSS = """
:root{
  --paper:#EEF0F2; --card:#FFFFFF; --ink:#16191D; --ink-2:#4A5158;
  --line:#D5DAE0; --blue:#2B4C7E; --blue-2:#5C7CA8;
  --neg:#B3261E; --pos:#1F6F5C; --band:#E4E7EB;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{
  margin:0;background:var(--paper);color:var(--ink);
  font-family:"IBM Plex Sans JP","Hiragino Sans","Yu Gothic",sans-serif;
  font-size:15px;line-height:1.75;letter-spacing:.01em;
}
.mono{font-family:"IBM Plex Mono","SFMono-Regular",monospace;font-variant-numeric:tabular-nums}
.wrap{max-width:960px;margin:0 auto;padding:0 20px}
a{color:var(--blue)}

/* ---- ヘッダ ---- */
header{border-bottom:1px solid var(--line);background:var(--card)}
.eyebrow{
  font-family:"IBM Plex Mono",monospace;font-size:11px;letter-spacing:.22em;
  text-transform:uppercase;color:var(--blue-2);padding-top:34px
}
h1{font-size:clamp(26px,4.4vw,40px);line-height:1.3;margin:10px 0 6px;font-weight:600}
.sub{color:var(--ink-2);font-size:15px;max-width:56ch;margin-bottom:30px}

/* ---- 署名要素：コストの壁 ---- */
.wall{background:var(--card);border:1px solid var(--line);border-radius:3px;
      padding:26px 24px 18px;margin:0 0 34px}
.wall h2{font-size:15px;margin:0 0 4px;font-weight:600}
.wall .note{font-size:13px;color:var(--ink-2);margin:0 0 22px}
.axis{position:relative;height:112px;margin:0 6px}
.band{position:absolute;top:0;bottom:26px;background:var(--band);
      border-left:1px dashed var(--line);border-right:1px dashed var(--line)}
.zero{position:absolute;top:0;bottom:26px;width:1px;background:var(--blue-2)}
.dot{position:absolute;width:9px;height:9px;border-radius:50%;
     background:var(--blue);transform:translate(-50%,-50%);opacity:.8}
.dot.neg{background:var(--neg)}
.ticks{position:absolute;left:0;right:0;bottom:0;height:24px;
       font-family:"IBM Plex Mono",monospace;font-size:11px;color:var(--ink-2)}
.tick{position:absolute;transform:translateX(-50%)}
.wall .verdict{margin-top:14px;padding-top:16px;border-top:1px solid var(--line);
               font-size:14px}
.verdict b{color:var(--neg)}

/* ---- セクション ---- */
section{margin:0 0 34px}
h2.sec{font-size:13px;letter-spacing:.14em;text-transform:uppercase;
       font-family:"IBM Plex Mono",monospace;color:var(--blue-2);
       border-bottom:1px solid var(--line);padding-bottom:8px;margin:0 0 16px;font-weight:500}
.card{background:var(--card);border:1px solid var(--line);border-radius:3px;padding:20px 22px}

table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;font-weight:500;color:var(--ink-2);font-size:11.5px;
   letter-spacing:.06em;border-bottom:1px solid var(--line);padding:7px 8px}
td{padding:7px 8px;border-bottom:1px solid #EDEFF2}
tbody tr:last-child td{border-bottom:none}
td.num,th.num{text-align:right;font-family:"IBM Plex Mono",monospace;font-variant-numeric:tabular-nums}
.neg{color:var(--neg)}.pos{color:var(--pos)}
td.pat{font-size:12px;color:var(--ink-2);white-space:nowrap}
.pill{display:inline-block;font-family:"IBM Plex Mono",monospace;font-size:10.5px;
      padding:1px 6px;border:1px solid var(--line);border-radius:2px;color:var(--ink-2)}

.spark{display:flex;align-items:flex-end;gap:2px;height:46px;margin:4px 0 14px}
.spark i{flex:1;background:var(--blue-2);opacity:.55;min-height:1px;border-radius:1px 1px 0 0}

details{border-top:1px solid var(--line)}
details summary{cursor:pointer;padding:11px 2px;font-size:13.5px;
                display:flex;justify-content:space-between;align-items:center}
details summary::-webkit-details-marker{display:none}
details summary .c{font-family:"IBM Plex Mono",monospace;font-size:12px;color:var(--ink-2)}
details[open] summary{color:var(--blue)}
details .body{padding-bottom:14px}

footer{border-top:1px solid var(--line);background:var(--card);
       padding:26px 0 40px;font-size:12.5px;color:var(--ink-2);margin-top:20px}
.disc{background:#FFF6F5;border:1px solid #F0D4D1;border-radius:3px;
      padding:14px 16px;font-size:13px;color:#6B2F2A;margin:0 0 30px}
@media(max-width:600px){
  .hide-sm{display:none}
  body{font-size:14px}
  td,th{padding:6px 5px;font-size:12px}
}
@media(prefers-reduced-motion:no-preference){
  .dot{transition:opacity .2s}
}
"""


def wall_svg(nextday: list) -> str:
    """署名要素：全型の日中超過リターンを1本の軸に置き、コスト帯を重ねる。"""
    if not nextday:
        return ""
    vals = [(r["pattern_id"], r["oc_excess"]) for r in nextday
            if np.isfinite(r.get("oc_excess", np.nan))]
    if not vals:
        return ""
    lo, hi = -0.35, 0.35
    def x(v):
        return (v - lo) / (hi - lo) * 100

    dots = []
    for i, (pid, v) in enumerate(sorted(vals, key=lambda z: z[1])):
        left = max(1.0, min(99.0, x(v)))
        top = 16 + (i % 8) * 9
        cls = "dot neg" if v < 0 else "dot"
        dots.append(
            f'<span class="{cls}" style="left:{left:.2f}%;top:{top}px" '
            f'title="型{pid}: {v:+.2f}%"></span>')

    ticks = "".join(
        f'<span class="tick" style="left:{x(t):.2f}%">{t:+.1f}</span>'
        for t in (-0.3, -0.2, -0.1, 0.0, 0.1, 0.2, 0.3))

    n_over = sum(1 for _, v in vals if v > COST)
    return f"""
<div class="wall">
  <h2>コストの壁</h2>
  <p class="note">16の型それぞれについて、翌日の寄りで買い引けで売った場合の
     超過リターン（同日の全銘柄中央値を控除）。灰色の帯は往復の売買コスト
     ±{COST:.2f}% を示す。帯の外に出なければ、実行しても手数料に消える。</p>
  <div class="axis">
    <div class="band" style="left:{x(-COST):.2f}%;width:{x(COST)-x(-COST):.2f}%"></div>
    <div class="zero" style="left:{x(0):.2f}%"></div>
    {''.join(dots)}
    <div class="ticks">{ticks}</div>
  </div>
  <p class="verdict">16型のうち、コスト帯を上抜けたものは
     <b>{n_over}個</b>。最良でも {max(v for _, v in vals):+.2f}%。</p>
</div>"""


def short_pattern(name: str) -> str:
    """型名の括弧書きを落として1行に収める。全文は title 属性で見せる。"""
    return re.sub(r"（.*?）", "", name).strip()


def tag(v, digits=2, suffix="%"):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return '<td class="num">—</td>'
    cls = "neg" if v < 0 else ("pos" if v > 0 else "")
    return f'<td class="num {cls}">{v:+.{digits}f}{suffix}</td>'


def verification_section(ver: dict) -> str:
    if not ver["available"]:
        return ""
    rows = sorted(ver["nextday"], key=lambda r: r["rank"])
    tr = []
    for r in rows:
        star = ' <span class="pill">有意</span>' if r.get("sig_BH") else ""
        tr.append(
            f'<tr><td class="num">{r["rank"]}</td>'
            f'<td>{html.escape(str(r["name"]))}{star}</td>'
            f'<td class="num">{int(r["n"]):,}</td>'
            f'{tag(r.get("gap_excess"))}'
            f'{tag(r.get("oc_excess"))}'
            f'<td class="num hide-sm">{r["oc_win"]:.1f}%</td>'
            f'<td class="num hide-sm">{r["p"]:.3f}</td></tr>')
    return f"""
<section>
  <h2 class="sec">検証 — 翌日 寄り→引け</h2>
  <div class="card">
    <p style="margin-top:0;font-size:13.5px;color:var(--ink-2)">
      当日の大引けで型が成立した銘柄を、翌日の寄り成行で買い、同じ日の引け成行で売る。
      窓は寄りで買う以上どうやっても取れないため、日中と分けて示す。
      いずれも同じ日の全銘柄中央値を控除している。</p>
    <table>
      <thead><tr>
        <th class="num">順</th><th>型</th><th class="num">件数</th>
        <th class="num">窓</th><th class="num">日中</th>
        <th class="num hide-sm">日中勝率</th><th class="num hide-sm">p</th>
      </tr></thead>
      <tbody>{''.join(tr)}</tbody>
    </table>
  </div>
</section>"""


def daily_section(df: pd.DataFrame) -> str:
    if df.empty:
        return """<section><h2 class="sec">検知結果</h2>
        <div class="card"><p>まだ結果がありません。</p></div></section>"""

    days = sorted(df["date"].unique(), reverse=True)
    counts = df.groupby("date").size().sort_index()
    mx = max(counts.max(), 1)
    # 日数が少ないうちは棒が巨大な帯になって意味を成さないので出さない
    spark = ""
    if len(counts) >= 5:
        spark = '<div class="spark">' + "".join(
            f'<i style="height:{max(1, int(v / mx * 46))}px" title="{d}: {v}件"></i>'
            for d, v in counts.items()) + '</div>'

    blocks = []
    for i, day in enumerate(days[:DETAIL_DAYS]):
        d = df[df["date"] == day].sort_values(["rank", "margin"],
                                              ascending=[True, False])
        tr = []
        for _, x in d.iterrows():
            per = f'{x["per"]:.1f}' if pd.notna(x.get("per")) else "—"
            pbr = f'{x["pbr"]:.2f}' if pd.notna(x.get("pbr")) else "—"
            yld = f'{x["yield_pct"]:.2f}%' if pd.notna(x.get("yield_pct")) else "—"
            tr.append(
                f'<tr><td class="mono">{html.escape(str(x["code4"]))}</td>'
                f'<td>{html.escape(str(x["name"]))}</td>'
                f'<td class="hide-sm pat" title="{html.escape(str(x["pattern"]))}">'
                f'{html.escape(short_pattern(str(x["pattern"])))}</td>'
                f'<td class="num">{x["price"]:,.0f}</td>'
                f'<td class="num">{x["margin"]:.2f}</td>'
                f'<td class="num hide-sm">{per}</td>'
                f'<td class="num hide-sm">{pbr}</td>'
                f'<td class="num hide-sm">{yld}</td></tr>')
        blocks.append(f"""
<details {"open" if i == 0 else ""}>
  <summary><span>{day}</span><span class="c">{len(d)}件</span></summary>
  <div class="body"><table>
    <thead><tr><th>コード</th><th>銘柄</th><th class="hide-sm">型</th>
      <th class="num">終値</th><th class="num">余裕</th>
      <th class="num hide-sm">PER</th><th class="num hide-sm">PBR</th>
      <th class="num hide-sm">利回り</th></tr></thead>
    <tbody>{''.join(tr)}</tbody>
  </table></div>
</details>""")

    return f"""
<section>
  <h2 class="sec">検知の記録 — {len(days)}営業日・のべ{len(df):,}件</h2>
  <div class="card">
    <p style="margin-top:0;font-size:13.5px;color:var(--ink-2)">
      毎営業日の大引け後に東証プライムを走査した結果。上の検証のとおり、
      これらの銘柄が上がりやすいという根拠は得られていない。
      観測の記録として残している。「余裕」は基準線からの距離をATRで割った値。</p>
    {spark}
    {''.join(blocks)}
  </div>
</section>"""


def build() -> str:
    df = load_daily()
    ver = load_verification()
    today = dt.date.today().isoformat()
    n_days = df["date"].nunique() if not df.empty else 0

    return f"""<!DOCTYPE html>
<html lang="ja"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>日本株プライスアクション検証</title>
<meta name="description" content="日本株のプライスアクション18型を定義し、10万セルで検証した記録。優位性は確認できなかった。">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans+JP:wght@400;500;600&display=swap" rel="stylesheet">
<style>{CSS}</style>
</head><body>

<header><div class="wrap">
  <div class="eyebrow">Price Action / Verification Log</div>
  <h1>弱いプライスアクションを<br>否定する動きに、優位性はあるか</h1>
  <p class="sub">日本株の値動きから18の型を定義し、東証プライムを毎営業日走査している。
     同時に過去データ101,918セルで検証した。結論から言えば、
     売買コストを上回る優位性は確認できなかった。その記録を公開する。</p>
</div></header>

<div class="wrap" style="padding-top:34px">

  <div class="disc">
    <b>このページは投資助言ではありません。</b>
    掲載しているのは機械的な走査の結果であって、推奨銘柄ではありません。
    検証の結果、これらの型に売買コストを上回る優位性は確認できませんでした。
    投資判断はご自身の責任で行ってください。
  </div>

  {wall_svg(ver["nextday"])}
  {verification_section(ver)}
  {daily_section(df)}

  <section>
    <h2 class="sec">検証の方法</h2>
    <div class="card" style="font-size:13.5px">
      <p style="margin-top:0"><b>対象</b>　東証プライム。売買代金の20日中央値が5,000万円以上の銘柄。</p>
      <p><b>判定</b>　各営業日の確定足で18型を判定する。指標はすべてその日までの
         データだけで計算し、スイング点は確定までの遅延を考慮している。
         同じ型が連続して報告されないよう、否定が成立した初日のみを記録する。</p>
      <p><b>超過リターン</b>　同じ日の全銘柄の中央値を引く。市場全体が上げた日に
         上がったからといって、その銘柄が強かったことにはならない。</p>
      <p><b>多重比較</b>　18型×複数期間で検定を繰り返せば、何も効いていなくても
         いくつかは「有意」になる。Benjamini-Hochberg 法で偽発見率を10%に制御している。</p>
      <p style="margin-bottom:0"><b>限界</b>　2023年5月〜2026年5月の単一の相場環境における、
         492銘柄での結果。日本株が上昇基調だった期間であり、
         下落局面では異なる可能性がある。</p>
    </div>
  </section>

</div>

<footer><div class="wrap">
  最終更新 {today}　/　検知 {n_days}営業日ぶん<br>
  データ提供：J-Quants API　/　自動更新：GitHub Actions
</div></footer>
</body></html>"""


def main():
    os.makedirs(DOCS, exist_ok=True)
    out = os.path.join(DOCS, "index.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(build())
    # Jekyll の処理を無効化（_ 始まりのファイルが無視されるのを防ぐ）
    open(os.path.join(DOCS, ".nojekyll"), "w").close()
    size = os.path.getsize(out)
    print(f"生成: {out}（{size:,} bytes）")


if __name__ == "__main__":
    main()
