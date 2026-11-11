"""把素材交给模型，产出结构化的群聊摘要；没有模型时用规则兜底。"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import llm
from .util import log, truncate

SYSTEM = (
    "你是一名中文群聊情报编辑。你的读者没时间爬楼，只想用三分钟知道一个群聊了什么、"
    "有什么值得跟进。你只输出 JSON，不输出任何解释。"
)

PROMPT = """以下是一个 QQ 群在 {window} 的聊天记录，已按时间排序（可能省略了部分重复/无意义内容）。

群名：{name}
记录：
---
{transcript}
---

【读者信息】{me}

请输出一个 JSON 对象（不要 markdown 代码块），字段如下：
{{
  "headline": "一句话日报标题，≤28字，要具体、有信息量，不要“大家热烈讨论”这类空话",
  "vibe": "整体氛围或当天主题，≤40字",
  "topics": [
    {{
      "title": "话题名，≤14字",
      "summary": "2-3 句话讲清起因、经过、结论",
      "points": ["关键信息点，最多 4 条，保留数字、产品名、结论、分歧"],
      "people": ["参与者昵称"],
      "status": "已聊完 | 仍在讨论 | 有待跟进"
    }}
  ],
  "highlights": [{{"who": "昵称", "what": "值得记住的原话/金句/梗/结论"}}],
  "links": [{{"title": "链接标题", "url": "http...", "why": "为什么值得点开"}}],
  "questions": ["群里提出但没人回答或没有结论的问题"],
  "mentions_me": ["提到读者、或需要读者回应的内容"]
}}

规则：
- topics 给 3-6 个，按讨论热度排序；哪怕在闲聊，也要提炼出具体在聊什么。
- 只写记录里真实出现的信息，禁止编造；不确定就省略。
- 忽略“哈哈”“+1”“收到”这类寒暄；但如果大家围绕某件事反复接话，要提炼这件事。
- 保留具体名词、数字和链接；links 只收记录里出现过的 URL。
- 没有内容的字段用空数组 []。
- 全部用中文。"""


OPPORTUNITY_PROMPT = """下面是几个 QQ 群在 {window} 的聊天摘要和原始片段。读者想从这些讨论里发现「可以做什么 AI 产品 / 插件 / 小工具」的机会。

{corpus}

请输出一个 JSON 对象（不要 markdown 代码块）：
{{
  "opportunities": [
    {{
      "title": "机会名，≤16字，像一个产品名或方向名",
      "need": "用户真实的需求/痛点，一句话说清谁在什么场景下缺什么",
      "product": "可以做的具体东西：形态（插件/网页/CLI/机器人）+ 核心功能，2 句话内",
      "evidence": [{{"group": "群名", "quote": "聊天里的原话，≤40字，必须逐字来自素材"}}],
      "signal": "强 | 中 | 弱"
    }}
  ]
}}

规则：
- 0-5 条，按价值从高到低排。没有真实信号就返回空数组，不要硬凑，不要写“可以做 AI 助手”这种空泛方向。
- 信号强的标准：多人反复提、有明确付费/求工具/踩坑表达、现有方案不好用。
- evidence 必须逐字摘自素材，不许改写或编造；每条机会 1-2 条依据。
- 优先找：重复出现的抱怨、求推荐/求工具、现有工具的使用障碍、愿意花钱解决的麻烦。
- 不要涉及违规、爬隐私、绕过平台风控的方向。
- 全部用中文。"""


def _norm_opportunities(raw) -> list:
    out = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        title = _text(item.get("title"), 40)
        if not title:
            continue
        evidence = []
        for ev in item.get("evidence") or []:
            if isinstance(ev, dict) and ev.get("quote"):
                evidence.append({"group": _text(ev.get("group"), 40), "quote": _text(ev.get("quote"), 120)})
        signal = item.get("signal") if item.get("signal") in ("强", "中", "弱") else "弱"
        out.append(
            {
                "title": title,
                "need": _text(item.get("need"), 200),
                "product": _text(item.get("product"), 300),
                "evidence": evidence[:2],
                "signal": signal,
            }
        )
        if len(out) >= 5:
            break
    return out


def synthesize_opportunities(groups: list, cfg: dict, window_label: str, source_groups: list | None = None) -> list:
    llm_cfg = cfg.get("llm", {})
    raw_by_id = {str(g.get("id")): g for g in (source_groups or [])}
    blocks = []
    for g in groups:
        topics = "；".join(
            "%s：%s" % (t.get("title"), _text(t.get("summary"), 120)) for t in (g.get("topics") or [])[:4]
        )
        transcript = _text(g.get("transcript") or (raw_by_id.get(str(g.get("id"))) or {}).get("transcript"), 2600)
        blocks.append(
            "## 群：%s\n摘要：%s\n未解决问题：%s\n原声片段：\n%s"
            % (
                g.get("name"),
                topics or g.get("headline") or "",
                "；".join(g.get("questions") or []) or "无",
                transcript,
            )
        )
    corpus = "\n\n".join(blocks)
    prompt = OPPORTUNITY_PROMPT.format(window=window_label, corpus=corpus)
    raw = llm.chat(
        base_url=llm_cfg.get("base_url", ""),
        api_key=llm_cfg.get("api_key", ""),
        model=llm_cfg.get("model", ""),
        messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
        temperature=0.4,
        timeout=float(llm_cfg.get("timeout", 120)),
        json_mode=bool(llm_cfg.get("json_mode", True)),
    )
    data = llm.extract_json(raw)
    if isinstance(data, dict):
        return _norm_opportunities(data.get("opportunities"))
    return []


def _me_desc(cfg: dict, bundle: dict) -> str:
    me = (bundle.get("me") or cfg.get("me") or {})
    names = [n for n in (me.get("names") or []) if n]
    qq = me.get("qq") or ""
    if not names and not qq:
        return "未提供（mentions_me 返回空数组）"
    parts = []
    if names:
        parts.append("昵称：" + "、".join(names))
    if qq:
        parts.append("QQ：" + str(qq))
    return "；".join(parts)


def _text(value, limit: int) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = "；".join(str(v) for v in value if v)
    return truncate(str(value), limit)


def _norm_topics(raw) -> list:
    topics = []
    for item in raw or []:
        if isinstance(item, str):
            item = {"title": item}
        if not isinstance(item, dict):
            continue
        title = _text(item.get("title") or item.get("topic"), 30)
        if not title:
            continue
        points = item.get("points") or item.get("key_points") or []
        if isinstance(points, str):
            points = [points]
        people = item.get("people") or item.get("participants") or []
        if isinstance(people, str):
            people = [people]
        status = _text(item.get("status"), 8)
        if status not in ("已聊完", "仍在讨论", "有待跟进"):
            status = "已聊完"
        topics.append({
            "title": title,
            "summary": _text(item.get("summary") or item.get("detail"), 300),
            "points": [_text(p, 160) for p in points[:6] if _text(p, 160)],
            "people": [_text(p, 20) for p in people[:8] if _text(p, 20)],
            "status": status,
        })
    return topics[:8]


def _norm_links(raw) -> list:
    out, seen = [], set()
    for item in raw or []:
        if isinstance(item, str):
            item = {"url": item}
        if not isinstance(item, dict):
            continue
        url = _text(item.get("url") or item.get("link"), 400)
        if not url.startswith("http") or url in seen:
            continue
        seen.add(url)
        out.append({
            "title": _text(item.get("title") or item.get("name"), 80) or url,
            "url": url,
            "why": _text(item.get("why") or item.get("reason") or item.get("desc"), 160),
        })
    return out[:10]


def normalize_summary(raw, group: dict, degraded: bool = False, error: str = "") -> dict:
    if isinstance(raw, dict) and isinstance(raw.get("summary"), dict):
        raw = raw["summary"]
    if not isinstance(raw, dict):
        raw = {}
    questions = raw.get("questions") or []
    if isinstance(questions, str):
        questions = [questions]
    mentions = raw.get("mentions_me") or raw.get("mentions") or []
    if isinstance(mentions, str):
        mentions = [mentions]
    highlights = []
    for item in raw.get("highlights") or []:
        if isinstance(item, str):
            item = {"what": item}
        if not isinstance(item, dict):
            continue
        what = _text(item.get("what") or item.get("text"), 160)
        if what:
            highlights.append({"who": _text(item.get("who") or item.get("sender"), 20), "what": what})

    return {
        "id": group["id"],
        "name": group["name"],
        "headline": _text(raw.get("headline"), 60) or ("%s 条消息，%s 人参与" % (group["stats"]["messages"], group["stats"]["people"])),
        "vibe": _text(raw.get("vibe") or raw.get("atmosphere"), 80),
        "topics": _norm_topics(raw.get("topics")),
        "highlights": highlights[:6],
        "links": _norm_links(raw.get("links")),
        "questions": [_text(q, 160) for q in questions[:6] if _text(q, 160)],
        "mentions_me": [_text(m, 160) for m in mentions[:6] if _text(m, 160)],
        "stats": group["stats"],
        "degraded": degraded,
        "error": error,
    }


# ---------------------------------------------------------------- 规则兜底
def burst_topics(group: dict, tz_name: str = "Asia/Shanghai", max_topics: int = 5) -> list:
    """按时间断点把群聊切成几段，用信息量最高的消息当标题。"""
    lines = []
    for line in (group.get("transcript") or "").splitlines():
        if line.startswith("……"):
            continue
        # 21:33 张三: 内容
        head, sep, body = line.partition(": ")
        if not sep:
            continue
        parts = head.split(" ", 1)
        ts = parts[0]
        sender = parts[1] if len(parts) > 1 else ""
        lines.append({"ts": ts, "sender": sender, "text": body})
    if not lines:
        return []

    def score(item):
        value = 0
        if "http" in item["text"]:
            value += 5
        value += min(len(item["text"]) // 40, 4)
        if "?" in item["text"] or "？" in item["text"]:
            value += 2
        return value

    # 按小时切段
    buckets = {}
    order = []
    for item in lines:
        hour = item["ts"].split(":")[0]
        if hour not in buckets:
            buckets[hour] = []
            order.append(hour)
        buckets[hour].append(item)
    # 相邻小时合并，最多 max_topics 段
    segments = []
    for hour in order:
        if segments and len(segments) >= max_topics:
            segments[-1].extend(buckets[hour])
        else:
            segments.append(list(buckets[hour]))

    topics = []
    for seg in segments:
        ranked = sorted(seg, key=score, reverse=True)
        best = ranked[0]
        people = []
        for item in seg:
            if item["sender"] and item["sender"] not in people:
                people.append(item["sender"])
        points = []
        for item in ranked[:4]:
            points.append("%s：%s" % (item["sender"], truncate(item["text"], 120)))
        topics.append({
            "title": truncate(best["text"], 22) or ("%s 点前后" % best["ts"]),
            "summary": "这一时段共 %d 条消息，%s 等 %d 人参与。" % (len(seg), "、".join(people[:3]), len(people)),
            "points": points,
            "people": people[:8],
            "status": "已聊完",
        })
    return topics


def fallback_summary(group: dict, cfg: dict) -> dict:
    stats = group["stats"]
    topics = burst_topics(group, cfg.get("report", {}).get("timezone", "Asia/Shanghai"))
    text = group.get("transcript") or ""
    lines = [ln for ln in text.splitlines() if ": " in ln]
    questions = [truncate(ln.split(": ", 1)[1], 120) for ln in lines if "?" in ln or "？" in ln][:5]

    me = cfg.get("me") or {}
    names = [n for n in (me.get("names") or []) if n]
    mentions = []
    for ln in lines:
        if any(name in ln for name in names):
            mentions.append(truncate(ln, 160))
    mentions = mentions[:5]

    highlights = []
    for ln in sorted(lines, key=len, reverse=True)[:4]:
        ts, _, rest = ln.partition(" ")
        sender, _, body = rest.partition(": ")
        highlights.append({"who": sender, "what": truncate(body, 120)})

    links = [{"title": item["url"].split("/")[2] if "//" in item["url"] else item["url"],
              "url": item["url"], "why": "记录里被分享 %d 次" % item["count"]}
             for item in stats.get("links", [])[:6]]

    topic_names = "、".join("「%s」" % t["title"][:12] for t in topics[:2])
    headline = "%s 条消息、%s 人参与" % (stats["messages"], stats["people"])
    if topic_names:
        headline += "，主要聊了" + topic_names
    top_names = "、".join(name for name, _ in stats.get("top_senders", [])[:3])
    vibe = "最活跃时段 %s 点" % stats.get("peak_hour", "?")
    if top_names:
        vibe += "，发言最多的是 " + top_names

    return {
        "id": group["id"],
        "name": group["name"],
        "headline": truncate(headline, 60),
        "vibe": vibe,
        "topics": topics,
        "highlights": highlights,
        "links": links,
        "questions": questions,
        "mentions_me": mentions,
        "stats": stats,
        "degraded": True,
        "error": "",
    }


# ---------------------------------------------------------------- 主流程
def _ask_llm(group: dict, cfg: dict, bundle: dict) -> dict:
    llm_cfg = cfg.get("llm", {})
    prompt = PROMPT.format(
        name=group["name"],
        window=bundle["window"]["label"],
        transcript=group.get("transcript") or "（没有消息）",
        me=_me_desc(cfg, bundle),
    )
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
    raw = llm.chat(
        base_url=llm_cfg.get("base_url", ""),
        api_key=llm_cfg.get("api_key", ""),
        model=llm_cfg.get("model", ""),
        messages=messages,
        temperature=float(llm_cfg.get("temperature", 0.3)),
        timeout=float(llm_cfg.get("timeout", 120)),
        json_mode=bool(llm_cfg.get("json_mode", True)),
    )
    try:
        data = llm.extract_json(raw)
    except llm.LLMError:
        # 让模型自己修一次
        repair = messages + [
            {"role": "assistant", "content": raw[:4000]},
            {"role": "user", "content": "上面的输出不是合法 JSON。请只输出一个 JSON 对象，不要任何解释、不要代码块。"},
        ]
        raw2 = llm.chat(
            base_url=llm_cfg.get("base_url", ""),
            api_key=llm_cfg.get("api_key", ""),
            model=llm_cfg.get("model", ""),
            messages=repair,
            temperature=0.1,
            timeout=float(llm_cfg.get("timeout", 120)),
            json_mode=bool(llm_cfg.get("json_mode", True)),
        )
        data = llm.extract_json(raw2)
    if isinstance(data, dict) and "groups" in data and isinstance(data["groups"], list) and data["groups"]:
        data = data["groups"][0]
    result = normalize_summary(data, group)
    if not result["mentions_me"]:
        result["mentions_me"] = _scan_mentions(group, cfg)
    return result


def _scan_mentions(group: dict, cfg: dict) -> list:
    names = [n for n in ((cfg.get("me") or {}).get("names") or []) if n]
    if not names:
        return []
    out = []
    for line in (group.get("transcript") or "").splitlines():
        if any(name in line for name in names):
            out.append(truncate(line.partition(": ")[2] or line, 160))
        if len(out) >= 5:
            break
    return out


def summarize(bundle: dict, cfg: dict, use_llm: bool = True) -> dict:
    groups = bundle.get("groups") or []
    tz_name = cfg.get("report", {}).get("timezone", "Asia/Shanghai")
    if not groups:
        return {
            "version": 1,
            "generated_at": bundle.get("generated_at", ""),
            "window": bundle.get("window", {}),
            "ai": False,
            "totals": {"groups": 0, "messages": 0, "people": 0, "links": 0},
            "groups": [],
            "errors": ["窗口内没有消息"],
        }

    llm_cfg = cfg.get("llm", {})
    ai = bool(use_llm and llm_cfg.get("api_key"))
    results = []
    if ai:
        workers = max(1, min(int(llm_cfg.get("concurrency", 3) or 3), len(groups)))
        log("调用模型总结 %d 个群（%s）…", len(groups), llm_cfg.get("model"))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_ask_llm, group, cfg, bundle): group for group in groups}
            for future in as_completed(futures):
                group = futures[future]
                try:
                    result = future.result()
                    log("  完成「%s」", group["name"])
                except Exception as exc:  # 单个群失败不影响整体
                    log("  「%s」总结失败：%s", group["name"], exc)
                    result = fallback_summary(group, cfg)
                    result["error"] = str(exc)
                results.append(result)
        results.sort(key=lambda r: [g["id"] for g in groups].index(r["id"]))
    else:
        for group in groups:
            results.append(fallback_summary(group, cfg))

    opportunities = []
    if ai:
        try:
            opportunities = synthesize_opportunities(
                results, cfg, bundle.get("window", {}).get("label") or "", bundle.get("groups")
            )
            log("  机会雷达：%d 条线索", len(opportunities))
        except Exception as exc:
            log("  机会雷达生成失败：%s", exc)

    totals = {
        "groups": len(results),
        "messages": sum(r["stats"]["messages"] for r in results),
        "people": sum(r["stats"]["people"] for r in results),
        "links": sum(len(r["links"]) for r in results),
    }
    return {
        "version": 1,
        "generated_at": bundle.get("generated_at", ""),
        "window": bundle.get("window", {}),
        "ai": ai,
        "totals": totals,
        "groups": results,
        "opportunities": opportunities,
        "errors": [r["error"] for r in results if r.get("error")],
    }
