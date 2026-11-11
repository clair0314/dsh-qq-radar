"""把摘要渲染成 Markdown / 自包含 HTML 日报。"""
from __future__ import annotations

import datetime as dt
import html
import json
import re
import shutil
from pathlib import Path

from .config import report_dir
from .util import atomic_write, fmt_local, get_tz, log

PALETTE = ["#C0563F", "#2F6F6A", "#7A5AA6", "#B07A2E", "#3D6FA8", "#A04A6B", "#5B7F3A", "#8A5A3B"]

_URL_RE = re.compile(r"(https?://[^\s<>\"']+)")

STATUS_CLASS = {"已聊完": "done", "仍在讨论": "live", "有待跟进": "todo"}


def esc(text) -> str:
    return html.escape(str(text if text is not None else ""), quote=True)


def rich(text) -> str:
    """转义后把 URL 变成可点链接。"""
    safe = esc(text)
    return _URL_RE.sub(lambda m: '<a href="%s" target="_blank" rel="noopener">%s</a>' % (m.group(1), m.group(1)), safe)


def weekday_cn(day: str) -> str:
    try:
        d = dt.datetime.strptime(day, "%Y-%m-%d")
        return "周" + "一二三四五六日"[d.weekday()]
    except ValueError:
        return ""


def window_bounds(summary: dict, tz_name: str):
    window = summary.get("window") or {}
    start = window.get("start")
    end = window.get("end")
    if start and end:
        return (
            dt.datetime.fromtimestamp(int(start), dt.timezone.utc),
            dt.datetime.fromtimestamp(int(end), dt.timezone.utc),
        )
    now = dt.datetime.now(dt.timezone.utc)
    return now - dt.timedelta(days=1), now


# --------------------------------------------------------------------- Markdown
def hydrate_summary(summary: dict, bundle: dict | None = None) -> dict:
    """给“AI 代写”的摘要补上 window/totals/id，少写字段也能正常渲染。"""
    s = dict(summary or {})
    b = bundle or {}
    if b:
        if not s.get("window"):
            s["window"] = b.get("window") or {}
        if not s.get("generated_at"):
            s["generated_at"] = b.get("generated_at")
        by_name = {g.get("name"): g for g in (b.get("groups") or [])}
        for g in s.get("groups") or []:
            hit = by_name.get(g.get("name"))
            if not hit:
                continue
            if not g.get("id"):
                g["id"] = hit["id"]
            for key in ("real_name", "stats", "transcript", "truncated", "omitted", "lines"):
                if not g.get(key):
                    g[key] = hit.get(key)
        if not s.get("totals"):
            msgs = people = links = 0
            for g in s.get("groups") or []:
                st = (by_name.get(g.get("name")) or {}).get("stats") or {}
                msgs += int(st.get("messages") or 0)
                people += int(st.get("people") or 0)
                links += len(g.get("links") or [])
            s["totals"] = {"groups": len(s.get("groups") or []), "messages": msgs, "people": people, "links": links}
    s.setdefault("ai", True)
    return s


def render_markdown(summary: dict, bundle: dict | None = None) -> str:
    summary = hydrate_summary(summary, bundle)
    tz_name = (summary.get("window") or {}).get("timezone") or "Asia/Shanghai"
    start, end = window_bounds(summary, tz_name)
    day = fmt_local(end, tz_name, "%Y-%m-%d")
    totals = summary.get("totals") or {}
    out = [
        "# QQ 群聊日报 · %s %s" % (day, weekday_cn(day)),
        "",
        "> 覆盖 %s ｜ %s 个群 ｜ %s 条消息 ｜ %s 人参与"
        % (
            (summary.get("window") or {}).get("label") or fmt_local(start, tz_name),
            totals.get("groups", 0),
            totals.get("messages", 0),
            totals.get("people", 0),
        ),
        "",
    ]
    if not summary.get("groups"):
        out += ["窗口内没有可总结的消息。", ""]
        return "\n".join(out)

    out.append("---")
    out.append("")
    opps = summary.get("opportunities") or []
    if opps:
        out.append("## 机会雷达")
        out.append("")
        out.append("> 从对话里提炼的需求信号，只是线索。")
        out.append("")
        for opp in opps:
            out.append("### %s（信号：%s）" % (opp.get("title", ""), opp.get("signal") or "弱"))
            out.append("")
            if opp.get("need"):
                out.append("- **需求：** %s" % opp["need"])
            if opp.get("product"):
                out.append("- **可以做什么：** %s" % opp["product"])
            for ev in opp.get("evidence") or []:
                if ev.get("quote"):
                    out.append("- **依据：** %s「%s」" % (ev.get("group", ""), ev["quote"]))
            out.append("")
        out.append("---")
        out.append("")

    for group in summary["groups"]:
        stats = group.get("stats") or {}
        out.append("## %s" % group.get("name", "群聊"))
        out.append("")
        out.append(
            "*%s 条 · %s 人 · 最活跃 %s 点%s*"
            % (
                stats.get("messages", 0),
                stats.get("people", 0),
                _peak(stats),
                " · 规则摘要" if group.get("degraded") else "",
            )
        )
        out.append("")
        out.append("**%s**" % group.get("headline", ""))
        out.append("")
        if group.get("vibe"):
            out.append("> %s" % group["vibe"])
            out.append("")
        for i, topic in enumerate(group.get("topics") or [], 1):
            out.append("### %d. %s  `%s`" % (i, topic.get("title", ""), topic.get("status", "")))
            out.append("")
            if topic.get("summary"):
                out.append(topic["summary"])
                out.append("")
            for point in topic.get("points") or []:
                out.append("- %s" % point)
            if topic.get("people"):
                out.append("")
                out.append("👥 " + "、".join(topic["people"]))
            out.append("")
        if group.get("links"):
            out.append("**值得点开**")
            out.append("")
            for link in group["links"]:
                why = (" — " + link["why"]) if link.get("why") else ""
                out.append("- [%s](%s)%s" % (link.get("title") or link.get("url"), link.get("url"), why))
            out.append("")
        if group.get("questions"):
            out.append("**还没解决**")
            out.append("")
            for q in group["questions"]:
                out.append("- %s" % q)
            out.append("")
        if group.get("mentions_me"):
            out.append("**提到你**")
            out.append("")
            for m in group["mentions_me"]:
                out.append("- %s" % m)
            out.append("")
        if group.get("highlights"):
            out.append("**群友金句**")
            out.append("")
            for h in group["highlights"]:
                who = ("%s：" % h["who"]) if h.get("who") else ""
                out.append("- %s%s" % (who, h.get("what", "")))
            out.append("")
        out.append("---")
        out.append("")

    out.append("*由 qq-digest 在本地生成，聊天记录不会离开这台电脑。*")
    out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------- HTML
CSS = """
:root{
  --bg:#F4F1EA; --paper:#FFFDF8; --ink:#1E2328; --muted:#6C7278; --line:#E4DED2;
  --accent:#C0563F; --ok:#2F6F6A; --warn:#B07A2E; --radius:16px;
  --shadow:0 1px 2px rgba(30,35,40,.05),0 8px 24px -18px rgba(30,35,40,.35);
}
@media (prefers-color-scheme:dark){
  :root{--bg:#121417;--paper:#1A1D22;--ink:#E8E6E1;--muted:#9AA0A6;--line:#2C3138;
        --shadow:0 1px 2px rgba(0,0,0,.3),0 12px 30px -20px rgba(0,0,0,.9);}
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:
  radial-gradient(1100px 420px at 12% -8%, rgba(192,86,63,.06), transparent 60%),
  radial-gradient(900px 380px at 92% 4%, rgba(47,111,106,.06), transparent 55%), var(--bg);
  color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
  line-height:1.72;font-size:16px;-webkit-font-smoothing:antialiased;
}
a{color:var(--accent);text-decoration:none;border-bottom:1px solid rgba(192,86,63,.3)}
a:hover{border-bottom-color:var(--accent)}
.wrap{max-width:900px;margin:0 auto;padding:56px 24px 80px}
.masthead{margin-bottom:44px}
.brand{display:flex;align-items:center;gap:10px;font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:var(--muted)}
.brand .dot{width:7px;height:7px;border-radius:50%;background:var(--accent);box-shadow:0 0 0 4px rgba(192,86,63,.12)}
h1{font-family:"Songti SC","Noto Serif CJK SC",Georgia,serif;font-size:clamp(34px,6vw,54px);line-height:1.18;font-weight:600;margin:18px 0 10px;letter-spacing:-.01em}
h1 em{font-style:normal;color:var(--accent)}
.sub{color:var(--muted);margin:0 0 26px;font-size:15px}
.stats{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:26px}
.stat{background:var(--paper);border:1px solid var(--line);border-radius:12px;padding:10px 16px;min-width:96px;box-shadow:var(--shadow)}
.stat b{display:block;font-size:22px;line-height:1.2;font-weight:650;font-variant-numeric:tabular-nums}
.stat span{font-size:12px;color:var(--muted);letter-spacing:.08em}
.toc{display:flex;flex-wrap:wrap;gap:8px}
.toc a{border:1px solid var(--line);background:var(--paper);border-radius:999px;padding:5px 14px;font-size:13px;color:var(--ink)}
.toc a:hover{border-color:var(--accent);color:var(--accent)}
.group{background:var(--paper);border:1px solid var(--line);border-radius:var(--radius);padding:26px 28px 22px;margin:0 0 26px;box-shadow:var(--shadow);position:relative;overflow:hidden}
.group::before{content:"";position:absolute;left:0;top:0;bottom:0;width:4px;background:var(--accent);opacity:.9}
.group-head{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}
.group-head h2{font-size:24px;margin:0;font-weight:650;letter-spacing:-.01em}
.group-head .no{font-size:12px;color:var(--accent);font-weight:700;letter-spacing:.12em}
.group-head .meta{font-size:13px;color:var(--muted);margin-left:auto;font-variant-numeric:tabular-nums}
.headline{font-size:19px;font-weight:600;margin:16px 0 6px;line-height:1.55}
.vibe{color:var(--muted);font-size:14px;margin:0 0 22px}
.topics{display:flex;flex-direction:column;gap:14px}
.topic{border:1px solid var(--line);border-radius:12px;padding:16px 18px;background:var(--paper);background:color-mix(in srgb,var(--paper) 70%,var(--bg))}
.topic-head{display:flex;align-items:center;gap:10px}
.topic-head .idx{font-size:11px;font-weight:700;color:var(--accent);letter-spacing:.08em}
.topic-head h3{font-size:16.5px;margin:0;font-weight:650}
.status{margin-left:auto;font-size:11px;padding:2px 9px;border-radius:999px;letter-spacing:.04em;white-space:nowrap}
.status.done{background:rgba(47,111,106,.1);color:var(--ok)}
.status.live{background:rgba(176,122,46,.14);color:var(--warn)}
.status.todo{background:rgba(192,86,63,.12);color:var(--accent)}
.topic p{margin:8px 0 6px;font-size:14.5px;color:#3a4046}
@media (prefers-color-scheme:dark){.topic p{color:#C9CDD3}}
.points{margin:6px 0 4px;padding-left:2px;list-style:none}
.points li{position:relative;padding-left:16px;margin:3px 0;font-size:14px;color:var(--muted)}
.points li::before{content:"";position:absolute;left:2px;top:.72em;width:5px;height:5px;border-radius:50%;background:var(--accent);opacity:.55}
.people{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.people span{font-size:12px;color:var(--muted);background:rgba(120,120,120,.08);border-radius:999px;padding:2px 9px}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:18px}
@media(max-width:640px){.cols{grid-template-columns:1fr}.wrap{padding:36px 16px 60px}.group{padding:20px 18px}}
.panel{border:1px solid var(--line);border-radius:12px;padding:14px 16px;background:transparent}
.panel h4{margin:0 0 8px;font-size:12px;letter-spacing:.12em;color:var(--muted);font-weight:600}
.panel ul{margin:0;padding-left:16px}
.panel li{font-size:14px;margin:5px 0;color:#3a4046}
@media (prefers-color-scheme:dark){.panel li{color:#C9CDD3}}
.panel.mention{border-color:rgba(192,86,63,.35);background:rgba(192,86,63,.05);margin-top:14px}
.panel.mention h4{color:var(--accent)}
.raw{margin-top:16px;border-top:1px dashed var(--line);padding-top:12px}
.raw summary{cursor:pointer;font-size:13px;color:var(--muted);list-style:none}
.raw summary::-webkit-details-marker{display:none}
.raw summary::before{content:"▸ ";color:var(--accent)}
.raw[open] summary::before{content:"▾ "}
.raw pre{white-space:pre-wrap;word-break:break-word;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px;line-height:1.7;color:var(--muted);background:rgba(120,120,120,.06);border-radius:10px;padding:14px;max-height:420px;overflow:auto;margin:10px 0 0}
.badge-degraded{font-size:11px;color:var(--warn);border:1px solid rgba(176,122,46,.35);border-radius:999px;padding:2px 8px;margin-left:8px}
.foot{color:var(--muted);font-size:12.5px;text-align:center;margin-top:40px;line-height:2}
.foot a{border:none}
.empty{background:var(--paper);border:1px dashed var(--line);border-radius:var(--radius);padding:40px;text-align:center;color:var(--muted)}
.opps{margin:0 0 30px;border:1px solid rgba(192,86,63,.28);background:linear-gradient(180deg,rgba(192,86,63,.055),rgba(192,86,63,.015));border-radius:var(--radius);padding:22px 24px 20px}
.opps-head{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:4px}
.opps-head h2{font-family:"Songti SC","Noto Serif CJK SC",Georgia,serif;font-size:23px;margin:0;font-weight:650;letter-spacing:.01em}
.opps-head .tag{font-size:11px;letter-spacing:.14em;color:var(--accent);border:1px solid rgba(192,86,63,.35);border-radius:999px;padding:2px 9px}
.opps-sub{color:var(--muted);font-size:13.5px;margin:2px 0 16px}
.opp-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media(max-width:640px){.opp-grid{grid-template-columns:1fr}}
.opp{background:var(--paper);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.opp-top{display:flex;align-items:center;gap:8px}
.opp-top h3{margin:0;font-size:15.5px;font-weight:650;line-height:1.45}
.sig{margin-left:auto;font-size:11px;padding:2px 8px;border-radius:999px;white-space:nowrap}
.sig.high{background:rgba(192,86,63,.13);color:var(--accent)}
.sig.mid{background:rgba(176,122,46,.14);color:var(--warn)}
.sig.low{background:rgba(120,120,120,.12);color:var(--muted)}
.opp p{margin:8px 0 0;font-size:13.8px;color:#3a4046}
.opp p b{color:var(--ink);font-weight:600}
.opp .ev{margin:9px 0 0;padding:8px 10px;border-left:2px solid rgba(192,86,63,.35);background:rgba(120,120,120,.045);border-radius:0 8px 8px 0;font-size:12.5px;color:var(--muted)}
.opp .ev i{font-style:normal;color:var(--accent);margin-right:6px}
@media (prefers-color-scheme:dark){.opp p{color:#C9CDD3}}
@media print{body{background:#fff}.group{box-shadow:none;break-inside:avoid}.raw{display:none}.toc{display:none}}
"""


def _peak(stats: dict) -> str:
    peak = stats.get("peak_hour") or "?"
    if isinstance(peak, str) and peak.isdigit():
        return peak + ":00"
    return str(peak)


def _stat(label: str, value) -> str:
    return '<div class="stat"><b>%s</b><span>%s</span></div>' % (esc(value), esc(label))


def _topic_html(topic: dict, index: int) -> str:
    status = topic.get("status") or "已聊完"
    cls = STATUS_CLASS.get(status, "done")
    points = "".join("<li>%s</li>" % rich(p) for p in topic.get("points") or [])
    people = "".join("<span>%s</span>" % esc(p) for p in topic.get("people") or [])
    return (
        '<article class="topic">'
        '<div class="topic-head"><span class="idx">%02d</span><h3>%s</h3>'
        '<span class="status %s">%s</span></div>'
        "<p>%s</p>%s%s</article>"
    ) % (
        index,
        esc(topic.get("title")),
        cls,
        esc(status),
        rich(topic.get("summary")),
        ("<ul class=\"points\">%s</ul>" % points) if points else "",
        ('<div class="people">%s</div>' % people) if people else "",
    )


def _group_html(group: dict, index: int, bundle_group: dict | None = None) -> str:
    accent = PALETTE[index % len(PALETTE)]
    stats = group.get("stats") or {}
    meta = "%s 条 · %s 人 · 峰值 %s" % (stats.get("messages", 0), stats.get("people", 0), _peak(stats))
    topics = "".join(_topic_html(t, i) for i, t in enumerate(group.get("topics") or [], 1))
    if not topics:
        topics = '<p class="vibe">这个群没有提炼出明确话题。</p>'

    links = ""
    if group.get("links"):
        items = "".join(
            '<li><a href="%s" target="_blank" rel="noopener">%s</a>%s</li>'
            % (esc(link.get("url")), esc(link.get("title") or link.get("url")), ("　" + esc(link["why"])) if link.get("why") else "")
            for link in group["links"]
        )
        links = '<section class="panel"><h4>值得点开</h4><ul>%s</ul></section>' % items

    questions = ""
    if group.get("questions"):
        items = "".join("<li>%s</li>" % rich(q) for q in group["questions"])
        questions = '<section class="panel"><h4>还没解决</h4><ul>%s</ul></section>' % items

    cols = ""
    if links and questions:
        cols = '<div class="cols">%s%s</div>' % (links, questions)
    elif links or questions:
        cols = '<div class="cols" style="grid-template-columns:1fr">%s%s</div>' % (links, questions)

    mention = ""
    if group.get("mentions_me"):
        items = "".join("<li>%s</li>" % rich(m) for m in group["mentions_me"])
        mention = '<section class="panel mention"><h4>提到你</h4><ul>%s</ul></section>' % items

    highlights = ""
    if group.get("highlights"):
        items = "".join(
            '<li>%s%s</li>' % (("<b>%s</b>：" % esc(h.get("who"))) if h.get("who") else "", rich(h.get("what")))
            for h in group["highlights"]
        )
        highlights = '<div class="cols" style="grid-template-columns:1fr"><section class="panel"><h4>群友金句</h4><ul>%s</ul></section></div>' % items

    raw = ""
    transcript = (bundle_group or {}).get("transcript") or ""
    if transcript:
        raw = (
            '<details class="raw"><summary>展开群聊原声 · %s 条%s</summary><pre>%s</pre></details>'
            % (
                stats.get("messages", 0),
                "（已抽样）" if (bundle_group or {}).get("truncated") else "",
                esc(transcript),
            )
        )

    degraded = '<span class="badge-degraded">规则摘要</span>' if group.get("degraded") else ""
    vibe = ('<p class="vibe">%s</p>' % rich(group["vibe"])) if group.get("vibe") else ""
    return (
        '<section class="group" id="g%d" style="--accent:%s">'
        '<div class="group-head"><span class="no">%02d</span><h2>%s</h2>%s'
        '<span class="meta">%s</span></div>'
        '<p class="headline">%s</p>%s'
        '<div class="topics">%s</div>%s%s%s%s</section>'
    ) % (
        index,
        accent,
        index + 1,
        esc(group.get("name")),
        degraded,
        meta,
        rich(group.get("headline")),
        vibe,
        topics,
        cols,
        mention,
        highlights,
        raw,
    )


def _sig_class(signal: str) -> str:
    return {"强": "high", "中": "mid"}.get(signal, "low")


def _opps_html(summary: dict) -> str:
    opps = summary.get("opportunities") or []
    if not opps:
        return ""
    cards = []
    for opp in opps:
        evidence = ""
        for ev in opp.get("evidence") or []:
            quote = ev.get("quote") or ""
            group = ev.get("group") or ""
            if not quote:
                continue
            evidence += '<p class="ev"><i>%s</i>%s</p>' % (esc(group), esc(quote))
        need = opp.get("need") or ""
        product = opp.get("product") or ""
        body = ""
        if need:
            body += "<p><b>需求：</b>%s</p>" % esc(need)
        if product:
            body += "<p><b>可以做什么：</b>%s</p>" % esc(product)
        cards.append(
            '<article class="opp"><div class="opp-top"><h3>%s</h3>'
            '<span class="sig %s">信号 %s</span></div>%s%s</article>'
            % (esc(opp.get("title")), _sig_class(opp.get("signal") or ""), esc(opp.get("signal") or "弱"), body, evidence)
        )
    return (
        '<section class="opps" id="opps"><div class="opps-head"><h2>机会雷达</h2>'
        '<span class="tag">PRODUCT SIGNALS</span></div>'
        '<p class="opps-sub">从今天各群的真实对话里提炼的需求和缺口，只是线索，不是结论。</p>'
        '<div class="opp-grid">%s</div></section>' % "".join(cards)
    )


def render_html(summary: dict, bundle: dict | None = None) -> str:
    summary = hydrate_summary(summary, bundle)
    tz_name = (summary.get("window") or {}).get("timezone") or "Asia/Shanghai"
    start, end = window_bounds(summary, tz_name)
    day = fmt_local(end, tz_name, "%Y-%m-%d")
    totals = summary.get("totals") or {}
    bundle_map = {str(g["id"]): g for g in (bundle or {}).get("groups", [])}
    groups_html = "".join(
        _group_html(g, i, bundle_map.get(str(g.get("id")))) for i, g in enumerate(summary.get("groups") or [])
    )
    if not groups_html:
        groups_html = '<div class="empty">这个时间窗口里没有可总结的消息。<br>试试把 --since 调大一点，或先运行 <code>qq-digest collect</code>。</div>'

    toc = "".join(
        '<a href="#g%d">%s</a>' % (i, esc(g.get("name")))
        for i, g in enumerate(summary.get("groups") or [])
    )
    model_badge = "AI 摘要" if summary.get("ai") else "规则摘要"
    errors = ""
    if summary.get("errors"):
        errors = '<p class="vibe">部分群总结失败：%s</p>' % esc("；".join(summary["errors"])[:300])

    return """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>QQ 群聊日报 · {day}</title>
<style>{css}</style></head>
<body><div class="wrap">
<header class="masthead">
  <div class="brand"><span class="dot"></span> QQ 群聊日报 <span style="opacity:.4">/</span> {day}</div>
  <h1>今天群里<br><em>都聊了什么</em></h1>
  <p class="sub">{label} ｜ 数据只存在本机 ｜ {model_badge}</p>
  <div class="stats">{s_groups}{s_msgs}{s_people}{s_links}</div>
  <nav class="toc">{toc}</nav>
</header>
{opps}
<main>{groups}</main>
<p class="foot">{errors}由 qq-digest 在本地生成 · {gen}<br>聊天记录不会上传到任何服务器（除非你配置了模型 API）</p>
</div></body></html>""".format(
        day=esc(day),
        css=CSS,
        label=esc((summary.get("window") or {}).get("label") or fmt_local(start, tz_name)),
        model_badge=esc(model_badge),
        s_groups=_stat("个群", totals.get("groups", 0)),
        s_msgs=_stat("条消息", totals.get("messages", 0)),
        s_people=_stat("人参与", totals.get("people", 0)),
        s_links=_stat("条链接", totals.get("links", 0)),
        toc=toc,
        groups=groups_html,
        opps=_opps_html(summary),
        errors=errors,
        gen=esc(summary.get("generated_at") or fmt_local(end, tz_name)),
    )


# --------------------------------------------------------------------- 落盘
def _base_name(summary: dict) -> str:
    tz_name = (summary.get("window") or {}).get("timezone") or "Asia/Shanghai"
    end = (summary.get("window") or {}).get("end")
    moment = dt.datetime.fromtimestamp(int(end), dt.timezone.utc) if end else dt.datetime.now(dt.timezone.utc)
    return moment.astimezone(get_tz(tz_name)).strftime("%Y-%m-%d_%H%M")


def write_reports(summary: dict, cfg: dict, bundle: dict | None = None) -> dict:
    summary = hydrate_summary(summary, bundle)
    out_dir = report_dir(cfg)
    base = _base_name(summary)
    md_path = atomic_write(out_dir / (base + ".md"), render_markdown(summary, bundle))
    html_path = atomic_write(out_dir / (base + ".html"), render_html(summary, bundle))
    json_path = atomic_write(out_dir / (base + ".json"), json.dumps(summary, ensure_ascii=False, indent=2))
    latest_html = out_dir / "latest.html"
    try:
        shutil.copyfile(html_path, latest_html)
    except OSError:
        pass
    atomic_write(out_dir / "latest.json", json.dumps(summary, ensure_ascii=False, indent=2))
    render_index(out_dir)
    log("日报已生成：%s", html_path)
    return {"md": str(md_path), "html": str(html_path), "json": str(json_path), "dir": str(out_dir)}


INDEX_CSS = """
:root{--bg:#F4F1EA;--paper:#FFFDF8;--ink:#1E2328;--muted:#6C7278;--line:#E4DED2;--accent:#C0563F}
@media (prefers-color-scheme:dark){:root{--bg:#121417;--paper:#1A1D22;--ink:#E8E6E1;--muted:#9AA0A6;--line:#2C3138}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;line-height:1.7}
.wrap{max-width:760px;margin:0 auto;padding:56px 24px 80px}
.kicker{font-size:12px;letter-spacing:.16em;color:var(--muted);text-transform:uppercase}
h1{font-family:"Songti SC",Georgia,serif;font-weight:600;font-size:40px;margin:12px 0 30px;letter-spacing:-.01em}
a{color:inherit;text-decoration:none}
.card{display:block;background:var(--paper);border:1px solid var(--line);border-radius:14px;padding:18px 20px;margin:12px 0;transition:transform .15s ease,border-color .15s ease}
.card:hover{transform:translateY(-1px);border-color:var(--accent)}
.card .date{font-size:12px;color:var(--accent);letter-spacing:.08em;font-weight:600}
.card .sum{font-size:13px;color:var(--muted);margin-top:4px}
.card h3{font-size:18px;margin:4px 0 0;font-weight:600}
.empty{color:var(--muted)}
"""


def render_index(out_dir: Path) -> Path:
    rows = []
    for path in sorted(out_dir.glob("*.json"), reverse=True):
        if path.name.startswith("latest"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        totals = data.get("totals") or {}
        day = fmt_local((data.get("window") or {}).get("end") or 0, (data.get("window") or {}).get("timezone") or "Asia/Shanghai")
        headlines = " ／ ".join(g.get("headline", "") for g in (data.get("groups") or [])[:3])
        rows.append(
            '<a class="card" href="%s.html"><div class="date">%s · %s 群 · %s 条</div><h3>%s</h3><div class="sum">%s</div></a>'
            % (esc(path.stem), esc(day), totals.get("groups", 0), totals.get("messages", 0), esc(headlines or "（无摘要）"), esc(_join_groups(data)))
        )
    body = "".join(rows) or '<p class="empty">还没有日报。运行一次 <code>qq-digest digest</code> 就会出现在这里。</p>'
    page = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>QQ 群聊日报 · 归档</title>
<style>%s</style></head><body><div class="wrap">
<div class="kicker">QQ DIGEST ARCHIVE</div><h1>群聊日报归档</h1>%s
</div></body></html>""" % (INDEX_CSS, body)
    return atomic_write(out_dir / "index.html", page)


def _join_groups(data: dict) -> str:
    names = [g.get("name", "") for g in (data.get("groups") or [])]
    if not names:
        return ""
    if len(names) <= 3:
        return " · ".join(names)
    return " · ".join(names[:3]) + " 等 %d 个群" % len(names)


def open_in_browser(path: str) -> None:
    import subprocess
    import sys

    try:
        if sys.platform == "darwin":
            subprocess.run(["open", path], check=False)
        elif sys.platform.startswith("win"):
            import os

            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.run(["xdg-open", path], check=False)
    except Exception as exc:
        log("无法自动打开浏览器：%s", exc)
