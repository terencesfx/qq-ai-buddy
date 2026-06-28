"""/ 开头的命令路由。这些消息不走 AI，直接由这里处理。

ctx 字段：
  text       去掉首个 / 后的完整文本
  name       命令名（首个空格前的词，小写）
  arg        命令名之后的剩余文本（去首尾空格）
  user_id    发送者 QQ
  group_id   群号（私聊为 None）
  is_group   bool
处理器返回字符串作为回复；返回 None 表示不回复。
"""
import memory

# name -> {"fn": handler, "help": "说明"}
_registry: dict[str, dict] = {}


def command(name: str, help: str = ""):
    def deco(fn):
        _registry[name] = {"fn": fn, "help": help}
        return fn
    return deco


async def handle(ctx: dict) -> str | None:
    name = ctx["name"]
    entry = _registry.get(name)
    if not entry:
        return f"未知命令 /{name}，发 /help 看看有哪些。"
    return await entry["fn"](ctx)


# ─────────────────────────────────────────
# 基础命令
# ─────────────────────────────────────────

@command("help", "查看命令列表")
async def _help(ctx):
    lines = ["可用命令："]
    for n, e in _registry.items():
        lines.append(f"  /{n} —— {e['help']}")
    return "\n".join(lines)


@command("profile", "查看某人档案，用法：/profile [QQ号]，省略则看自己")
async def _profile(ctx):
    arg = ctx["arg"].strip()
    uid = int(arg) if arg.isdigit() else ctx["user_id"]
    m = memory.get_member(uid)
    if not m:
        return f"还没有 {uid} 的档案。"
    mems = m.get("memories", [])
    body = "\n".join(f"  {i}. {x}" for i, x in enumerate(mems)) or "  （暂无记忆）"
    return f"【{m.get('name', uid)}（QQ {uid}）】\n记忆：\n{body}"


@command("记住", "手动加一条记忆，用法：/记住 [QQ号] 内容；省略QQ号则记到自己名下")
async def _remember(ctx):
    parts = ctx["arg"].split(maxsplit=1)
    if parts and parts[0].isdigit():
        uid = int(parts[0])
        fact = parts[1] if len(parts) > 1 else ""
    else:
        uid = ctx["user_id"]
        fact = ctx["arg"].strip()
    if not fact:
        return "要记什么呢？用法：/记住 [QQ号] 内容"
    memory.add_memory(uid, fact)
    return f"记下了：{memory.member_name(uid, str(uid))} → {fact}"


@command("忘记", "删除某人的一条记忆，用法：/忘记 [QQ号] 序号（序号见 /profile）")
async def _forget(ctx):
    parts = ctx["arg"].split()
    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
        uid, idx = int(parts[0]), int(parts[1])
    elif len(parts) == 1 and parts[0].isdigit():
        uid, idx = ctx["user_id"], int(parts[0])
    else:
        return "用法：/忘记 [QQ号] 序号（序号见 /profile）"
    removed = memory.remove_memory(uid, idx)
    return f"已忘记：{removed}" if removed else "没有这条记忆（序号超范围？）"
