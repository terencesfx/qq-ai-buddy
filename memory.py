"""持久化层：群/私聊历史（JSONL）+ 成员档案与公共记忆（JSON）。

- 历史按"会话"分文件存：group:{id} → data/history/group_{id}.jsonl
                          private:{id} → data/history/private_{id}.jsonl
  每行一条消息记录，追加写即实时同步，按时间戳可回查。
- 记忆是公共的：按 user_id 为每个群友维护一份档案（昵称 + AI 提炼的记忆条目），
  存在 data/members.json，跨群共享。
"""
import json
import os

from config import DATA_DIR

_HISTORY_DIR = os.path.join(DATA_DIR, "history")
_MEMBERS_FILE = os.path.join(DATA_DIR, "members.json")

# 成员档案缓存：{user_id(int): {...}}；首次访问时从磁盘加载
_members: dict | None = None


# ─────────────────────────────────────────
# 会话 key
# ─────────────────────────────────────────

def session_key(message_type: str, group_id: int | None, user_id: int | None) -> str:
    if message_type == "group":
        return f"group:{group_id}"
    return f"private:{user_id}"


def _history_path(skey: str) -> str:
    safe = skey.replace(":", "_")
    return os.path.join(_HISTORY_DIR, f"{safe}.jsonl")


# ─────────────────────────────────────────
# 历史：JSONL 追加 / 读取 / 按时间戳回查
# ─────────────────────────────────────────

def append_message(skey: str, ts: int, role: str, content: str,
                   uid: int | None = None, name: str | None = None) -> None:
    """追加一条消息到会话历史并立即落盘。role: 'user' | 'assistant'。"""
    os.makedirs(_HISTORY_DIR, exist_ok=True)
    record = {"ts": ts, "role": role, "content": content}
    if uid is not None:
        record["uid"] = uid
    if name is not None:
        record["name"] = name
    with open(_history_path(skey), "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read_all(skey: str) -> list[dict]:
    path = _history_path(skey)
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def recent(skey: str, n: int) -> list[dict]:
    """最近 n 条消息（按时间顺序，旧→新）。"""
    return _read_all(skey)[-n:]


def history_before(skey: str, before_ts: int, limit: int = 30) -> list[dict]:
    """取时间戳 < before_ts 的历史消息，返回最靠近 before_ts 的 limit 条（旧→新）。
    供 AI 通过 get_history 工具回溯更早的对话。"""
    older = [m for m in _read_all(skey) if m.get("ts", 0) < before_ts]
    return older[-limit:]


# ─────────────────────────────────────────
# 成员档案 + 公共记忆（按 user_id）
# ─────────────────────────────────────────

def _load_members() -> dict:
    global _members
    if _members is not None:
        return _members
    if os.path.exists(_MEMBERS_FILE):
        try:
            with open(_MEMBERS_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            _members = {int(k): v for k, v in raw.items()}
        except (json.JSONDecodeError, ValueError):
            _members = {}
    else:
        _members = {}
    return _members


def _save_members() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    data = {str(k): v for k, v in _load_members().items()}
    with open(_MEMBERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def touch_member(user_id: int, name: str, ts: int) -> dict:
    """见到某人就建档 / 更新昵称与最后活跃时间。返回该成员档案。"""
    members = _load_members()
    m = members.get(user_id)
    if m is None:
        m = {"name": name, "first_seen": ts, "last_seen": ts, "memories": []}
        members[user_id] = m
    else:
        if name and name != "未知":
            m["name"] = name
        m["last_seen"] = ts
    _save_members()
    return m


def add_memory(user_id: int, fact: str) -> None:
    """为某个群友追加一条公共记忆（去重）。"""
    members = _load_members()
    m = members.setdefault(user_id, {"name": str(user_id), "memories": []})
    mem = m.setdefault("memories", [])
    if fact not in mem:
        mem.append(fact)
        _save_members()


def remove_memory(user_id: int, index: int) -> str | None:
    """按序号删除某人的一条记忆，返回被删内容（越界返回 None）。"""
    members = _load_members()
    m = members.get(user_id)
    if not m:
        return None
    mem = m.get("memories", [])
    if 0 <= index < len(mem):
        removed = mem.pop(index)
        _save_members()
        return removed
    return None


def get_member(user_id: int) -> dict | None:
    return _load_members().get(user_id)


def all_members() -> dict:
    return _load_members()


def member_name(user_id: int, fallback: str = "未知") -> str:
    m = _load_members().get(user_id)
    return m["name"] if m and m.get("name") else fallback
