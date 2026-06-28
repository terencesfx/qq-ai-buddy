"""日志配置：跨平台（Windows 开发 / Linux 部署通用）。

控制台 + 文件双输出，文件按大小轮转，不会撑爆磁盘。
各模块用 logging.getLogger(__name__) 取 logger 即可。
"""
import logging
import os
from logging.handlers import RotatingFileHandler

_FMT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

_configured = False


def setup_logging(log_dir: str = "logs", level: str = "INFO",
                  max_bytes: int = 5 * 1024 * 1024, backups: int = 5) -> None:
    """初始化根 logger。控制台输出 + logs/bot.log（轮转保留 backups 个）。
    重复调用只生效一次。"""
    global _configured
    if _configured:
        return

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    fmt = logging.Formatter(_FMT, datefmt=_DATEFMT)

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    try:
        os.makedirs(log_dir, exist_ok=True)
        file_handler = RotatingFileHandler(
            os.path.join(log_dir, "bot.log"),
            maxBytes=max_bytes, backupCount=backups, encoding="utf-8",
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError as e:
        # 文件日志建不起来也不致命，至少保留控制台
        root.warning("无法创建日志文件（仅控制台输出）：%s", e)

    # 第三方库降噪
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    _configured = True
