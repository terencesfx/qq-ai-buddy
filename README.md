# QQ AI 群友 · 小晨

一个有人设、有长期记忆、能自主决定是否发言的 QQ 群聊 AI 机器人。

通过 [OneBot v11](https://github.com/botuniverse/onebot-11) 正向 WebSocket 连接（如 NapCat / Lagrange / go-cqhttp），把群聊消息交给大模型（OpenAI 兼容接口），由模型在每条消息上做**双重判断**：要不要提取记忆、要不要发言。

---

## 特性

- **人设驱动**：小晨是一个在腾讯上班的插画师（女），结构化的人设（背景 / 性格 / 兴趣 / 擅长 / 发言准则）会拼进 system prompt，决定她怎么说话、何时开口。
- **自主发言**：默认潜水，只有被 @、聊到她擅长/感兴趣的话题、或氛围合适时才插话——不刷存在感。被 @ 是结构化事实，**强制回复**，不交给模型判断。
- **长期记忆**：模型自己判断哪些信息值得长期记住，按 `user_id` 为每个群友维护一份公共档案（跨群共享），持久化到 JSON。
- **群友视角的历史**：每个群/私聊维护一条带时间戳的对话历史（JSONL，实时落盘），每条消息标注发言人。模型看最近 N 条作为上下文，不够时可用 `get_history` 工具按时间戳回溯更早的历史。
- **命令系统**：`/` 开头的消息不走 AI，直接路由到命令处理器（`/help` `/profile` `/记住` `/忘记`）。
- **工具接口（MCP 预留）**：基于 function calling 的工具注册机制，`get_history` 是第一个工具，后续可挂接外部信息源。
- **白名单**：只处理你配置的群和私聊，其余消息一律忽略。
- **健壮性**：断线指数退避自动重连；每条消息异步处理、同会话串行锁保证顺序；前置过滤省掉对琐碎消息（纯表情/刷屏）的模型调用；非文本消息（图片/文件/语音）转占位文本，不污染历史。
- **日志**：标准 `logging`，控制台 + 文件轮转，跨平台。
- **部署**：自带 systemd 服务模板，开机自启、崩溃自动重启。

---

## 架构

```
main.py        入口：解析命令行参数、初始化日志、连接 OneBot WebSocket、断线重连
  └ protocol.py  协议层：解析 OneBot 事件、消息段转文本、白名单过滤、路由(/命令 / 普通消息)、发消息 API
       ├ commands.py  / 命令注册表与处理器
       └ pipeline.py  管线编排：建档 → 入历史 → 前置过滤 → AI 判断 → 落记忆 → 按需发言（每会话串行锁）
            └ ai.py     AI 决策层：组装上下文、tool-loop、结构化双重判断输出
                 ├ tools.py   AI 可调用工具（get_history 起步，预留 MCP）
                 └ memory.py  持久化：JSONL 历史 + 成员档案/公共记忆
config.py      配置：API、机器人 QQ、白名单、上下文窗口、结构化人设
logconf.py     日志配置（控制台 + 文件轮转）
```

### 一条消息的处理流程

```
收到群/私聊消息
  → 白名单过滤（不在 ENABLED_GROUPS/ENABLED_USERS 直接忽略）
  → 是 /命令？  ── 是 ──→ commands 处理，直接回复，结束
  → 否，进 pipeline：
       1. 成员建档/更新（按 user_id）
       2. 追加进历史 JSONL（每条都进，实时落盘）
       3. 前置过滤（纯表情/刷屏/纯图片 → 不调 AI，除非被 @）
       4. 调 AI 双重判断（被 @ 则强制发言）
            ├ 可选：调 get_history 工具回溯更早历史
            └ 返回 {memory, should_speak, reply}
       5. 落记忆（写 members.json）
       6. should_speak 为真 → 发送 reply，并把 reply 也写入历史
```

---

## 安装

需要 Python 3.10+。

```bash
git clone <你的仓库>
cd python
python -m venv .venv

# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate

pip install -r requirements.txt
```

依赖：`openai`、`websockets`。

---

## 前置条件：OneBot 实现

本项目只是 OneBot 客户端，需要一个 OneBot v11 实现提供 QQ 接入，并开启**正向 WebSocket**。推荐 [NapCat](https://github.com/NapNeko/NapCatQQ)。

在 OneBot 实现里配置：
- 开启正向 WebSocket 服务（默认端口如 3001）
- 设置 access_token（对应本项目的 `--auth`）

---

## 配置

### config.py

```python
API_KEY  = "sk-xxxxxxxx"            # 大模型 API Key（也可命令行传）
API_BASE = "https://api.openai.com/v1"  # OpenAI 兼容接口地址
MODEL    = "gpt-5.5"                # 模型名

BOT_QQ   = 1234567890              # 机器人自己的 QQ 号（用于识别 @）

ENABLED_GROUPS = { 756576090 }      # 只处理这些群
ENABLED_USERS  = { }                # 只处理这些人的私聊

CONTEXT_WINDOW = 30                 # 每次给 AI 的最近消息条数
DATA_DIR       = "data"             # 历史与记忆存储目录

PERSONA = { ... }                   # 结构化人设，在此调整小晨的设定
```

> `BOT_QQ`、`ENABLED_GROUPS` 必须按你的实际情况填，否则机器人不会响应任何消息。

### 命令行参数

WebSocket 和大模型 API 可通过命令行覆盖 `config.py` 的默认值：

| 参数 | 说明 | 默认值来源 |
|---|---|---|
| `--url` | OneBot 正向 WebSocket 地址 | 内置 |
| `--auth` | OneBot access_token（拼成 `Authorization: Bearer <token>`） | 内置 |
| `--header K:V` | 额外连接头，可重复，覆盖同名头 | 无 |
| `--api-key` | 大模型 API Key | `config.API_KEY` |
| `--api-base` | 大模型 API Base | `config.API_BASE` |
| `--model` | 模型名 | `config.MODEL` |
| `--log-dir` | 日志目录 | `logs` |
| `--log-level` | `DEBUG`/`INFO`/`WARNING`/`ERROR` | `INFO` |

`python main.py --help` 查看完整说明。

---

## 运行

### 本地开发

```bash
python main.py
# 或覆盖参数
python main.py --url ws://127.0.0.1:3001 --auth 你的令牌 \
               --api-key sk-xxx --api-base https://api.openai.com/v1 --model gpt-5.5
```

日志同时输出到控制台和 `logs/bot.log`（自动轮转）。`Ctrl+C` 退出。

### 命令（群里/私聊发送）

| 命令 | 说明 |
|---|---|
| `/help` | 查看命令列表 |
| `/profile [QQ号]` | 查看某人档案与记忆（省略则看自己） |
| `/记住 [QQ号] 内容` | 手动加一条记忆（省略 QQ 号记到自己名下） |
| `/忘记 [QQ号] 序号` | 删除某人的一条记忆（序号见 `/profile`） |

---

## 部署到 Linux（systemd）

```bash
# 1. 上传代码到服务器，例如 /opt/qqbot，建虚拟环境装依赖
cd /opt/qqbot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. 配置密钥（root-only 文件，避免写进 service / 暴露在进程列表）
cp .env.example .env
vim .env            # 填 ONEBOT_TOKEN 与 OPENAI_API_KEY
chmod 600 .env

# 3. 安装服务（先按实际改 User / WorkingDirectory / --url / --model 等）
sudo cp qqbot.service /etc/systemd/system/
sudo vim /etc/systemd/system/qqbot.service
sudo systemctl daemon-reload
sudo systemctl enable --now qqbot

# 4. 查看状态与日志
systemctl status qqbot
journalctl -u qqbot -f             # journald 实时日志
tail -f /opt/qqbot/logs/bot.log    # 脚本自身的轮转日志
```

维护：`systemctl restart qqbot` 重启，`systemctl stop qqbot` 停止。改完代码后 `systemctl restart qqbot`。

> 若 OneBot 实现（如 NapCat）也跑在同机的 systemd 下，建议在 `qqbot.service` 用 `After=`/`Wants=` 声明依赖（文件内有注释示例）。

---

## 数据与存储

```
data/
  history/
    group_<群号>.jsonl     # 群聊历史，每行一条带时间戳的消息
    private_<QQ号>.jsonl   # 私聊历史
  members.json             # 成员档案 + 公共记忆（按 user_id，跨群共享）
logs/
  bot.log                  # 运行日志（轮转）
```

记忆是公共的：同一个人在不同群、私聊里共用一份档案。建议把 `data/` 和 `logs/` 加入 `.gitignore`，不要提交聊天记录与运行数据。

---

## 当前限制

- **不理解图片内容**：图片/文件/语音会被转成 `[图片]`/`[文件:...]`/`[语音]` 占位文本，模型知道"有人发了图"但看不到画面内容。后续可接多模态。
- **QQ 图床 URL 有时效**：即便日后做多模态，历史里的旧图链接可能已失效。
- **每条非琐碎消息至少一次模型调用**：活跃群的调用量与费用需留意，前置过滤已挡掉一部分。
- **依赖模型遵守 JSON 输出格式**：解析层有容错，非法 JSON 会按"不发言"处理并记 WARNING 日志。

---

## 安全提示

- `--auth` / `--api-key` 写在命令行会留在 shell 历史和进程列表。生产部署用 `.env` + systemd `EnvironmentFile` 注入，命令行用 `${VAR}` 引用。
- `config.py` 里若填了真实密钥，注意不要提交到公开仓库。
- 自动通过好友申请（`set_friend_add_request approve=True`）默认开启，按需在 `protocol.py` 调整。
