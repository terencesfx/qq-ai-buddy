import argparse
import asyncio
import json
import logging

import websockets

import config
from logconf import setup_logging


RECONNECT_MIN = 1     # 首次重连等待秒数
RECONNECT_MAX = 60    # 最大重连等待秒数

log = logging.getLogger("main")


def _parse_args():
    p = argparse.ArgumentParser(
        description="QQ AI 群友 —— 通过 OneBot WebSocket 连接，接入大模型。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    # ── WebSocket ──
    p.add_argument("--url", default="ws://110.40.173.49:3001",
                   help="OneBot 正向 WebSocket 地址")
    p.add_argument("--auth", default="0t59wQg9c~K98dvD",
                   help="OneBot access_token（会作为 Authorization: Bearer <token> 发送）")
    p.add_argument("--header", action="append", default=[], metavar="K:V",
                   help="额外的连接头，格式 名:值，可重复。会覆盖 --auth 生成的同名头。")
    # ── 大模型 API ──
    p.add_argument("--api-key", default=config.API_KEY, help="大模型 API Key")
    p.add_argument("--api-base", default=config.API_BASE, help="大模型 API Base（OpenAI 兼容）")
    p.add_argument("--model", default=config.MODEL, help="模型名")
    # ── 日志 ──
    p.add_argument("--log-dir", default="logs", help="日志目录")
    p.add_argument("--log-level", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="日志级别")
    return p.parse_args()


def _build_headers(args) -> dict:
    headers: dict[str, str] = {}
    if args.auth:
        headers["Authorization"] = f"Bearer {args.auth}"
    for h in args.header:
        if ":" not in h:
            raise SystemExit(f"--header 格式应为 名:值，收到：{h!r}")
        k, v = h.split(":", 1)
        headers[k.strip()] = v.strip()
    return headers


async def _run_once(uri: str, headers: dict, dispatch):
    async with websockets.connect(uri, additional_headers=headers) as ws:
        log.info("已连接 OneBot：%s", uri)
        async for raw in ws:
            try:
                event = json.loads(raw)
                await dispatch(ws, event)
            except json.JSONDecodeError:
                log.warning("无法解析消息：%s", raw)
            except Exception:
                log.exception("处理事件时异常")


async def main(uri: str, headers: dict, dispatch):
    delay = RECONNECT_MIN
    while True:
        try:
            await _run_once(uri, headers, dispatch)
            # 正常断开（服务端关闭连接），重置退避
            log.info("连接已断开，准备重连…")
            delay = RECONNECT_MIN
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("连接异常：%s，%ds 后重连…", e, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX)
            continue
        # 走到这里说明是正常断开，短暂等待后重连
        await asyncio.sleep(delay)


if __name__ == "__main__":
    args = _parse_args()
    setup_logging(log_dir=args.log_dir, level=args.log_level)

    # 先把 API 配置写回 config，再导入下游模块——
    # ai.py 在 import 时就会读取这些值并创建客户端。
    config.API_KEY = args.api_key
    config.API_BASE = args.api_base
    config.MODEL = args.model

    from protocol import dispatch  # noqa: E402  (必须在 config 覆盖之后导入)

    uri = args.url
    headers = _build_headers(args)
    log.info("启动：连接 %s，模型 %s @ %s", uri, config.MODEL, config.API_BASE)

    try:
        asyncio.run(main(uri, headers, dispatch))
    except KeyboardInterrupt:
        log.info("收到中断，已退出。")
