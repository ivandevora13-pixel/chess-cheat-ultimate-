"""Always-on-top, click-through transparent overlay that draws the best move."""
from __future__ import annotations

import ctypes
import tkinter as tk

import chess

KEY_COLOR = "#010203"   # becomes fully transparent on Windows
ARROW = "#17A81A"
ARROW_DARK = "#0B4F0D"
TEXT = "#FFFFFF"
TEXT_DARK = "#101010"


class BoardOverlay:
    def __init__(self, root: tk.Misc):
        self.root = root
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        try:
            self.win.attributes("-toolwindow", True)
        except tk.TclError:
            pass
        try:
            self.win.attributes("-transparentcolor", KEY_COLOR)
        except tk.TclError:
            pass
        self.win.configure(bg=KEY_COLOR)
        self.canvas = tk.Canvas(self.win, bg=KEY_COLOR, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.win.withdraw()
        self.visible = False

    # -- window management --------------------------------------------------

    def _apply_click_through(self):
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetAncestor(self.win.winfo_id(), 2)  # GA_ROOT
            if not hwnd:
                hwnd = self.win.winfo_id()
            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x00080000
            WS_EX_TRANSPARENT = 0x00000020
            WS_EX_TOOLWINDOW = 0x00000080
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                                  style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW)
        except Exception:
            pass

    def show(self, region):
        x, y, w, h = (int(v) for v in region)
        self.win.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")
        self.win.deiconify()
        self.win.lift()
        self.win.update_idletasks()
        self._apply_click_through()
        self.visible = True

    def hide(self):
        self.win.withdraw()
        self.visible = False

    def destroy(self):
        try:
            self.win.destroy()
        except Exception:
            pass

    def clear(self):
        self.canvas.delete("all")

    # -- drawing ------------------------------------------------------------

    def draw_move(self, region, flip: bool, move: chess.Move, label: str):
        x, y, w, h = (int(v) for v in region)
        if not self.visible:
            self.show(region)
        self.win.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")
        self.canvas.delete("all")

        side = min(w, h)
        ox = (w - side) / 2.0
        oy = (h - side) / 2.0
        sq = side / 8.0

        def center(square: int):
            f = chess.square_file(square)
            r = chess.square_rank(square)
            if not flip:
                col, row = f, 7 - r
            else:
                col, row = 7 - f, r
            return ox + (col + 0.5) * sq, oy + (row + 0.5) * sq

        x1, y1 = center(move.from_square)
        x2, y2 = center(move.to_square)

        dx, dy = x2 - x1, y2 - y1
        dist = max(1.0, (dx * dx + dy * dy) ** 0.5)
        ux, uy = dx / dist, dy / dist
        shrink = sq * 0.35
        sx, sy = x1 + ux * shrink, y1 + uy * shrink
        ex, ey = x2 - ux * shrink, y2 - uy * shrink

        width = max(4.0, sq * 0.16)
        self.canvas.create_line(sx, sy, ex, ey, fill=ARROW_DARK,
                                width=width + 4, capstyle="round")
        self.canvas.create_line(sx, sy, ex, ey, fill=ARROW, width=width, capstyle="round")

        # arrow head
        head = sq * 0.42
        hx, hy = x2 - ux * shrink * 0.4, y2 - uy * shrink * 0.4
        px, py = -uy, ux
        pts = [
            hx, hy,
            hx - ux * head + px * head * 0.55, hy - uy * head + py * head * 0.55,
            hx - ux * head - px * head * 0.55, hy - uy * head - py * head * 0.55,
        ]
        self.canvas.create_polygon(pts, fill=ARROW, outline=ARROW_DARK, width=2)

        # destination ring
        rr = sq * 0.30
        self.canvas.create_oval(x2 - rr, y2 - rr, x2 + rr, y2 + rr,
                                outline=ARROW_DARK, width=3)

        # label near the origin square
        size = max(10, int(sq * 0.30))
        tx, ty = x1, y1 - sq * 0.55
        if ty < size:
            ty = y1 + sq * 0.55
        for ddx, ddy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            self.canvas.create_text(tx + ddx, ty + ddy, text=label, fill=TEXT_DARK,
                                    font=("Segoe UI", size, "bold"))
        self.canvas.create_text(tx, ty, text=label, fill=TEXT,
                                font=("Segoe UI", size, "bold"))
