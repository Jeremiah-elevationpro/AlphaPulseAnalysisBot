"""
AlphaPulse - Centralized Logger
"""

import sys
import logging
import os
from logging.handlers import RotatingFileHandler
from config.settings import LOG_LEVEL, LOG_FILE


def get_logger(name: str) -> logging.Logger:
    """Return a named logger with both console and rotating file output."""
    log_file = _resolve_log_file()
    log_dir = os.path.dirname(log_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    logger = logging.getLogger(name)
    replay_mode = _is_replay_process()
    if replay_mode:
        # Replay may run while the live bot has recently used alphapulse.log.
        # Rebuild handlers so no stale rotating live-file handler can survive.
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass
    elif logger.handlers:
        return logger  # already configured

    logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))
    logger.propagate = False

    fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)-8s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console — force UTF-8 on Windows so emoji in Telegram mock messages don't crash
    if sys.platform == "win32":
        import io
        stream = io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
        )
    else:
        stream = sys.stdout

    ch = logging.StreamHandler(stream)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # Rotating file (10 MB x 5 backups) — always UTF-8
    if replay_mode or _use_plain_file_handler():
        fh = logging.FileHandler(log_file, encoding="utf-8")
    else:
        fh = RotatingFileHandler(
            log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


def _resolve_log_file() -> str:
    override = os.getenv("ALPHAPULSE_LOG_FILE")
    if override:
        return override
    if _is_replay_process():
        return os.path.join("logs", "historical_replay.log")
    return LOG_FILE


def _is_replay_process() -> bool:
    if os.getenv("ALPHAPULSE_REPLAY_MODE", "").lower() in ("1", "true", "yes"):
        return True
    return "historical_replay" in " ".join(sys.argv).lower()


def _use_plain_file_handler() -> bool:
    """Avoid Windows rollover rename failures when another process has the log open."""
    override = os.getenv("ALPHAPULSE_ROTATING_LOGS", "").lower()
    if override in ("1", "true", "yes"):
        return False
    if override in ("0", "false", "no"):
        return True
    return sys.platform == "win32"


# Absolute path to the runtime event log — safe to compute at import time.
_RUNTIME_LOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "logs",
    "spencer_runtime.log",
)


class _RuntimeInstanceFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "instance_id"):
            record.instance_id = os.getenv("ALPHAPULSE_INSTANCE_ID", "api")
        return True


def get_runtime_logger() -> logging.Logger:
    """
    Dedicated logger for high-level lifecycle events only.

    Format:  [YYYY-MM-DD HH:MM:SS] EVENT_TYPE: message
    File:    logs/spencer_runtime.log

    Only write events that have operational meaning to the user/frontend:
    BOT STARTED/STOPPED, ANALYSIS PHASE, SCAN STARTED/COMPLETE,
    WATCHLIST LOOP STARTED, NO WATCHLIST SETUPS FOUND, WATCHLIST ALERT SENT/FAILED,
    TELEGRAM SEND SUCCESS/FAILED.
    """
    name = "alphapulse.runtime"
    rl = logging.getLogger(name)
    if rl.handlers:
        return rl

    os.makedirs(os.path.dirname(_RUNTIME_LOG_PATH), exist_ok=True)

    rl.setLevel(logging.INFO)
    rl.propagate = False

    fmt = logging.Formatter(
        "[%(asctime)s] instance_id=%(instance_id)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh = logging.FileHandler(_RUNTIME_LOG_PATH, encoding="utf-8")
    fh.setFormatter(fmt)
    fh.addFilter(_RuntimeInstanceFilter())
    rl.addHandler(fh)

    return rl
