import argparse
import asyncio
import json

import websockets

import config


RECONNECT_MIN = 1     # 首次重连等待秒数
RECONNECT_MAX = 60    # 最大重连等待秒数


def _parse_args():
    p = argparse.ArgumentParser(
        description="QQ AI 群友 —— 通过 OneBot WebSocket 连接，接入大模型。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    # ── WebSocket ──
    p.add_argument("--url", default="ws://127.0.0.1:3001",
                   help="OneBot 正向 WebSocket 地址")
    p.add_argument("--auth", default="xxx",
                   help="OneBot access_token（会作为 Authorization: Bearer <token> 发送）")
    p.add_argument("--header", action="append", default=[], metavar="K:V",
                   help="额外的连接头，格式 名:值，可重复。会覆盖 --auth 生成的同名头。")
    # ── 大模型 API ──
    p.add_argument("--api-key", default=config.API_KEY, help="大模型 API Key")
    p.add_argument("--api-base", default=config.API_BASE, help="大模型 API Base（OpenAI 兼容）")
    p.add_argument("--model", default=config.MODEL, help="模型名")
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
        print("Connected.")
        async for raw in ws:
            try:
                event = json.loads(raw)
                await dispatch(ws, event)
            except json.JSONDecodeError:
                print(f"[错误] 无法解析消息：{raw}")
            except Exception as e:
                print(f"[错误] 处理事件时异常：{e}")


async def main(uri: str, headers: dict, dispatch):
    delay = RECONNECT_MIN
    while True:
        try:
            await _run_once(uri, headers, dispatch)
            # 正常断开（服务端关闭连接），重置退避
            print("[连接] 已断开，准备重连…")
            delay = RECONNECT_MIN
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[连接] 连接异常：{e}，{delay}s 后重连…")
            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX)
            continue
        # 走到这里说明是正常断开，短暂等待后重连
        await asyncio.sleep(delay)


if __name__ == "__main__":
    args = _parse_args()

    # 先把 API 配置写回 config，再导入下游模块——
    # ai.py 在 import 时就会读取这些值并创建客户端。
    config.API_KEY = args.api_key
    config.API_BASE = args.api_base
    config.MODEL = args.model

    from protocol import dispatch  # noqa: E402  (必须在 config 覆盖之后导入)

    uri = args.url
    headers = _build_headers(args)
    print(f"[配置] 连接 {uri}，模型 {config.MODEL} @ {config.API_BASE}")

    try:
        asyncio.run(main(uri, headers, dispatch))
    except KeyboardInterrupt:
        print("\n已退出。")
