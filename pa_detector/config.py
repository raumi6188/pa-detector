# -*- coding: utf-8 -*-
"""設定。環境変数で上書きできる。"""
from __future__ import annotations
import os
from dataclasses import dataclass, field


def _env(key: str, default):
    v = os.environ.get(key)
    if v is None or v == "":
        return default
    if isinstance(default, bool):
        return v.lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        return int(v)
    if isinstance(default, float):
        return float(v)
    return v


@dataclass
class Config:
    # ---- データ ----
    # 既存の J-Quants 由来 SQLite。daily_quotes テーブルを読む
    db_path: str = _env("PA_DB_PATH", r"C:\quant\jquants.db")
    # 過去足の読み込み日数（スイング抽出に十分な長さ）
    history_days: int = _env("PA_HISTORY_DAYS", 180)

    # ---- kabuステーションAPI ----
    kabus_base: str = _env("KABUS_BASE", "http://localhost:18080/kabusapi")
    kabus_password: str = _env("KABUS_PASSWORD", "")
    kabus_exchange: int = _env("KABUS_EXCHANGE", 1)      # 1 = 東証
    kabus_rate_limit: float = _env("KABUS_RATE_LIMIT", 8.0)   # req/sec
    kabus_workers: int = _env("KABUS_WORKERS", 4)
    kabus_timeout: float = _env("KABUS_TIMEOUT", 5.0)

    # ---- 出来高の日中補正 ----
    # 14:00 時点で当日フル出来高の何割が出ているか。calibrate_volume.py で較正する
    volume_fraction_1400: float = _env("PA_VOL_FRACTION", 0.72)

    # ---- 判定パラメータ ----
    atr_period: int = 14
    swing_n: int = _env("PA_SWING_N", 3)         # fractal の前後本数
    ma_short: int = 5
    ma_long: int = 25
    vol_avg_period: int = 20

    # ---- 確度しきい値（ATR倍）----
    conf_a_margin: float = _env("PA_CONF_A", 1.5)
    conf_b_margin: float = _env("PA_CONF_B", 0.5)
    report_confidences: tuple = ("A", "B")

    # ---- 流動性フィルタ ----
    min_turnover_median: float = _env("PA_MIN_TURNOVER", 50_000_000)  # 20日中央値・円

    # ---- 除外する型 ----
    # 型4（引け際の売り叩き）は 14:00 時点では原理的に判定不能
    excluded_patterns: tuple = tuple(
        int(x) for x in _env("PA_EXCLUDE", "4").split(",") if x.strip()
    )

    # ---- メール ----
    smtp_host: str = _env("PA_SMTP_HOST", "smtp.gmail.com")
    smtp_port: int = _env("PA_SMTP_PORT", 587)
    smtp_user: str = _env("PA_SMTP_USER", "")
    smtp_password: str = _env("PA_SMTP_PASSWORD", "")
    mail_from: str = _env("PA_MAIL_FROM", "")
    mail_to: str = _env("PA_MAIL_TO", "")

    # ---- 出力 ----
    out_dir: str = _env("PA_OUT_DIR", r"C:\quant\pa_out")
    save_csv: bool = _env("PA_SAVE_CSV", True)


CFG = Config()
