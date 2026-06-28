"""管线编排：每条命中白名单的消息走这里。

流程：建档/更新成员 → 追加历史(JSONL) → 前置过滤 → AI 双重判断
      → 落记忆 → 按需发言。

并发：每个会话一把 asyncio.Lock，同一群/私聊的消息串行处理，
保证 AI 看到的顺序与历史写入顺序一致；不同会话之间互不阻塞。
"""
import asyncio
import re

import protocol  # 运行时调用其 send_* / 在 import 期不引用其属性，避免循环导入问题
import memory
import ai
from config import CONTEXT_WINDOW

# 每个会话一把锁
_locks: dict[str, asyncio.Lock] = {}

# 前置过滤：这些消息照常入历史，但不触发 AI（除非 @ 了机器人）
_TRIVIAL = re.compile(r"^(?:[。，、~!?！？.\s]|哈+|嗯+|啊+|哦+|草|6+|赞|顶)+$")
# 纯非文本消息（图片/表情/语音等占位），无文字内容时不值得单独触发 AI
_PURE_NONTEXT = re.compile(r"^(?:\[[^\]]*\])+$")


def _lock(skey: str) -> asyncio.Lock:
    lock = _locks.get(skey)
    if lock is None:
        lock = asyncio.Lock()
        _locks[skey] = lock
    return lock


def _should_consult_ai(text: str, at_bot: bool) -> bool:
    if at_bot:
        return True
    s = (text or "").strip()
    if not s:
        return False
    if _TRIVIAL.match(s):
        return False
    if _PURE_NONTEXT.match(s):   # 纯图片/表情/语音等，无文字
        return False
    return True


async def process(ws, *, skey: str, is_group: bool, group_id, user_id,
                  username: str, text: str, ts: int, at_bot: bool):
    async with _lock(skey):
        # 1) 成员建档/更新（公共，按 user_id）
        memory.touch_member(user_id, username, ts)

        # 2) 入历史（每条都进，实时落盘）
        memory.append_message(skey, ts, "user", text, uid=user_id, name=username)

        # 3) 前置过滤：省掉对琐碎消息的模型调用
        if not _should_consult_ai(text, at_bot):
            return

        # 4) AI 双重判断（被 @ 是结构化事实，强制发言，AI 只生成内容+记忆）
        try:
            decision = await ai.decide(skey, is_group, CONTEXT_WINDOW, must_reply=at_bot)
        except Exception as e:
            print(f"[AI] 判断失败：{e}")
            return

        # 5) 落记忆
        for item in decision.get("memory", []):
            try:
                uid = int(item["uid"])
                fact = str(item["fact"]).strip()
            except (KeyError, ValueError, TypeError):
                continue
            if fact:
                memory.add_memory(uid, fact)
                print(f"[记忆] {memory.member_name(uid, str(uid))} → {fact}")

        # 6) 按需发言
        if decision.get("should_speak") and decision.get("reply"):
            reply = decision["reply"]
            if is_group:
                await protocol.send_group_msg(ws, group_id, reply)
            else:
                await protocol.send_private_msg(ws, user_id, reply)
            # bot 自己的发言也进历史
            memory.append_message(skey, ts, "assistant", reply)
            print(f"[发言] {skey}: {reply}")
