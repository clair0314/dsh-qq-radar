"""飞书自定义机器人推送：把摘要压成一张卡片。

只依赖标准库。卡片走 interactive 消息，手机上直接能读；
完整 HTML 仍然留在本机，卡片只放“读完就够”的部分。
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request

MAX_CARD_CHARS = 12000


class FeishuError(RuntimeError):
    pass


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip().replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _sign(secret: str, timestamp: int) -> str:
    string_to_sign = "%s\n%s" % (timestamp, secret)
    digest = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _date_label(summary: dict) -> str:
    if summary.get("date"):
        return str(summary["date"])
    window = summary.get("window") or {}
    start = window.get("end") or window.get("start")
    if isinstance(start, int):
        try:
            from zoneinfo import ZoneInfo

            moment = dt.datetime.fromtimestamp(start, ZoneInfo(window.get("timezone") or "Asia/Shanghai"))
        except Exception:
            moment = dt.datetime.fromtimestamp(start)
        return moment.strftime("%Y-%m-%d")
    if isinstance(start, str):
        return start[:10]
    return ""


def _window_text(summary: dict) -> str:
    window = summary.get("window") or {}
    label = window.get("label") or ""
    totals = summary.get("totals") or {}
    bits = []
    if label:
        bits.append(label)
    if totals.get("groups"):
        bits.append("%s 个群" % totals["groups"])
    if totals.get("messages"):
        bits.append("%s 条消息" % totals["messages"])
    if totals.get("people"):
        bits.append("%s 人参与" % totals["people"])
    return " ｜ ".join(str(b) for b in bits)


def build_card(summary: dict, max_topics: int = 3, title: str | None = None) -> dict:
    groups = summary.get("groups") or []
    date = _date_label(summary)
    if not title:
        title = "QQ 群聊日报" + (" · " + date if date else "")

    elements = []
    subtitle = _window_text(summary)
    if subtitle:
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": subtitle}})

    for idx, group in enumerate(groups, 1):
        name = group.get("name") or "未命名群"
        headline = _clip(group.get("headline") or "", 60)
        lines = ["**%02d %s**" % (idx, name)]
        if headline:
            lines.append(headline)
        topics = group.get("topics") or []
        for t_idx, topic in enumerate(topics[:max_topics], 1):
            t_title = _clip(topic.get("title") or "", 30)
            t_sum = _clip(topic.get("summary") or "", 76)
            if t_title and t_sum:
                lines.append("%d. %s — %s" % (t_idx, t_title, t_sum))
            elif t_title:
                lines.append("%d. %s" % (t_idx, t_title))
        elements.append({"tag": "hr"})
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}})

    opportunities = summary.get("opportunities") or []
    if opportunities:
        lines = ["🎯 **机会雷达**"]
        for opp in opportunities[:5]:
            title_o = _clip(opp.get("title") or "", 30)
            signal = opp.get("signal") or ""
            need = _clip(opp.get("need") or "", 90)
            product = _clip(opp.get("product") or "", 90)
            head = "**%s**" % title_o if title_o else "**机会**"
            if signal:
                head += "（信号：%s）" % signal
            lines.append(head)
            if need:
                lines.append("需求：%s" % need)
            if product:
                lines.append("可以做：%s" % product)
        elements.append({"tag": "hr"})
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}})

    elements.append(
        {
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": "由 qq-digest 在本地生成 · 完整日报（含原声）在电脑上：reports/latest.html",
                }
            ],
        }
    )

    card = {
        "config": {"wide_screen_mode": True, "enable_forward": True},
        "header": {
            "template": "orange",
            "title": {"tag": "plain_text", "content": title},
        },
        "elements": elements,
    }
    return card


def sign_payload(payload: dict, secret: str = "") -> dict:
    if secret:
        timestamp = int(time.time())
        payload["timestamp"] = str(timestamp)
        payload["sign"] = _sign(secret, timestamp)
    return payload


def build_payload(summary: dict, secret: str = "", title: str | None = None) -> dict:
    card = build_card(summary, title=title)
    raw = json.dumps(card, ensure_ascii=False)
    if len(raw) > MAX_CARD_CHARS:
        # 卡片过大时只保留每个群的第一条话题，避免被飞书拒收。
        card = build_card(summary, max_topics=1, title=title)
    return sign_payload({"msg_type": "interactive", "card": card}, secret)


def build_text(summary: dict) -> str:
    lines = ["QQ 群聊日报 " + _date_label(summary)]
    subtitle = _window_text(summary)
    if subtitle:
        lines.append(subtitle)
    for idx, group in enumerate(summary.get("groups") or [], 1):
        lines.append("")
        lines.append("%d. %s" % (idx, group.get("name") or ""))
        if group.get("headline"):
            lines.append(group["headline"])
        for topic in (group.get("topics") or [])[:3]:
            lines.append("· %s：%s" % (topic.get("title") or "", _clip(topic.get("summary") or "", 60)))
    if summary.get("opportunities"):
        lines.append("")
        lines.append("机会雷达")
        for opp in summary["opportunities"][:5]:
            lines.append("· %s（%s）：%s" % (opp.get("title") or "", opp.get("signal") or "", _clip(opp.get("need") or "", 60)))
    return "\n".join(lines)


def send(webhook: str, payload: dict, timeout: int = 15) -> dict:
    if not webhook:
        raise FeishuError("没有配置飞书 Webhook")
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise FeishuError("飞书返回 HTTP %s：%s" % (exc.code, detail)) from exc
    except urllib.error.URLError as exc:
        raise FeishuError("连不上飞书：%s（检查网络或 Webhook）" % exc.reason) from exc

    try:
        result = json.loads(body)
    except json.JSONDecodeError:
        return {"raw": body}
    code = result.get("code", result.get("StatusCode", 0))
    if code not in (0, None):
        raise FeishuError("飞书拒绝：%s %s" % (code, result.get("msg") or result.get("StatusMessage") or ""))
    return result
