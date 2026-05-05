"""Shared UI helpers: console factory + box-style selection.

Centralizes color/ASCII mode so commands don't have to detect it themselves.
Resolution order (highest precedence first):
    1. Explicit configure() call from the CLI (--ascii / --no-color flags)
    2. TT_MGMT_ASCII / TT_MGMT_NO_COLOR environment variables
    3. NO_COLOR (https://no-color.org)
    4. TERM=dumb
    5. Non-TTY stdout (auto-disable color, keep Unicode boxes)
"""

from __future__ import annotations

import os
import sys
from typing import Optional

from rich.console import Console
from rich import box as _box


_ascii_mode: bool = False
_no_color: bool = False
_console: Optional[Console] = None


def _truthy(val: Optional[str]) -> bool:
    return bool(val) and val.lower() not in ("0", "false", "no", "off", "")


def _detect_defaults() -> tuple[bool, bool]:
    """Return (ascii_mode, no_color) from environment + TTY heuristics."""
    ascii_mode = _truthy(os.environ.get("TT_MGMT_ASCII"))
    no_color = (
        _truthy(os.environ.get("TT_MGMT_NO_COLOR"))
        or "NO_COLOR" in os.environ
        or os.environ.get("TERM") == "dumb"
        or not sys.stdout.isatty()
    )
    return ascii_mode, no_color


def configure(ascii: Optional[bool] = None, no_color: Optional[bool] = None) -> None:
    """Configure UI mode. None = leave at auto-detected default."""
    global _ascii_mode, _no_color, _console
    auto_ascii, auto_no_color = _detect_defaults()
    _ascii_mode = auto_ascii if ascii is None else bool(ascii)
    _no_color = auto_no_color if no_color is None else bool(no_color)
    _console = None  # rebuild on next get_console()


def is_ascii() -> bool:
    return _ascii_mode


def is_no_color() -> bool:
    return _no_color


def get_console() -> Console:
    global _console
    if _console is None:
        # force_terminal must reflect actual TTY state — overriding it breaks
        # rich.live.Live (cursor positioning). no_color alone is enough to
        # strip ANSI when the user explicitly asked for plain output.
        _console = Console(
            no_color=_no_color,
            force_terminal=False if not sys.stdout.isatty() else None,
            highlight=False,
        )
    return _console


def get_box():
    """Default box style for primary tables (ROUNDED → ASCII in ascii mode)."""
    return _box.ASCII if _ascii_mode else _box.ROUNDED


def get_simple_box():
    """Box style for compact / inline tables (SIMPLE → ASCII in ascii mode)."""
    return _box.ASCII if _ascii_mode else _box.SIMPLE


def get_double_box():
    """Box style for emphasis panels (DOUBLE → ASCII_DOUBLE_HEAD in ascii mode)."""
    return _box.ASCII_DOUBLE_HEAD if _ascii_mode else _box.DOUBLE


# Initialize from environment at import time so early imports get sane defaults.
configure()
