"""小工具：时间解析、格式化、原子写入。"""
from __future__ import annotations

import datetime as dt
import os
import re
import sys
import tempfile
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - Python < 3.9
    ZoneInfo = None  # type: ignore

UTC = dt.timezone.utc


def log(msg: str = "", *args) -> None:
    """日志走 stderr，避免污染 --json 的 stdout。"""
    text = msg % args if args else msg
    print("[qq-digest] " + text, file=sys.stderr, flush=True)


def get_tz(name: str = "Asia/Shanghai"):
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return dt.timezone(dt.timedelta(hours=8))


def now_utc() -> dt.datetime:
    return dt.datetime.now(UTC)


_UNITS = {
    "m": 60,
    "min": 60,
    "分钟": 60,
    "h": 3600,
    "hr": 3600,
    "小时": 3600,
    "d": 86400,
    "天": 86400,
    "w": 604800,
    "周": 604800,
}

_REL_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(m|min|分钟|h|hr|小时|d|天|w|周)$", re.I)


def parse_since(value: str, tz_name: str = "Asia/Shanghai", now: dt.datetime | None = None):
    """把 24h / 3d / today / 2026-11-10 解析成 UTC 时间点。"""
    now = now or now_utc()
    tz = get_tz(tz_name)
    v = (value or "").strip()
    if not v:
        v = "24h"
    low = v.lower()

    local_now = now.astimezone(tz)
    if low in ("today", "今天"):
        start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start.astimezone(UTC)
    if low in ("yesterday", "昨天"):
        start = local_now.replace(hour=0, minute=0, second=0, microsecond=0) - dt.timedelta(days=1)
        return start.astimezone(UTC)
    if low in ("week", "本周", "this_week"):
        start = local_now.replace(hour=0, minute=0, second=0, microsecond=0) - dt.timedelta(days=local_now.weekday())
        return start.astimezone(UTC)

    m = _REL_RE.match(v)
    if m:
        amount = float(m.group(1))
        unit = m.group(2).lower()
        return now - dt.timedelta(seconds=amount * _UNITS[unit])

    try:
        parsed = dt.datetime.fromisoformat(v)
    except ValueError:
        raise ValueError("看不懂的时间：%r（支持 24h / 3d / today / 2026-11-10 / 2026-11-10T20:00）" % value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(UTC)


def fmt_local(ts: int | float | dt.datetime, tz_name: str = "Asia/Shanghai", fmt: str = "%m-%d %H:%M") -> str:
    if isinstance(ts, dt.datetime):
        d = ts
        if d.tzinfo is None:
            d = d.replace(tzinfo=UTC)
    else:
        d = dt.datetime.fromtimestamp(float(ts), UTC)
    return d.astimezone(get_tz(tz_name)).strftime(fmt)


def fmt_hm(ts: int | float, tz_name: str = "Asia/Shanghai") -> str:
    return fmt_local(ts, tz_name, "%H:%M")


def local_day(ts: int | float, tz_name: str = "Asia/Shanghai") -> str:
    return fmt_local(ts, tz_name, "%Y-%m-%d")


def human_span(start: dt.datetime, end: dt.datetime, tz_name: str = "Asia/Shanghai") -> str:
    s = start.astimezone(get_tz(tz_name))
    e = end.astimezone(get_tz(tz_name))
    if s.date() == e.date():
        return "%s %s–%s" % (s.strftime("%m 月 %d 日"), s.strftime("%H:%M"), e.strftime("%H:%M"))
    return "%s → %s" % (s.strftime("%m-%d %H:%M"), e.strftime("%m-%d %H:%M"))


def truncate(text: str, limit: int, suffix: str = "…") -> str:
    if text is None:
        return ""
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(suffix))].rstrip() + suffix


def atomic_write(path: str | Path, text: str, mode: int | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return path


def read_json(path: str | Path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    with p.open("r", encoding="utf-8") as f:
        return json_load(f)


def json_load(f):
    import json

    return json.load(f)


def deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def safe_filename(text: str, fallback: str = "report") -> str:
    text = re.sub(r"[^\w\-.]+", "-", (text or "").strip(), flags=re.UNICODE).strip("-")
    return text or fallback
