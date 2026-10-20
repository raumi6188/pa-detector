# -*- coding: utf-8 -*-
"""毎日の記事原稿を生成する。

    python -m pa_detector.article

出力
----
  note/note_YYYY-MM-DD.md   note に貼るための原稿（フロントマターなし）
  articles/pa-YYYY-MM-DD.md Zenn 用（フロントマター付き。push すると公開される）

note について
-------------
  note には公式の投稿APIが無く、今後の公開予定も未定とされている。
  非公式エンドポイントを使えば投稿自体は可能だが、規約リスク・アカウントリスク・
  仕様変更で壊れるリスクがあるため、このツールでは原稿の生成までに留める。
  貼り付けは手作業で行うこと。

Zenn について
-------------
  GitHub リポジトリ連携は公式機能で、articles/ 配下を push すると公開される。
  published: false にしておけば下書き扱いのまま溜まるので、
  最初はそれで様子を見るのが安全。

記事の立場
----------
  検知結果を「有望銘柄」として出す記事にはしない。
  優位性が確認できなかった事実を毎回明記したうえで、観測記録として出す。
"""
from __future__ import annotations
import os
import re
import glob
import datetime as dt

import pandas as pd
import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(HERE, "results")
VERIFY = os.path.join(HERE, "verification")
NOTE_DIR = os.path.join(HERE, "note")
ZENN_DIR = os.path.join(HERE, "articles")

COST = 0.20
TOP_N = 20               # 記事に載せる上位件数
PAGE_URL = os.environ.get("PA_PAGE_URL", "")   # 公開ページのURL（あれば末尾に載せる）
ZENN_PUBLISHED = os.environ.get("PA_ZENN_PUBLISHED", "false").lower() == "true"


def latest_result():
    files = sorted(glob.glob(os.path.join(RESULTS, "pa_*.csv")))
    if not files:
        return None, None
    f = files[-1]
    m = re.search(r"pa_(\d{4}-\d{2}-\d{2})", os.path.basename(f))
    d = pd.read_csv(f, dtype={"code": str})
    if d.empty:
        return None, None
    d["code4"] = d["code"].astype(str).str.slice(0, 4)
    return m.group(1), d


def verification_summary():
    p = os.path.join(VERIFY, "nextday.csv")
    if not os.path.exists(p):
        return None
    v = pd.read_csv(p)
    vals = v["oc_excess"].dropna()
    return {
        "n_type": len(v),
        "best": float(vals.max()),
        "median": float(vals.median()),
        "n_over": int((vals > COST).sum()),
        "rows": v.sort_values("rank").to_dict("records"),
    }


def body(date: str, d: pd.DataFrame, ver) -> str:
    """記事の本文。note でも Zenn でも同じものを使う。"""
    n_days = len(glob.glob(os.path.join(RESULTS, "pa_*.csv")))
    top = d.sort_values(["rank", "margin"], ascending=[True, False]).head(TOP_N)

    lines = []
    lines.append(f"東証プライムを大引け後に機械的に走査した記録です。**推奨銘柄ではありません。**\n")

    if ver:
        lines.append("## 先に結論")
        lines.append("")
        lines.append(
            f"この検知には**優位性が確認できていません**。"
            f"過去データ101,918セル（銘柄×営業日）で検証したところ、"
            f"{ver['n_type']}の型のうち往復の売買コスト{COST:.2f}%を上回ったものは"
            f"**{ver['n_over']}個**でした。最良の型でも{ver['best']:+.2f}%、"
            f"中央値は{ver['median']:+.2f}%です。")
        lines.append("")
        lines.append(
            "つまり、以下の一覧は「上がりやすい銘柄」ではありません。"
            "定義した型に当てはまった銘柄を並べているだけです。"
            "それでも記録を続けているのは、"
            "**当てはまった後に何が起きるかを数え続けること自体に意味がある**と考えているからです。")
        lines.append("")

    lines.append(f"## {date} の検知：{len(d)}件")
    lines.append("")
    lines.append(f"| コード | 銘柄 | 型 | 終値 | 余裕 | PER | PBR | 利回り |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for _, x in top.iterrows():
        pat = re.sub(r"（.*?）", "", str(x["pattern"])).strip()
        per = f'{x["per"]:.1f}' if pd.notna(x.get("per")) else "—"
        pbr = f'{x["pbr"]:.2f}' if pd.notna(x.get("pbr")) else "—"
        yld = f'{x["yield_pct"]:.2f}%' if pd.notna(x.get("yield_pct")) else "—"
        lines.append(
            f'| {x["code4"]} | {x["name"]} | {pat} | {x["price"]:,.0f} | '
            f'{x["margin"]:.2f} | {per} | {pbr} | {yld} |')
    if len(d) > TOP_N:
        lines.append("")
        lines.append(f"※ 全{len(d)}件のうち上位{TOP_N}件を掲載しています。")
    lines.append("")

    lines.append("### 表の読み方")
    lines.append("")
    lines.append(
        "**余裕** … 終値が基準線をどれだけ上回っているかを、"
        "その銘柄のATR（1日の平均的な値幅）で割った値。"
        "値がさ株と低位株を同じ物差しで比べるための正規化です。"
        "大きいほど明確に基準線を奪回していますが、"
        "その分エントリー価格は基準線から離れます。")
    lines.append("")
    lines.append(
        "**PER** … 会社予想の通期EPS、なければTTM（直近12か月）ベース。"
        "四半期の累計値をそのまま使うと期の途中で過大に出るため、"
        "前期通期＋当期累計−前年同期累計で算出しています。")
    lines.append("")

    if ver:
        lines.append("## 検証の詳細")
        lines.append("")
        lines.append(
            "当日の大引けで型が成立した銘柄を、翌日の寄り成行で買い、"
            "同じ日の引け成行で売った場合の超過リターンです。"
            "超過は同じ日の全銘柄の中央値を控除しています。")
        lines.append("")
        lines.append("| 型 | 件数 | 日中超過 | 勝率 | 基準 |")
        lines.append("|---|---|---|---|---|")
        for r in ver["rows"]:
            nm = re.sub(r"（.*?）", "", str(r["name"])).strip()
            lines.append(
                f'| {nm} | {int(r["n"]):,} | {r["oc_excess"]:+.2f}% | '
                f'{r["oc_win"]:.1f}% | {r["oc_base_win"]:.1f}% |')
        lines.append("")
        lines.append(
            "「窓」（当日終値から翌日寄り付きまで）にも情報はありませんでした。"
            "つまり**シグナルに情報はあるが寄り付きで織り込まれてしまう**のではなく、"
            "そもそも情報が無かった、ということになります。")
        lines.append("")

    lines.append("## この記録について")
    lines.append("")
    lines.append(
        "値動きの「弱さ」と、その弱さを打ち消す「否定」を18の型として定義し、"
        "毎営業日の大引け後に東証プライムを走査しています。"
        "判定はすべてその日までのデータだけで行い、"
        "同じ型が連続して報告されないよう、否定が成立した初日のみを記録しています。")
    lines.append("")
    lines.append(f"これまでに{n_days}営業日ぶんの記録が溜まりました。")
    if PAGE_URL:
        lines.append("")
        lines.append(f"全期間の記録と検証結果は[こちら]({PAGE_URL})にまとめています。")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "**免責**　本記事は投資助言ではありません。掲載しているのは機械的な走査の結果であって、"
        "推奨銘柄ではありません。検証の結果、これらの型に売買コストを上回る優位性は"
        "確認できませんでした。投資判断はご自身の責任で行ってください。")
    lines.append("")
    lines.append("データ提供：J-Quants API")
    return "\n".join(lines)


def main():
    date, d = latest_result()
    if d is None:
        print("結果CSVがありません")
        return
    ver = verification_summary()
    text = body(date, d, ver)

    os.makedirs(NOTE_DIR, exist_ok=True)
    np_ = os.path.join(NOTE_DIR, f"note_{date}.md")
    title = f"【検証記録】日本株プライスアクション検知 {date}（{len(d)}件）"
    with open(np_, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n{text}\n")
    print(f"note用: {np_}")

    os.makedirs(ZENN_DIR, exist_ok=True)
    zp = os.path.join(ZENN_DIR, f"pa-{date}.md")
    fm = (
        "---\n"
        f'title: "{title}"\n'
        'emoji: "📉"\n'
        "type: idea\n"
        "topics: [python, 統計, 投資, データ分析]\n"
        f"published: {'true' if ZENN_PUBLISHED else 'false'}\n"
        "---\n\n"
    )
    with open(zp, "w", encoding="utf-8") as f:
        f.write(fm + text + "\n")
    print(f"Zenn用: {zp}（published: {'true' if ZENN_PUBLISHED else 'false'}）")


if __name__ == "__main__":
    main()
