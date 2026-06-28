import asyncio
import json
import uuid

from config import BOT_QQ, ENABLED_GROUPS, ENABLED_USERS, PERSONA
import memory
import commands
import pipeline


# ─────────────────────────────────────────
# 消息发送辅助函数
# ─────────────────────────────────────────

async def call_api(ws, action: str, params: dict) -> str:
    echo = str(uuid.uuid4())
    await ws.send(json.dumps({"action": action, "params": params, "echo": echo}, ensure_ascii=False))
    return echo


async def send_private_msg(ws, user_id: int, text: str):
    return await call_api(ws, "send_private_msg", {
        "user_id": user_id,
        "message": [{"type": "text", "data": {"text": text}}],
    })


async def send_group_msg(ws, group_id: int, text: str):
    return await call_api(ws, "send_group_msg", {
        "group_id": group_id,
        "message": [{"type": "text", "data": {"text": text}}],
    })


async def send_group_msg_with_at(ws, group_id: int, user_id: int, text: str):
    return await call_api(ws, "send_group_msg", {
        "group_id": group_id,
        "message": [
            {"type": "at",   "data": {"qq": str(user_id)}},
            {"type": "text", "data": {"text": f" {text}"}},
        ],
    })


async def delete_msg(ws, message_id: int):
    return await call_api(ws, "delete_msg", {"message_id": message_id})


# ─────────────────────────────────────────
# 内部辅助
# ─────────────────────────────────────────

def _seg_to_text(seg: dict) -> str:
    """把单个消息段转成可读文本。非文本类型转成中文占位，方便 AI 知道发生了什么。"""
    t = seg.get("type")
    data = seg.get("data", {})
    if t == "text":
        return data.get("text", "")
    if t == "at":
        qq = data.get("qq", "")
        if str(qq) == str(BOT_QQ):
            return f"@{PERSONA['name']}"
        if qq in ("all", "0"):
            return "@全体成员"
        name = memory.member_name(int(qq), fallback=str(qq)) if str(qq).isdigit() else str(qq)
        return f"@{name}"
    if t == "image":
        return "[动画表情]" if data.get("sub_type") in (1, "1") else "[图片]"
    if t == "face":
        return "[表情]"
    if t == "record":
        return "[语音]"
    if t == "video":
        return "[视频]"
    if t == "file":
        return f"[文件：{data.get('name', '')}]" if data.get("name") else "[文件]"
    if t == "reply":
        return "[回复]"
    if t == "forward":
        return "[转发的聊天记录]"
    if t == "json":
        return "[卡片消息]"
    if t == "share":
        title = data.get("title", "")
        return f"[分享链接：{title}]" if title else "[分享链接]"
    if t == "music":
        return "[音乐分享]"
    if t == "poke":
        return "[戳一戳]"
    return ""  # 其它未知段忽略


def _extract_text(segments: list) -> str:
    """把整条消息的所有段拼成可读文本。纯非文本消息也会得到占位，不再是空串。"""
    parts = [_seg_to_text(seg) for seg in segments]
    text = "".join(parts).strip()
    return text or "[非文本消息]"


def _is_at_bot(segments: list) -> bool:
    return any(seg.get("type") == "at" and seg["data"].get("qq") == str(BOT_QQ) for seg in segments)


def _username(user_id: int, event: dict) -> str:
    # 优先用记忆里已知的名字，否则用本次消息携带的群昵称/昵称
    known = memory.member_name(user_id, fallback="")
    if known:
        return known
    sender = event.get("sender", {})
    return sender.get("card") or sender.get("nickname") or str(user_id)


# ─────────────────────────────────────────
# 事件处理器
# ─────────────────────────────────────────

async def handle_message_event(ws, event: dict):
    message_type = event.get("message_type")
    user_id      = event.get("user_id")
    group_id     = event.get("group_id")
    segments     = event.get("message", [])
    ts           = event.get("time", 0)
    full_text    = _extract_text(segments).strip()
    username     = _username(user_id, event)
    at_bot       = _is_at_bot(segments)

    # 白名单：只处理配置过的群 / 私聊
    if message_type == "group":
        if group_id not in ENABLED_GROUPS:
            return
    elif message_type == "private":
        if user_id not in ENABLED_USERS:
            return
    else:
        return

    is_group = message_type == "group"
    skey = memory.session_key(message_type, group_id, user_id)
    print(f"[{'群' + str(group_id) if is_group else '私'}] {username}({user_id}): {full_text}")

    # /命令 不走 AI，直接路由到 commands
    if full_text.startswith("/"):
        body = full_text[1:]
        parts = body.split(maxsplit=1)
        ctx = {
            "text": body,
            "name": parts[0].lower() if parts else "",
            "arg": parts[1] if len(parts) > 1 else "",
            "user_id": user_id,
            "group_id": group_id,
            "is_group": is_group,
        }
        reply = await commands.handle(ctx)
        if reply:
            if is_group:
                await send_group_msg(ws, group_id, reply)
            else:
                await send_private_msg(ws, user_id, reply)
        return

    # 普通消息交给管线（建档 / 入历史 / AI 双重判断 / 按需发言）
    await pipeline.process(
        ws, skey=skey, is_group=is_group, group_id=group_id, user_id=user_id,
        username=username, text=full_text, ts=ts, at_bot=at_bot,
    )


async def handle_notice_event(ws, event: dict):
    notice_type = event.get("notice_type")

    if notice_type == "group_increase":
        group_id = event.get("group_id")
        user_id  = event.get("user_id")
        print(f"[通知] 新成员 {user_id} 加入群 {group_id}")
        # 修复：使用消息段格式，不混用 CQ 码
        await call_api(ws, "send_group_msg", {
            "group_id": group_id,
            "message": [
                {"type": "at",   "data": {"qq": str(user_id)}},
                {"type": "text", "data": {"text": " 欢迎加入！"}},
            ],
        })

    elif notice_type == "group_decrease":
        sub_type = event.get("sub_type")  # "leave" | "kick" | "kick_me"
        print(f"[通知] {event.get('user_id')} 离开群 {event.get('group_id')}（{sub_type}）")

    elif notice_type == "group_recall":
        print(f"[通知] 消息 {event.get('message_id')} 被 {event.get('operator_id')} 撤回")

    elif notice_type == "friend_add":
        print(f"[通知] 新好友：{event.get('user_id')}")

    elif notice_type == "group_ban":
        duration = event.get("duration", 0)
        print(f"[通知] {event.get('user_id')} 在群 {event.get('group_id')} 被{'禁言' if duration else '解禁'}，时长={duration}s")

    elif notice_type == "notify" and event.get("sub_type") == "poke":
        if event.get("target_id") == BOT_QQ:
            await send_group_msg_with_at(ws, event.get("group_id"), event.get("user_id"), "干嘛戳我 (｀・ω・´)")

    else:
        print(f"[通知] {notice_type}: {event}")


async def handle_request_event(ws, event: dict):
    request_type = event.get("request_type")

    if request_type == "friend":
        flag    = event.get("flag")
        user_id = event.get("user_id")
        print(f"[请求] 好友申请 from {user_id}，验证：{event.get('comment', '')}")
        await call_api(ws, "set_friend_add_request", {"flag": flag, "approve": True})

    elif request_type == "group":
        sub_type = event.get("sub_type")
        print(f"[请求] 群 {event.get('group_id')} 的 {sub_type} 请求，来自 {event.get('user_id')}")


async def handle_meta_event(ws, event: dict):
    meta_type = event.get("meta_event_type")
    if meta_type == "heartbeat":
        print(f"[心跳] 在线={event.get('status', {}).get('online')}，间隔={event.get('interval', 0)}ms")
    elif meta_type == "lifecycle":
        print(f"[生命周期] {event.get('sub_type')}")


async def handle_api_response(event: dict):
    print(f"[API响应] echo={event.get('echo')} status={event.get('status')} retcode={event.get('retcode')}")


# ─────────────────────────────────────────
# 事件分发入口
# ─────────────────────────────────────────

async def _safe_handle_message(ws, event: dict):
    try:
        await handle_message_event(ws, event)
    except Exception as e:
        print(f"[错误] 处理消息事件异常：{e}")


async def dispatch(ws, event: dict):
    if "echo" in event:
        await handle_api_response(event)
        return

    post_type = event.get("post_type")

    if post_type == "message":           # 不处理 message_sent，避免响应自身消息
        # 用 task 异步处理，读循环不被 AI 判断阻塞；同会话顺序由 pipeline 的锁保证
        asyncio.create_task(_safe_handle_message(ws, event))
    elif post_type == "notice":
        await handle_notice_event(ws, event)
    elif post_type == "request":
        await handle_request_event(ws, event)
    elif post_type == "meta_event":
        await handle_meta_event(ws, event)
    else:
        print(f"[未知事件] post_type={post_type}")
