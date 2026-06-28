import asyncio
import json
import logging
import uuid

from config import BOT_QQ, ENABLED_GROUPS, ENABLED_USERS, PERSONA
import memory
import commands
import pipeline

log = logging.getLogger("protocol")


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
    log.info("[%s] %s(%s): %s", "群" + str(group_id) if is_group else "私", username, user_id, full_text)

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
        log.info("新成员 %s 加入群 %s", user_id, group_id)
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
        log.info("%s 离开群 %s（%s）", event.get("user_id"), event.get("group_id"), sub_type)

    elif notice_type == "group_recall":
        log.info("消息 %s 被 %s 撤回", event.get("message_id"), event.get("operator_id"))

    elif notice_type == "friend_add":
        log.info("新好友：%s", event.get("user_id"))

    elif notice_type == "group_ban":
        duration = event.get("duration", 0)
        log.info("%s 在群 %s 被%s，时长=%ss", event.get("user_id"), event.get("group_id"),
                 "禁言" if duration else "解禁", duration)

    elif notice_type == "notify" and event.get("sub_type") == "poke":
        if event.get("target_id") == BOT_QQ:
            await send_group_msg_with_at(ws, event.get("group_id"), event.get("user_id"), "干嘛戳我 (｀・ω・´)")

    else:
        log.debug("其它通知 %s: %s", notice_type, event)


async def handle_request_event(ws, event: dict):
    request_type = event.get("request_type")

    if request_type == "friend":
        flag    = event.get("flag")
        user_id = event.get("user_id")
        log.info("好友申请 from %s，验证：%s", user_id, event.get("comment", ""))
        await call_api(ws, "set_friend_add_request", {"flag": flag, "approve": True})

    elif request_type == "group":
        sub_type = event.get("sub_type")
        log.info("群 %s 的 %s 请求，来自 %s", event.get("group_id"), sub_type, event.get("user_id"))


async def handle_meta_event(ws, event: dict):
    meta_type = event.get("meta_event_type")
    if meta_type == "heartbeat":
        log.debug("心跳 在线=%s 间隔=%sms", event.get("status", {}).get("online"), event.get("interval", 0))
    elif meta_type == "lifecycle":
        log.info("生命周期：%s", event.get("sub_type"))


async def handle_api_response(event: dict):
    status = event.get("status")
    retcode = event.get("retcode")
    # 失败的 API 调用值得 warning，成功的降为 debug
    if status == "ok" or retcode == 0:
        log.debug("API响应 echo=%s status=%s retcode=%s", event.get("echo"), status, retcode)
    else:
        log.warning("API调用失败 echo=%s status=%s retcode=%s", event.get("echo"), status, retcode)


# ─────────────────────────────────────────
# 事件分发入口
# ─────────────────────────────────────────

async def _safe_handle_message(ws, event: dict):
    try:
        await handle_message_event(ws, event)
    except Exception:
        log.exception("处理消息事件异常")


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
        log.warning("未知事件 post_type=%s", post_type)
