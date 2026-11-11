"""把本地消息整理成「给模型看的」紧凑素材。"""
from __future__ import annotations

import re
from collections import Counter

from .normalize import is_noise
from .store import Store
from .util import fmt_hm, fmt_local, human_span, log, truncate

URL_RE = re.compile(r"https?://[^\s<>\"'）)】]+", re.I)
TRAIL = ".,，。;；:：!！?？、）)】]》\"'"


def display_name(target: dict) -> str:
    return (target.get("alias") or target.get("name") or str(target.get("id"))).strip()


def extract_links(text: str) -> list:
    out = []
    for match in URL_RE.findall(text or ""):
        url = match.rstrip(TRAIL)
        if url and url not in out:
            out.append(url)
    return out


def _score(line: str) -> int:
    score = 0
    if "http://" in line or "https://" in line:
        score += 6
    length = len(line)
    if length >= 80:
        score += 4
    elif length >= 40:
        score += 2
    if "?" in line or "？" in line:
        score += 3
    if any(k in line for k in ("建议", "推荐", "结论", "注意", "报错", "价格", "免费", "更新", "发布")):
        score += 2
    return score


def collapse_noise(rows: list) -> list:
    """连续的表情/图片刷屏折叠成一条。"""
    out = []
    run = []
    for row in rows:
        if is_noise(row["text"]):
            run.append(row)
            continue
        if len(run) > 2:
            out.append({"ts": run[0]["ts"], "sender": run[0]["sender"], "text": "……（%d 条表情/图片）" % len(run), "_noise": True})
        else:
            out.extend(run)
        run = []
        out.append(row)
    if len(run) > 2:
        out.append({"ts": run[0]["ts"], "sender": run[0]["sender"], "text": "……（%d 条表情/图片）" % len(run), "_noise": True})
    else:
        out.extend(run)
    return out


def build_lines(rows: list, tz_name: str) -> list:
    lines = []
    for row in collapse_noise(rows):
        text = truncate(row["text"].replace("\n", " / "), 260)
        lines.append("%s %s: %s" % (fmt_hm(row["ts"], tz_name), truncate(row["sender"], 12), text))
    return lines


def fit_budget(lines: list, budget: int) -> tuple:
    """按信息量挑选，尽量保留链接、长文、提问；返回 (lines, 省略条数)。"""
    costs = [len(line) + 1 for line in lines]
    total = sum(costs)
    if total <= budget:
        return lines, 0

    scores = [_score(line) for line in lines]
    order = sorted(range(len(lines)), key=lambda i: (-scores[i], i))
    chosen = set()
    used = 0
    for i in order:
        if used + costs[i] <= budget - 40:
            chosen.add(i)
            used += costs[i]
        if used >= budget - 40:
            break
    if not chosen:
        chosen = {0}

    out, omitted, prev = [], 0, None
    for i in sorted(chosen):
        if prev is not None and i - prev > 1:
            gap = i - prev - 1
            out.append("……（略 %d 条）……" % gap)
            omitted += gap
        out.append(lines[i])
        prev = i
    if prev is not None and len(lines) - 1 - prev > 0:
        gap = len(lines) - 1 - prev
        out.append("……（略 %d 条）……" % gap)
        omitted += gap
    return out, omitted


def group_stats(rows: list, tz_name: str) -> dict:
    participants = Counter(r["sender"] for r in rows)
    links = Counter()
    for row in rows:
        for url in extract_links(row["text"]):
            links[url] += 1
    hours = Counter(fmt_local(r["ts"], tz_name, "%H") for r in rows)
    peak = hours.most_common(1)[0][0] if hours else ""
    return {
        "messages": len(rows),
        "people": len(participants),
        "top_senders": [[name, count] for name, count in participants.most_common(8)],
        "links": [{"url": url, "count": count} for url, count in links.most_common(12)],
        "peak_hour": peak,
        "first": fmt_local(rows[0]["ts"], tz_name) if rows else "",
        "last": fmt_local(rows[-1]["ts"], tz_name) if rows else "",
    }


def build_bundle(cfg: dict, store: Store, since_ts: int, until_ts: int) -> dict:
    tz_name = cfg.get("report", {}).get("timezone", "Asia/Shanghai")
    targets = cfg.get("groups") or []
    if not targets:
        # 用库里出现过的群（名称从 groups 表取）
        names = store.group_names()
        targets = [{"id": gid, "name": name} for gid, name in names.items()]

    # 先算每个群的消息量，按比例分预算
    data = []
    for target in targets:
        rows = store.messages(str(target["id"]), since_ts, until_ts)
        if rows:
            data.append((target, rows))

    if not data:
        return {
            "version": 1,
            "generated_at": fmt_local(until_ts, tz_name, "%Y-%m-%d %H:%M"),
            "window": {"start": since_ts, "end": until_ts, "label": human_span(_dt(since_ts), _dt(until_ts), tz_name)},
            "me": cfg.get("me", {}),
            "groups": [],
        }

    max_chars = int(cfg.get("llm", {}).get("max_input_chars", 60000))
    total_msgs = sum(len(rows) for _, rows in data)
    groups = []
    for target, rows in data:
        share = max(0.2, len(rows) / max(1, total_msgs))
        budget = max(6000, int(max_chars * share))
        lines = build_lines(rows, tz_name)
        fitted, omitted = fit_budget(lines, budget)
        stats = group_stats(rows, tz_name)
        groups.append({
            "id": str(target["id"]),
            "name": display_name(target),
            "real_name": target.get("name") or "",
            "stats": stats,
            "transcript": "\n".join(fitted),
            "truncated": omitted > 0,
            "omitted": omitted,
            "lines": len(lines),
        })

    return {
        "version": 1,
        "generated_at": fmt_local(until_ts, tz_name, "%Y-%m-%d %H:%M"),
        "window": {
            "start": int(since_ts),
            "end": int(until_ts),
            "label": human_span(_dt(since_ts), _dt(until_ts), tz_name),
            "timezone": tz_name,
        },
        "me": cfg.get("me", {}),
        "groups": groups,
    }


def _dt(ts):
    import datetime as dt

    return dt.datetime.fromtimestamp(int(ts), dt.timezone.utc)


def bundle_to_text(bundle: dict) -> str:
    """给希望自己读素材的 AI 用的纯文本版本。"""
    parts = []
    for group in bundle.get("groups", []):
        stats = group["stats"]
        parts.append(
            "### 群：%s（%s 条，%s 人，峰值 %s 点）\n%s"
            % (group["name"], stats["messages"], stats["people"], stats["peak_hour"], group["transcript"])
        )
    return "\n\n".join(parts)
