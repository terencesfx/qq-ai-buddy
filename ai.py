"""AI 决策层。

每条消息过一次：把人设 + 成员记忆 + 最近上下文交给模型，模型做"双重判断"——
要不要提取记忆、要不要发言——并以结构化 JSON 返回：

    {
      "memory":       [{"uid": <QQ号>, "fact": "<要记住的事>"}, ...],  // 没有就 []
      "should_speak": true | false,
      "reply":        "<要发的话，should_speak=false 时为空>"
    }

模型可在判断前调用 get_history 工具回溯更早的历史（tool-loop）。
"""
import json
import logging

from openai import AsyncOpenAI

from config import API_KEY, API_BASE, MODEL, PERSONA, BOT_QQ
import memory
import tools

_client = AsyncOpenAI(api_key=API_KEY, base_url=API_BASE)

_MAX_TOOL_ROUNDS = 4  # tool-loop 最大轮数，防止模型反复翻历史死循环

log = logging.getLogger("ai")


def _persona_block() -> str:
    p = PERSONA
    return (
        f"你叫{p['name']}。\n"
        f"【背景】{p['background']}\n"
        f"【性格】{p['personality']}\n"
        f"【兴趣】{'、'.join(p['interests'])}\n"
        f"【擅长】{'、'.join(p['expertise'])}\n"
        f"【发言准则】\n{p['speak_policy']}"
    )


def _members_block() -> str:
    members = memory.all_members()
    if not members:
        return "（你还不认识群里任何人，刚来，慢慢熟悉。）"
    lines = []
    for uid, m in members.items():
        name = m.get("name", str(uid))
        mems = m.get("memories", [])
        if mems:
            lines.append(f"  {name}(QQ {uid})：{'；'.join(mems)}")
        else:
            lines.append(f"  {name}(QQ {uid})：（还不太了解）")
    return "你认识的群成员及你对他们的了解：\n" + "\n".join(lines)


def _system_prompt(is_group: bool, must_reply: bool) -> str:
    fmt = (
        "群消息会以『昵称(QQ号)：内容』的格式呈现，你能据此区分谁在说话、@ 了谁。"
        if is_group else
        "这是一对一私聊。"
    )
    if must_reply:
        speak_rule = (
            "should_speak 规则：最新一条消息**直接 @ 了你（点名找你）**，"
            "所以你必须回复，should_speak 一律为 true，reply 写出你要说的话。"
        )
    else:
        speak_rule = (
            "should_speak 规则：严格按上面的发言准则，默认 false，宁缺毋滥。"
            "注意：文本里出现你的名字不一定是在找你（可能是在议论、或恰好重名），要结合上下文判断。"
        )
    return (
        f"{_persona_block()}\n\n"
        f"{_members_block()}\n\n"
        f"{fmt}\n\n"
        "你的任务：阅读下面的最近对话，做两件事的判断，并只输出一个 JSON 对象，"
        "不要有任何额外文字或代码块标记：\n"
        '{\n'
        '  "memory": [{"uid": QQ号(整数), "fact": "值得长期记住的关于这个人的事"}],\n'
        '  "should_speak": true 或 false,\n'
        '  "reply": "若发言则填内容，否则空字符串"\n'
        '}\n'
        "memory 规则：只记真正有长期价值的信息（喜好、身份、重要事件、约定等），"
        "闲聊寒暄不要记，没有就给空数组。\n"
        f"{speak_rule}\n"
        "如果最近上下文不足以判断，可以先调用 get_history 工具回溯更早的消息。"
    )


def _render_recent(skey: str, n: int) -> str:
    msgs = memory.recent(skey, n)
    if not msgs:
        return "（还没有历史消息）"
    lines = []
    for m in msgs:
        if m.get("role") == "assistant":
            lines.append(f"[{m['ts']}] 你：{m['content']}")
        else:
            who = m.get("name", "某人")
            uid = m.get("uid", "")
            lines.append(f"[{m['ts']}] {who}({uid})：{m['content']}")
    return "\n".join(lines)


def _parse_decision(text: str) -> dict:
    """容错解析模型输出的 JSON。失败则视为不发言、不记忆。"""
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.lstrip().lower().startswith("json"):
            s = s.lstrip()[4:]
    # 截取第一个 { 到最后一个 }
    i, j = s.find("{"), s.rfind("}")
    if i != -1 and j != -1 and j > i:
        s = s[i:j + 1]
    try:
        d = json.loads(s)
    except json.JSONDecodeError:
        log.warning("模型输出非合法 JSON，按不发言处理。原始输出：%r", (text or "")[:300])
        return {"memory": [], "should_speak": False, "reply": ""}
    return {
        "memory": d.get("memory") or [],
        "should_speak": bool(d.get("should_speak")),
        "reply": (d.get("reply") or "").strip(),
    }


async def decide(skey: str, is_group: bool, context_window: int, must_reply: bool = False) -> dict:
    """对当前会话最近上下文做双重判断。返回 {memory, should_speak, reply}。
    must_reply=True（被 @）时强制发言，AI 只负责生成回复内容与记忆。"""
    messages = [
        {"role": "system", "content": _system_prompt(is_group, must_reply)},
        {"role": "user", "content": "最近的对话：\n" + _render_recent(skey, context_window)},
    ]
    ctx = {"skey": skey}

    for _ in range(_MAX_TOOL_ROUNDS):
        resp = await _client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=tools.schemas(),
        )
        msg = resp.choices[0].message

        if msg.tool_calls:
            # 把助手的工具调用回合放回消息列表，再逐个执行并回喂结果
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in msg.tool_calls
                ],
            })
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = await tools.execute(tc.function.name, args, ctx)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })
            continue

        decision = _parse_decision(msg.content)
        if must_reply:
            # 被 @ 是结构化事实，强制发言；万一模型仍判 false 或漏了 reply 也兜住
            decision["should_speak"] = True
            if not decision["reply"]:
                decision["reply"] = "嗯？怎么啦~"
        return decision

    # 工具轮数用尽仍未给结论
    if must_reply:
        return {"memory": [], "should_speak": True, "reply": "嗯？怎么啦~"}
    return {"memory": [], "should_speak": False, "reply": ""}
