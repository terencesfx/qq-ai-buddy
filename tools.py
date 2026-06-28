"""AI 可调用的工具层（function calling）。

这是未来"MCP 那层"的落地点：每个工具登记 {schema, 实现}，
ai.py 在 tool-loop 里按模型的调用请求执行。get_history 是第一个工具，
让 AI 在最近上下文不够时，按时间戳回溯更早的群/私聊历史。

新增外部信息工具时，照 get_history 的写法 @tool(...) 注册即可。
"""
import json

import memory

# 工具注册表：name -> {"schema": <OpenAI function def>, "fn": <callable(ctx, **args)>}
_registry: dict[str, dict] = {}


def tool(name: str, description: str, parameters: dict):
    """注册一个工具。parameters 是 JSON Schema（OpenAI function 的 parameters 字段）。"""
    def deco(fn):
        _registry[name] = {
            "schema": {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": parameters,
                },
            },
            "fn": fn,
        }
        return fn
    return deco


def schemas() -> list[dict]:
    """所有工具的 OpenAI function 定义，传给 chat.completions 的 tools 参数。"""
    return [t["schema"] for t in _registry.values()]


async def execute(name: str, args: dict, ctx: dict) -> str:
    """执行某个工具，返回字符串结果（回喂给模型）。ctx 携带会话信息（如 skey）。"""
    t = _registry.get(name)
    if not t:
        return f"[工具错误] 未知工具：{name}"
    try:
        result = t["fn"](ctx, **args)
        return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return f"[工具错误] {name} 执行失败：{e}"


# ─────────────────────────────────────────
# 工具：按时间戳回溯历史
# ─────────────────────────────────────────

@tool(
    name="get_history",
    description=(
        "当最近的上下文不足以理解或回应时，回溯更早的历史消息。"
        "传入一个时间戳 before_ts，返回该时间之前最靠近的若干条消息。"
        "可多次调用以继续往前翻。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "before_ts": {
                "type": "integer",
                "description": "回溯此 Unix 时间戳（秒）之前的消息。一般填当前上下文里最早那条的 ts。",
            },
            "limit": {
                "type": "integer",
                "description": "返回条数，默认 30，最大 100。",
            },
        },
        "required": ["before_ts"],
    },
)
def _get_history(ctx: dict, before_ts: int, limit: int = 30) -> str:
    skey = ctx["skey"]
    limit = max(1, min(int(limit), 100))
    msgs = memory.history_before(skey, int(before_ts), limit)
    if not msgs:
        return "（没有更早的历史了）"
    lines = []
    for m in msgs:
        if m.get("role") == "assistant":
            lines.append(f"[{m['ts']}] 你：{m['content']}")
        else:
            who = m.get("name", "某人")
            uid = m.get("uid", "")
            lines.append(f"[{m['ts']}] {who}({uid})：{m['content']}")
    return "\n".join(lines)
