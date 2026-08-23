# -*- coding: utf-8 -*-
"""HTMLメールの組み立てと送信。"""
from __future__ import annotations
import smtplib
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formatdate

from .valuation import fmt
from .store import to_kabu_code

CONF_COLOR = {"A": "#0f6e61", "B": "#b56a1c", "C": "#8a8a8a"}


def _header_note(snapshot_time: str, intraday: bool) -> str:
    if intraday:
        return (f"スナップショット時刻 {snapshot_time}（<b>未確定足</b>）"
                f"／確定足は前営業日まで")
    return f"判定基準 {snapshot_time}（<b>確定足</b>）"

CSS = """
body{font-family:'Hiragino Sans','Yu Gothic',sans-serif;color:#23262b;font-size:14px;line-height:1.6}
h2{font-size:17px;color:#14243c;border-bottom:2px solid #1f3a5f;padding-bottom:5px;margin:22px 0 10px}
h3{font-size:14px;color:#1f3a5f;border-left:4px solid #c8a45c;padding-left:8px;margin:18px 0 6px}
table{border-collapse:collapse;width:100%;font-size:12.5px;margin-bottom:14px}
th{background:#1f3a5f;color:#fff;padding:6px 7px;text-align:left;font-weight:500}
td{border-bottom:1px solid #e2e6ea;padding:6px 7px;vertical-align:top}
tr:nth-child(even) td{background:#f7f9fb}
.code{font-weight:700;color:#14243c}
.conf{font-weight:700;padding:1px 6px;border-radius:3px;color:#fff;font-size:11px}
.note{color:#5a6470;font-size:11.5px}
.lead{background:#f6f8fa;border-top:2px solid #1f3a5f;padding:10px 12px;margin-bottom:16px;font-size:13px}
.warn{background:#fbf8f0;border:1px dashed #d8c79a;padding:9px 12px;font-size:12px;color:#6b5a3e;margin-top:20px}
.num{text-align:right;font-variant-numeric:tabular-nums}
"""


def build_html(rows, as_of: str, snapshot_time: str, stats: dict,
               intraday: bool = True) -> str:
    """rows: list of dict(code,name,pattern_id,pattern,rank,group,conf,margin,
                          price,per,pbr,yield_pct,note,baseline)"""
    by_pat = {}
    for r in rows:
        by_pat.setdefault((r["rank"], r["pattern_id"], r["pattern"]), []).append(r)

    parts = [f"<html><head><meta charset='utf-8'><style>{CSS}</style></head><body>"]
    parts.append(f"""<div class='lead'>
      <b>プライスアクション検知 — {as_of}</b><br>
      {_header_note(snapshot_time, intraday)}<br>
      対象 {stats.get('universe',0)} 銘柄・流動性通過 {stats.get('passed',0)} 銘柄
      ・ヒット {len(rows)} 件（確度A/Bのみ）
    </div>""")

    if not rows:
        parts.append("<p>本日の該当銘柄はありません。</p>")
    else:
        for (rank, pid, pname), items in sorted(by_pat.items()):
            items.sort(key=lambda x: (x["conf"], -x["margin"]))
            parts.append(f"<h3>否定度{rank}位 — 型{pid} {pname}"
                         f"（{len(items)}件）</h3>")
            px_label = "現在値" if intraday else "終値"
            parts.append("<table><tr><th>確度</th><th>コード</th><th>銘柄名</th>"
                         f"<th class='num'>{px_label}</th><th class='num'>余裕</th>"
                         "<th class='num'>PER</th><th class='num'>PBR</th>"
                         "<th class='num'>利回り</th><th>内容</th></tr>")
            for r in items:
                col = CONF_COLOR.get(r["conf"], "#888")
                parts.append(
                    f"<tr><td><span class='conf' style='background:{col}'>{r['conf']}</span></td>"
                    f"<td class='code'>{to_kabu_code(r['code'])}</td><td>{r['name']}</td>"
                    f"<td class='num'>{r['price']:,.0f}</td>"
                    f"<td class='num'>{r['margin']:.2f}</td>"
                    f"<td class='num'>{fmt(r['per'])}</td>"
                    f"<td class='num'>{fmt(r['pbr'], 2)}</td>"
                    f"<td class='num'>{fmt(r['yield_pct'], 2, '%')}</td>"
                    f"<td class='note'>{r['note']}</td></tr>")
            parts.append("</table>")

    if intraday:
        parts.append("""<div class='warn'>
      <b>この一覧の性質。</b>
      これは大引け時点の確定判定ではなく、スナップショット時刻での候補である。
      後場の値動きで型から外れる銘柄が必ず出る。<br>
      「余裕」は (現在値 − 基準線) ÷ ATR。この値が大きいほど引けまで剥落しにくい。
      確度Aは余裕1.5ATR以上、Bは0.5ATR以上。<br>
      PERはTTM（直近12か月）EPSベース。四半期の累計値をそのまま使うと
      期の途中では過大に出るため、前期通期＋当期累計−前年同期累計で算出している。
      一過性の損失があった銘柄はPERが跳ねるので、数値の異常は決算内容を確認すること。
      EPSが負の場合PERは表示しない。PBRは直近本決算のBPSベース。
    </div>""")
    else:
        parts.append("""<div class='warn'>
      <b>この一覧の性質。</b>
      大引けの確定足による判定であり、後から型が外れることはない。
      エントリーは翌営業日以降になるため、寄り付きのギャップは別途の リスクとして残る。<br>
      「余裕」は (終値 − 基準線) ÷ ATR。基準線をどれだけ明確に奪回したかの強さを表す。
      値が大きいほど 明確な奪回だが、その分エントリー価格は基準線から離れる。<br>
      PERはTTM（直近12か月）EPS、または会社予想の通期EPSがあればそちらを優先。
      EPSが負の場合PERは表示しない。PBRは直近本決算のBPSベース。
    </div>""")
    parts.append("</body></html>")
    return "".join(parts)


def send(html: str, subject: str, cfg):
    if not (cfg.smtp_user and cfg.smtp_password and cfg.mail_to):
        raise RuntimeError("SMTP設定が未入力です（PA_SMTP_USER / PA_SMTP_PASSWORD / PA_MAIL_TO）")
    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = cfg.mail_from or cfg.smtp_user
    msg["To"] = cfg.mail_to
    msg["Date"] = formatdate(localtime=True)
    with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30) as s:
        s.starttls()
        s.login(cfg.smtp_user, cfg.smtp_password)
        s.sendmail(msg["From"], [x.strip() for x in cfg.mail_to.split(",")],
                   msg.as_string())
