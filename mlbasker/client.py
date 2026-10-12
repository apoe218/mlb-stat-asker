"""Thin MLB Stats API client with an on-disk TTL cache."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import httpx

BASE = "https://statsapi.mlb.com/api/v1"
BASE_11 = "https://statsapi.mlb.com/api/v1.1"

CACHE_DIR = Path(os.environ.get("MLBASKER_CACHE", "data/cache"))

TTL_SHORT = 15 * 60          # schedules / standings / stats for the current season
TTL_LONG = 24 * 60 * 60      # rosters, teams, seasons
TTL_FOREVER = 10 * 365 * 24 * 3600  # finished games never change


class ApiError(RuntimeError):
    pass


class MlbClient:
    def __init__(self, cache_dir: Path | None = None, timeout: float = 30.0):
        self.cache_dir = Path(cache_dir) if cache_dir else CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._http = httpx.Client(timeout=timeout, headers={"User-Agent": "mlb-stat-asker/0.1"})

    def _path(self, url: str, params: dict[str, Any] | None) -> Path:
        key = url + "?" + json.dumps(params or {}, sort_keys=True, default=str)
        return self.cache_dir / (hashlib.sha1(key.encode()).hexdigest() + ".json")

    def get(self, path: str, params: dict[str, Any] | None = None, *, ttl: int = TTL_SHORT,
            base: str = BASE, cache_result: bool = True) -> dict:
        url = f"{base}/{path.lstrip('/')}"
        cache = self._path(url, params)
        if not cache_result:
            return self._fetch(url, params)
        if cache.exists() and time.time() - cache.stat().st_mtime < ttl:
            return json.loads(cache.read_text())
        try:
            data = self._fetch(url, params)
        except ApiError:
            if cache.exists():  # stale beats nothing
                return json.loads(cache.read_text())
            raise
        tmp = cache.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(cache)
        return data

    def _fetch(self, url: str, params: dict[str, Any] | None) -> dict:
        last: Exception | None = None
        for attempt in range(3):
            try:
                r = self._http.get(url, params=params)
                r.raise_for_status()
                return r.json()
            except httpx.HTTPError as e:
                last = e
                time.sleep(0.5 * (attempt + 1))
        raise ApiError(f"MLB API request failed: {last}") from last

    def close(self) -> None:
        self._http.close()
