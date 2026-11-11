"""把 OneBot 消息对象拆成「谁 / 什么时候 / 说了什么」。"""
from __future__ import annotations

import hashlib
import html
import json
import re

_CQ_RE = re.compile(r"\[CQ:([a-zA-Z0-9_]+)(?:,([^\]]*))?\]")
_AT_RE = re.compile(r"@(\S+)")
_CTRL_RE = re.compile(r"[\u200b-\u200f\u2060\ufeff]")
_MULTI_NL_RE = re.compile(r"\n{2,}")

# 纯噪音消息：单独出现时折叠
_NOISE = {"[表情]", "[图片]", "[语音]", "[视频]", "[卡片]", "[戳一戳]", "[骰子]", "[猜拳]", "[文件]"}


def unescape_cq(text: str) -> str:
    return (
        text.replace("&#91;", "[")
        .replace("&#93;", "]")
        .replace("&#44;", ",")
        .replace("&amp;", "&")
    )


def _cq_param(params: str) -> dict:
    out = {}
    for part in (params or "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = unescape_cq(v.strip())
    return out


def cq_to_text(text: str) -> str:
    def repl(match: re.Match) -> str:
        kind = match.group(1).lower()
        params = _cq_param(match.group(2) or "")
        if kind == "text":
            return unescape_cq(params.get("text", ""))
        if kind == "at":
            qq = params.get("qq", "")
            name = params.get("name", "")
            if qq == "all":
                return "@全体成员"
            return "@" + (name or qq)
        if kind in ("image", "mface"):
            return "[图片]"
        if kind == "face":
            return "[表情]"
        if kind == "record":
            return "[语音]"
        if kind == "video":
            return "[视频]"
        if kind == "file":
            return "[文件]" + (params.get("name", "") or "")
        if kind == "reply":
            return ""
        if kind in ("json", "xml"):
            return "[卡片]"
        if kind == "forward":
            return "[聊天记录]"
        if kind == "poke":
            return "[戳一戳]"
        if kind in ("dice",):
            return "[骰子]"
        if kind in ("rps",):
            return "[猜拳]"
        return "[%s]" % kind

    return _CQ_RE.sub(repl, text or "")


def _json_card(data: str) -> str:
    if not data:
        return "[卡片]"
    try:
        payload = json.loads(data)
    except (TypeError, ValueError):
        return "[卡片]"
    if isinstance(payload, dict):
        prompt = payload.get("prompt")
        if prompt:
            return "[卡片:%s]" % _clip(str(prompt), 60)
        meta = payload.get("meta") or {}
        if isinstance(meta, dict):
            for value in meta.values():
                if isinstance(value, dict):
                    title = value.get("title") or value.get("desc")
                    if title:
                        return "[卡片:%s]" % _clip(str(title), 60)
        title = payload.get("title") or payload.get("desc")
        if title:
            return "[卡片:%s]" % _clip(str(title), 60)
    return "[卡片]"


def _segment_text(seg) -> str:
    if isinstance(seg, str):
        return cq_to_text(seg)
    if not isinstance(seg, dict):
        return ""
    kind = str(seg.get("type") or "").lower()
    data = seg.get("data")
    if not isinstance(data, dict):
        data = {}
    if kind == "text":
        return cq_to_text(str(data.get("text") or ""))
    if kind == "at":
        qq = str(data.get("qq") or "")
        return "@全体成员" if qq == "all" else "@" + (str(data.get("name") or qq))
    if kind == "image":
        return "[图片]"
    if kind in ("face", "mface"):
        return "[表情]"
    if kind == "record":
        return "[语音]"
    if kind == "video":
        return "[视频]"
    if kind == "file":
        name = str(data.get("name") or data.get("file") or "")
        return "[文件]" + name
    if kind == "reply":
        return ""
    if kind == "json":
        return _json_card(data.get("data") if isinstance(data.get("data"), str) else json.dumps(data, ensure_ascii=False))
    if kind == "xml":
        return "[卡片]"
    if kind == "forward":
        return "[聊天记录]"
    if kind == "poke":
        return "[戳一戳]"
    if kind == "dice":
        return "[骰子]"
    if kind == "rps":
        return "[猜拳]"
    if kind == "markdown":
        return str(data.get("content") or data.get("text") or "")
    if kind == "node":
        return "[转发消息]"
    return "[%s]" % kind


def render_message(raw) -> str:
    segs = raw.get("message")
    if isinstance(segs, str):
        text = cq_to_text(segs)
    elif isinstance(segs, list):
        text = "".join(_segment_text(seg) for seg in segs)
    elif isinstance(segs, dict):
        text = _segment_text(segs)
    else:
        text = cq_to_text(str(raw.get("raw_message") or ""))
    text = _CTRL_RE.sub("", text or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _MULTI_NL_RE.sub("\n", text)
    text = "\n".join(line.strip() for line in text.split("\n")).strip()
    return _clip(text, 1000)


def sender_of(raw) -> tuple:
    sender = raw.get("sender") if isinstance(raw.get("sender"), dict) else {}
    anon = raw.get("anonymous") if isinstance(raw.get("anonymous"), dict) else {}
    uid = str(sender.get("user_id") or raw.get("user_id") or anon.get("id") or "")
    name = (
        (anon.get("name") if anon else "")
        or sender.get("card")
        or sender.get("nickname")
        or raw.get("sender_nickname")
        or (("QQ" + uid) if uid else "匿名")
    )
    return uid, _clip(str(name), 40)


def message_id_of(raw, fallback: str) -> str:
    for key in ("message_id", "real_id", "id", "msg_id"):
        value = raw.get(key)
        if value not in (None, "", 0, "0"):
            return str(value)
    return fallback


def normalize_message(raw, tz_name: str = "Asia/Shanghai"):
    """返回 {id, ts, sender_id, sender, text}；时间或内容不可用时返回 None。"""
    if not isinstance(raw, dict):
        return None
    ts_raw = raw.get("time") or raw.get("timestamp")
    try:
        ts = int(float(ts_raw))
    except (TypeError, ValueError):
        return None
    text = render_message(raw)
    if not text:
        return None
    uid, name = sender_of(raw)
    digest = hashlib.sha1(("%s|%s|%s|%s" % (ts, uid, name, text)).encode("utf-8")).hexdigest()[:16]
    return {
        "id": message_id_of(raw, "h" + digest),
        "ts": ts,
        "sender_id": uid,
        "sender": name,
        "text": text,
    }


def is_noise(text: str) -> bool:
    return text.strip() in _NOISE


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def plain(text: str) -> str:
    """给 HTML 用之前先去掉控制字符。"""
    return html.escape(_CTRL_RE.sub("", text or ""))
