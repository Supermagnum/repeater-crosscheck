from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import requests


class RateLimitedSession:
    """requests.Session with per-host minimum interval and simple retries."""

    def __init__(
        self,
        user_agent: str,
        min_interval_s: float = 1.5,
        timeout_s: float = 120,
        retries: int = 3,
    ) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        self.min_interval_s = min_interval_s
        self.timeout_s = timeout_s
        self.retries = retries
        self._last_request: dict[str, float] = {}

    def set_user_agent(self, user_agent: str) -> None:
        self.session.headers["User-Agent"] = user_agent

    def _wait(self, host: str, interval: float | None = None) -> None:
        gap = self.min_interval_s if interval is None else interval
        last = self._last_request.get(host, 0.0)
        delay = gap - (time.monotonic() - last)
        if delay > 0:
            time.sleep(delay)

    def request(
        self,
        method: str,
        url: str,
        *,
        min_interval_s: float | None = None,
        **kwargs: Any,
    ) -> requests.Response:
        from urllib.parse import urlparse

        host = urlparse(url).netloc
        last_exc: Exception | None = None
        for attempt in range(1, self.retries + 1):
            self._wait(host, min_interval_s)
            try:
                kwargs.setdefault("timeout", self.timeout_s)
                resp = self.session.request(method, url, **kwargs)
                self._last_request[host] = time.monotonic()
                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", "10"))
                    time.sleep(retry_after)
                    continue
                if resp.status_code >= 500:
                    time.sleep(min(2 ** attempt, 30))
                    continue
                return resp
            except requests.RequestException as exc:
                last_exc = exc
                time.sleep(min(2 ** attempt, 30))
        if last_exc:
            raise last_exc
        raise RuntimeError(f"Failed to fetch {url}")


class ResponseCache:
    def __init__(self, cache_dir: Path, refresh: bool = False) -> None:
        self.cache_dir = cache_dir
        self.refresh = refresh
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str, suffix: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)[:80]
        return self.cache_dir / f"{safe}_{digest}{suffix}"

    def get_text(self, key: str) -> str | None:
        if self.refresh:
            return None
        path = self._path(key, ".txt")
        if path.is_file():
            return path.read_text(encoding="utf-8")
        return None

    def put_text(self, key: str, text: str) -> Path:
        path = self._path(key, ".txt")
        path.write_text(text, encoding="utf-8")
        meta = path.with_suffix(".meta.json")
        meta.write_text(
            json.dumps({"key": key, "saved_at": time.time()}, indent=2),
            encoding="utf-8",
        )
        return path

    def get_json(self, key: str) -> Any | None:
        if self.refresh:
            return None
        path = self._path(key, ".json")
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        return None

    def put_json(self, key: str, data: Any) -> Path:
        path = self._path(key, ".json")
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        meta = path.with_suffix(".meta.json")
        # .json -> would collide; use separate name
        meta = self.cache_dir / (path.stem + ".meta.json")
        meta.write_text(
            json.dumps({"key": key, "saved_at": time.time()}, indent=2),
            encoding="utf-8",
        )
        return path

    def get_bytes(self, key: str, *, suffix: str = ".bin") -> bytes | None:
        if self.refresh:
            return None
        path = self._path(key, suffix)
        if path.is_file():
            return path.read_bytes()
        return None

    def put_bytes(self, key: str, data: bytes, *, suffix: str = ".bin") -> Path:
        path = self._path(key, suffix)
        path.write_bytes(data)
        meta = self.cache_dir / (path.stem + ".meta.json")
        meta.write_text(
            json.dumps({"key": key, "saved_at": time.time()}, indent=2),
            encoding="utf-8",
        )
        return path
