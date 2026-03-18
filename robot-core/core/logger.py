"""
core/logger.py — Structured, rotating logger setup.

Call setup_logging() once at startup before importing any other module.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Optional


# ANSI colours for console (disabled on Windows or when not a tty)
_COLOURS = {
    "DEBUG":    "\033[36m",   # cyan
    "INFO":     "\033[32m",   # green
    "WARNING":  "\033[33m",   # yellow
    "ERROR":    "\033[31m",   # red
    "CRITICAL": "\033[35m",   # magenta
}
_RESET = "\033[0m"


class _ColouredFormatter(logging.Formatter):
    def __init__(self, use_colour: bool = True, **kwargs):
        super().__init__(**kwargs)
        self._colour = use_colour and sys.stderr.isatty()

    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        if self._colour:
            colour = _COLOURS.get(record.levelname, "")
            return f"{colour}{msg}{_RESET}"
        return msg


def setup_logging(
    level: str = "INFO",
    log_file: Optional[Path] = None,
    max_bytes: int = 5 * 1024 * 1024,   # 5 MB
    backup_count: int = 3,
) -> None:
    """
    Configure root logger.

    Args:
        level:        "DEBUG" / "INFO" / "WARNING" / "ERROR"
        log_file:     path to rotating log file; None = console only
        max_bytes:    rotate when file exceeds this size
        backup_count: number of rotated backups to keep
    """
    numeric = getattr(logging, level.upper(), logging.INFO)

    fmt = "%(asctime)s  %(levelname)-8s  %(name)-24s  %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    handlers: list[logging.Handler] = []

    # Console handler
    ch = logging.StreamHandler(sys.stderr)
    ch.setFormatter(_ColouredFormatter(fmt=fmt, datefmt=datefmt))
    handlers.append(ch)

    # File handler (optional)
    if log_file is not None:
        log_file = Path(log_file)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )
        fh.setFormatter(logging.Formatter(fmt=fmt, datefmt=datefmt))
        handlers.append(fh)

    logging.basicConfig(level=numeric, handlers=handlers, force=True)

    # Quiet noisy third-party loggers
    for noisy in ("urllib3", "httpx", "httpcore", "PIL", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger(__name__).debug(
        f"Logging initialised: level={level}, file={log_file}"
    )
