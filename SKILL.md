---
name: qq-digest
description: 把用户关注的 QQ 群最近聊的内容总结成中文日报（HTML/Markdown），可推送到飞书群。当用户说“群里在聊什么”“总结一下 QQ 群”“群聊日报”“看看今天群里”“把日报发到飞书”时使用。依赖本机 OneBot 11 服务（NapCat / Lagrange）读取消息。
---

# QQ 群聊日报

读本机 QQ 消息 → 每个群一份结构化摘要 → 一页能读的日报（含群聊原声）。

## 0. 先确认状态

```bash
~/.agents/skills/qq-digest/bin/qq-digest check
```

- 退出码 `0`：连接正常，会列出机器人所在的群
- 退出码 `2`：还没配置 → 运行 `qq-digest init`，再改 `~/.qq-digest/config.json`
- 退出码 `3`：连不上 OneBot → 见下面「接入 QQ」

不确定用户想总结哪些群时，先跑 `check` 看群列表，再问一句；配置里 `groups` 留空表示「全部群」。

## 1. 推荐流程：你来当摘要模型（不需要 API key）

```bash
Q=~/.agents/skills/qq-digest/bin/qq-digest
$Q bundle --collect --since 24h --out /tmp/qq/bundle.json --split /tmp/qq/parts
ls /tmp/qq/parts          # 每个群一个 txt，行格式：HH:MM 昵称: 内容
```

1. 逐个读 `/tmp/qq/parts/*.txt`（先读前 200 行通常够抓住主线，长群可以再往后翻）。
2. 按下面的契约写 `/tmp/qq/summary.json`；如果用户关心商机，再跨群扫一遍抱怨、求助和“有没有XX”的问题，写进 `opportunities`。
3. 渲染并打开：

```bash
$Q render --summary /tmp/qq/summary.json --bundle /tmp/qq/bundle.json --open
```

4. 用户要手机上看（飞书）时，再推一张卡片：

```bash
$Q push --summary /tmp/qq/summary.json --bundle /tmp/qq/bundle.json
```

5. 回复用户：日报文件路径 + 每个群 1–2 句 headline。日报正文已经写得很完整，聊天里不用复述全文。

### 时间窗口

`--since` 支持 `24h` / `3d` / `90m` / `today` / `yesterday` / `week` / ISO 时间。
用户说“今天”用 `today`；说“这两天”用 `2d`。配置里的 `collect.window_hours` 是默认值。

## 2. 一条命令（配了模型 API key 时）

```bash
$Q digest --since 24h --open          # 拉取 + AI 总结 + 日报
$Q digest --since 24h --no-llm        # 不联网，规则摘要（效果一般，但能用）
```

失败时退出码：`3` OneBot，`4` 模型。单群总结失败会降级为规则摘要，不影响其他群。

## 3. 摘要 JSON 契约

```json
{
  "groups": [
    {
      "name": "AI 工具折腾群",
      "headline": "Claude 涨价、本地跑 Qwen3、内网 wiki 接 Cursor",
      "vibe": "一边吐槽 API 涨价，一边折腾本地部署，周末还约了咖啡局",
      "topics": [
        {
          "title": "Claude 新模型与涨价",
          "summary": "新模型写前端更稳，但 Pro 月付从 20 涨到 25 美元，API 长上下文涨幅更大，小林的项目成本多了 30%。",
          "points": ["Pro 月付 $20 → $25，年付 $200 不变", "折中方案：贵的写前端，便宜的跑批"],
          "people": ["老K", "小林"],
          "status": "已聊完"
        }
      ],
      "highlights": [{"who": "老K", "what": "贵的写前端，便宜的跑批，混着用"}],
      "links": [{"title": "RAGFlow", "url": "https://github.com/infiniflow/ragflow", "why": "可私有化部署的 RAG"}],
      "questions": ["内网 wiki 有没有不部署服务就能接进 Cursor 的办法？"],
      "mentions_me": ["阿瓜问本地模型能否接 OpenAI 兼容接口，量子猫确认 llama.cpp 可以"]
    }
  ],
  "opportunities": [
    {
      "title": "AI 账号健康哨兵",
      "need": "群友同时担心封号、余额蒸发和 API 报错，缺一个统一看状态的地方",
      "product": "本地小面板：聚合多家 API 的余额、可用性、最近报错，异常推送到微信/QQ",
      "evidence": [{"group": "AI 工具折腾群", "quote": "我号里不是一块钱吗 怎么突然蒸发了"}],
      "signal": "中"
    }
  ]
}
```

规则：

- `name` 必须和素材里的群名**完全一致**，渲染时靠它对上统计数字和群聊原声。
- `topics` 3–6 个，按重要性排序；`title` ≤ 14 字；`summary` 2–3 句；`points` 2–4 条。
- `status` 只能是 `已聊完` / `有待跟进` / `还在聊`。
- `people` 只写素材里真实出现过的昵称，不要凑数。
- `headline` ≤ 28 字，回答“这个群今天最值得知道的一件事”。
- `highlights` 最多 3 条，尽量贴近原话；`links` 只放素材里真实出现的 URL；`questions` 最多 3 条。
- 素材被截断时宁可少写。**不要编造数字、人名、链接、结论。**
- `window` / `totals` / `generated_at` 可以省略，`render --bundle` 会自动从素材补齐。

### 商机视角（`opportunities`）

用户如果关心“能做什么产品/插件/商机”，重点写这个字段，0–5 条，按价值从高到低排：

- `need`：谁、在什么场景、缺什么。必须是群里真实出现的麻烦，不是泛泛的“需要 AI 助手”。
- `product`：具体形态（插件 / 网页 / CLI / 机器人 / 桌面小工具）+ 核心功能，两句话内。
- `evidence`：1–2 条逐字原话，带群名；不许改写、不许编造。
- `signal`：`强` = 多人反复提 + 有付费/求工具/踩坑表达；`中` = 单次明确需求；`弱` = 只有一句抱怨或推测。
- 找信号的四个抓手：重复出现的抱怨、求推荐/求工具、现有工具的使用障碍、愿意花钱省事的场景。
- 不写违规、爬隐私、绕过平台风控的方向；没有信号就返回空数组。

## 3.5 推送到飞书

1. 在飞书目标群：`…` → 群设置 → 群机器人 → 添加机器人 → **自定义机器人**，复制 Webhook。
2. 填进 `~/.qq-digest/config.json`：

```json
"push": {
  "feishu_webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/xxxx",
  "feishu_secret": ""
}
```

开了「签名校验」就把密钥填到 `feishu_secret`；用「自定义关键词」时，卡片里要含关键词（标题里有“日报”，一般够）。

3. 先看要发什么，再真发：

```bash
$Q push --summary /tmp/qq/summary.json --bundle /tmp/qq/bundle.json --dry-run
$Q push --summary /tmp/qq/summary.json --bundle /tmp/qq/bundle.json
```

卡片内容：时间窗口、每群 headline + 最多 3 条话题、机会雷达（最多 3 条）、页脚。完整 HTML 和群聊原声仍只在本机。`--text` 可改发纯文本，`--webhook` / `--secret` 可临时覆盖配置。

## 4. 配置

`~/.qq-digest/config.json`（`qq-digest init` 会生成）：

```json
{
  "onebot": {"base_url": "http://127.0.0.1:3000", "token": "你的 token"},
  "groups": [{"id": "123456789", "name": "AI 工具折腾群"}],
  "exclude_ids": [],
  "me": {"names": ["阿瓜"], "qq": "1008611"},
  "collect": {"window_hours": 24, "max_messages_per_group": 3000, "page_size": 100},
  "llm": {"api_key": "", "model": "deepseek-chat", "base_url": "https://api.deepseek.com/v1"},
  "report": {"dir": "reports", "timezone": "Asia/Shanghai"}
}
```

- `groups: []` = 机器人所在的全部群；`exclude_ids` 用来排除不看的群。
- `me.names` 用于标记「提到你」，填用户在群里的昵称。
- 环境变量优先级更高：`QQDIGEST_ONEBOT_URL`、`QQDIGEST_ONEBOT_TOKEN`、`QQDIGEST_LLM_API_KEY`、`QQDIGEST_LLM_BASE_URL`、`QQDIGEST_LLM_MODEL`。
- 数据目录默认 `~/.qq-digest`（消息库 `messages.db`、日报 `reports/`），可用 `QQDIGEST_HOME` 改。

## 5. 接入 QQ（OneBot 11）

QQ 官方机器人只能收实时消息、还得把机器人拉进每个群，读不了历史，所以这里用 **OneBot 11 协议 + 本机登录的 QQ 小号**（NapCat / Lagrange）。**有账号风控风险，务必用小号。**

### 方案 A：NapCat（推荐，Mac 用 Docker）

```yaml
services:
  napcat:
    image: mlikiowa/napcat-docker:latest
    restart: unless-stopped
    environment:
      MODE: ws
      WEBUI_TOKEN: 换成一个自己的口令
    ports:
      - "127.0.0.1:6099:6099"
      - "127.0.0.1:3000:3000"
    volumes:
      - ./data/qq:/app/.config/QQ
      - ./data/config:/app/napcat/config
```

```bash
docker compose up -d
```

打开 `http://127.0.0.1:6099/webui` 扫码登录，然后在 WebUI 的「网络配置」里新增 **HTTP 服务器**：host `0.0.0.0`、port `3000`、token 与配置里一致。把对应群号填进 `groups`。

### 方案 B：Lagrange.OneBot

跨平台 .NET 实现，配置 `appsettings.json` 里的 `Implementations: [{"Type":"Http","Host":"0.0.0.0","Port":3000}]`，扫码登录后同样指向 `http://127.0.0.1:3000`。

### 验证

```bash
$Q check          # 能看到群列表就通了
$Q collect --since 2h --json
```

## 6. 排错

| 现象 | 处理 |
| --- | --- |
| `连不上 OneBot 服务` | 服务没启动 / 端口不对 / token 不对；先 `curl http://127.0.0.1:3000/get_login_info -H "Authorization: Bearer <token>"` |
| `群列表为空` | 小号没加群，或 OneBot 适配器没开「获取群列表」权限 |
| 拉到 0 条消息 | 群号写错；NapCat 需要账号在群里且有历史消息权限；换 `--since 3d` 试试 |
| 摘要全是“规则摘要” | `llm.api_key` 为空或调用失败；看 `digest` 的 stderr，或改用「你来当摘要模型」流程 |
| 日报中文变成方框 | 截图/PDF 环境缺中文字体，浏览器直接打开不受影响 |
| 想清理旧数据 | `$Q prune`（默认保留 90 天，配置 `report.keep_days`） |

## 7. 隐私

- 消息只落在本机 `~/.qq-digest/messages.db`。
- 只有 `digest`（且配置了 API key）会把群聊文本发给模型；「bundle → 你写摘要 → render」流程里，文本只进入当前会话上下文。
- `--no-llm` 完全不联网。
- 配置文件权限 `0600`；不要把 `config.json`、`messages.db` 提交到 git。
