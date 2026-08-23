# -*- coding: utf-8 -*-
"""kabuステーションAPI から 14:00 時点の未完成足スナップショットを取る。

kabuステーションが起動しログイン済みであることが前提。
REST /board を銘柄数ぶん叩く。1,574銘柄・8req/秒で約3分半なので、
13:50 起動 → 13:54 完了 → 14:00 送信、という運用になる。

レート制限はドキュメントで明示されていない部分があるため、
KABUS_RATE_LIMIT を保守的に設定してある。429 が出るようなら下げること。
"""
from __future__ import annotations
import time
import threading
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

from .store import to_kabu_code


class RateLimiter:
    def __init__(self, per_sec: float):
        self.interval = 1.0 / max(per_sec, 0.1)
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self):
        with self.lock:
            now = time.monotonic()
            if now < self.next_at:
                time.sleep(self.next_at - now)
                now = time.monotonic()
            self.next_at = now + self.interval


class KabuClient:
    def __init__(self, cfg):
        self.cfg = cfg
        self.base = cfg.kabus_base.rstrip("/")
        self.token = None
        self.limiter = RateLimiter(cfg.kabus_rate_limit)
        self.session = requests.Session()

    def authenticate(self):
        r = self.session.post(f"{self.base}/token",
                              json={"APIPassword": self.cfg.kabus_password},
                              timeout=self.cfg.kabus_timeout)
        r.raise_for_status()
        self.token = r.json()["Token"]
        return self.token

    def _headers(self):
        return {"X-API-KEY": self.token}

    def board(self, code: str):
        """1銘柄の板情報。当日の O/H/L/現在値/出来高を返す。"""
        self.limiter.wait()
        url = f"{self.base}/board/{to_kabu_code(code)}@{self.cfg.kabus_exchange}"
        r = self.session.get(url, headers=self._headers(),
                             timeout=self.cfg.kabus_timeout)
        if r.status_code != 200:
            return None
        d = r.json()
        try:
            o = d.get("OpeningPrice")
            h = d.get("HighPrice")
            l = d.get("LowPrice")
            c = d.get("CurrentPrice")
            v = d.get("TradingVolume")
            if None in (o, h, l, c, v):
                return None
            return {"code": str(code), "o": float(o), "h": float(h),
                    "l": float(l), "c": float(c), "v": float(v)}
        except (TypeError, ValueError):
            return None

    def snapshot(self, codes, progress_every: int = 200) -> dict:
        """{code: {o,h,l,c,v}}。取得できなかった銘柄は含まれない。"""
        out, done = {}, 0
        with ThreadPoolExecutor(max_workers=self.cfg.kabus_workers) as ex:
            futs = {ex.submit(self.board, c): c for c in codes}
            for f in as_completed(futs):
                done += 1
                if done % progress_every == 0:
                    print(f"  スナップショット取得 {done}/{len(codes)}")
                try:
                    r = f.result()
                except Exception:
                    r = None
                if r:
                    out[r["code"]] = r
        return out
