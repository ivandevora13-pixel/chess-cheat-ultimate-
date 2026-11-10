"""Chess Screen Assistant — entry point.

Watches a selected screen region with a chess board (chess.com / lichess /
any site or app whose piece art matches a template set), recognizes the
position and shows Stockfish's best move in a click-through overlay.
"""
from __future__ import annotations

import ctypes
import sys


def _set_dpi_awareness():
    """Must run before Tk/mss so window coords == captured pixel coords."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor v2
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def main() -> int:
    _set_dpi_awareness()
    import tkinter as tk
    from ui import MainWindow

    root = tk.Tk()
    MainWindow(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
