"""Tkinter control panel: preview, controls and message pump."""
from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog

import chess
from PIL import Image
from PIL import ImageTk

import downloads
import engines
import settings
import vision
from overlay import BoardOverlay
from worker import AnalysisWorker, VisionWorker

FAIR_PLAY = ("Heads-up: use this for study, analysis and watching games. Using engine help in "
             "online rated games violates the rules of chess.com / lichess and gets accounts banned.")


class MainWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(settings.APP_NAME)
        root.resizable(False, False)

        self.state = settings.State(settings.load())
        self.piece_sets = vision.load_piece_sets(settings.TEMPLATES_DIR)
        self.ui_q: queue.Queue = queue.Queue()
        self.analysis_q: queue.Queue = queue.Queue()
        self.overlay = BoardOverlay(root)
        self.preview_photo = None
        self.last_board = None

        self._build_ui()
        self.vision_worker = VisionWorker(self.state, self.ui_q, self.analysis_q, self.piece_sets)
        self.analysis_worker = AnalysisWorker(self.state, self.ui_q, self.analysis_q)
        self.vision_worker.start()
        self.analysis_worker.start()

        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._sync_widgets()
        self._engine_label_refresh()
        self._update_region_label()
        if not self.piece_sets:
            self._set_status("No piece templates found — click 'Get templates' to download them.")
        self._poll()

    # ------------------------------------------------------------------ UI

    def _build_ui(self):
        outer = ttk.Frame(self.root, padding=8)
        outer.grid(row=0, column=0)

        # ---- left: preview + board setup --------------------------------
        left = ttk.Frame(outer)
        left.grid(row=0, column=0, sticky="n")

        self.preview = tk.Canvas(left, width=384, height=384, bg="#1e1e1e",
                                 highlightthickness=1, highlightbackground="#666")
        self.preview.grid(row=0, column=0, columnspan=6, sticky="w")

        ttk.Button(left, text="1. Select board area", command=self._select_area).grid(
            row=1, column=0, columnspan=2, sticky="we", pady=(6, 2))
        ttk.Button(left, text="Auto-align", command=self._auto_align).grid(
            row=1, column=2, columnspan=2, sticky="we", pady=(6, 2))
        ttk.Button(left, text="Get templates", command=self._get_templates).grid(
            row=1, column=4, columnspan=2, sticky="we", pady=(6, 2))

        self.flip_var = tk.BooleanVar()
        ttk.Checkbutton(left, text="Board is flipped (playing Black)",
                        variable=self.flip_var, command=self._on_flip).grid(
            row=2, column=0, columnspan=3, sticky="w")
        ttk.Label(left, text="Theme:").grid(row=2, column=3, sticky="e")
        self.template_var = tk.StringVar()
        self.template_combo = ttk.Combobox(left, textvariable=self.template_var, width=10,
                                           state="readonly",
                                           values=["auto"] + sorted(self.piece_sets.keys()))
        self.template_combo.grid(row=2, column=4, columnspan=2, sticky="we")
        self.template_combo.bind("<<ComboboxSelected>>", self._on_template)

        nudge = ttk.LabelFrame(left, text="Nudge area (pixels)")
        nudge.grid(row=3, column=0, columnspan=6, sticky="we", pady=(6, 0))
        for i, (text, dx, dy) in enumerate([("← 1", -1, 0), ("→ 1", 1, 0), ("↑ 1", 0, -1), ("↓ 1", 0, 1),
                                            ("← 10", -10, 0), ("→ 10", 10, 0), ("↑ 10", 0, -10), ("↓ 10", 0, 10)]):
            ttk.Button(nudge, text=text, width=5,
                       command=lambda dx=dx, dy=dy: self._nudge(dx, dy)).grid(
                row=i // 4, column=i % 4, padx=2, pady=2)

        self.region_label = ttk.Label(left, text="Area: not set")
        self.region_label.grid(row=4, column=0, columnspan=6, sticky="w", pady=(4, 0))

        # ---- right: controls --------------------------------------------
        right = ttk.Frame(outer)
        right.grid(row=0, column=1, sticky="n", padx=(12, 0))

        self.start_btn = ttk.Button(right, text="Start watching", command=self._toggle_start)
        self.start_btn.grid(row=0, column=0, sticky="we", ipady=6)

        engine_lf = ttk.LabelFrame(right, text="Engine")
        engine_lf.grid(row=1, column=0, sticky="we", pady=(8, 0))
        self.engine_label = ttk.Label(engine_lf, text="Engine: not found")
        self.engine_label.grid(row=0, column=0, columnspan=2, sticky="w", padx=6, pady=(4, 2))
        ttk.Button(engine_lf, text="Download Stockfish", command=self._download_engine).grid(
            row=1, column=0, padx=6, pady=(0, 6))
        ttk.Button(engine_lf, text="Browse...", command=self._browse_engine).grid(
            row=1, column=1, padx=6, pady=(0, 6))

        game_lf = ttk.LabelFrame(right, text="Game")
        game_lf.grid(row=2, column=0, sticky="we", pady=(8, 0))
        ttk.Button(game_lf, text="New game (from start)", command=self._new_game).grid(
            row=0, column=0, padx=6, pady=(6, 2), sticky="we")
        ttk.Button(game_lf, text="Sync from screen", command=self._sync).grid(
            row=0, column=1, padx=6, pady=(6, 2), sticky="we")
        ttk.Button(game_lf, text="Analyze now", command=self._analyze_now).grid(
            row=0, column=2, padx=6, pady=(6, 2), sticky="we")
        self.mode_var = tk.StringVar()
        ttk.Radiobutton(game_lf, text="Track moves automatically (follow a game)",
                        variable=self.mode_var, value="track", command=self._on_mode).grid(
            row=1, column=0, columnspan=3, sticky="w", padx=6)
        ttk.Radiobutton(game_lf, text="Manual — analyse screen position as-is",
                        variable=self.mode_var, value="manual", command=self._on_mode).grid(
            row=2, column=0, columnspan=3, sticky="w", padx=6)
        self.turn_var = tk.StringVar()
        ttk.Label(game_lf, text="Side to move (Sync / New game / Manual):").grid(
            row=3, column=0, columnspan=3, sticky="w", padx=6, pady=(4, 0))
        ttk.Radiobutton(game_lf, text="White", variable=self.turn_var, value="white",
                        command=self._on_turn).grid(row=4, column=0, sticky="w", padx=6)
        ttk.Radiobutton(game_lf, text="Black", variable=self.turn_var, value="black",
                        command=self._on_turn).grid(row=4, column=1, sticky="w", padx=(0, 6))

        ana_lf = ttk.LabelFrame(right, text="Analysis")
        ana_lf.grid(row=3, column=0, sticky="we", pady=(8, 0))
        ttk.Label(ana_lf, text="Think time:").grid(row=0, column=0, sticky="w", padx=6, pady=(4, 0))
        self.movetime_var = tk.IntVar()
        self.movetime_scale = ttk.Scale(ana_lf, from_=200, to=3000, orient="horizontal",
                                        variable=self.movetime_var, command=self._on_movetime,
                                        length=180)
        self.movetime_scale.grid(row=0, column=1, padx=6, pady=(4, 0))
        self.movetime_label = ttk.Label(ana_lf, text="0.9s")
        self.movetime_label.grid(row=0, column=2, sticky="w")
        self.overlay_var = tk.BooleanVar()
        ttk.Checkbutton(ana_lf, text="Show arrow overlay on the board",
                        variable=self.overlay_var, command=self._on_overlay).grid(
            row=1, column=0, columnspan=3, sticky="w", padx=6)

        self.move_labels = []
        for i in range(3):
            lbl = ttk.Label(right, text="", font=("Consolas", 10))
            lbl.grid(row=4 + i, column=0, sticky="w", pady=(2 if i == 0 else 0, 0))
            self.move_labels.append(lbl)

        self.turn_label = ttk.Label(right, text="White to move", font=("Segoe UI", 10, "bold"))
        self.turn_label.grid(row=7, column=0, sticky="w", pady=(6, 0))
        self.history_label = ttk.Label(right, text="", font=("Consolas", 9), foreground="#555")
        self.history_label.grid(row=8, column=0, sticky="w")
        self.theme_label = ttk.Label(right, text="Board read: —", foreground="#555")
        self.theme_label.grid(row=9, column=0, sticky="w")
        self.status_label = ttk.Label(right, text="", wraplength=390, justify="left")
        self.status_label.grid(row=10, column=0, sticky="w", pady=(6, 0))
        fair = ttk.Label(right, text=FAIR_PLAY, wraplength=390, justify="left", foreground="#8a6d00")
        fair.grid(row=11, column=0, sticky="w", pady=(10, 0))

    # ------------------------------------------------------------- helpers

    def _sync_widgets(self):
        cfg = self.state.snapshot()
        self.flip_var.set(bool(cfg["flipped"]))
        self.template_var.set(cfg["template"] if cfg["template"] in ["auto"] + list(self.piece_sets)
                              else "auto")
        self.mode_var.set(cfg["mode"])
        self.turn_var.set(cfg["turn"])
        self.overlay_var.set(bool(cfg["overlay"]))
        self.movetime_var.set(int(cfg["movetime_ms"]))
        self.movetime_label.configure(text=f"{int(cfg['movetime_ms']) / 1000.0:.1f}s")
        self._refresh_start_btn()

    def _refresh_start_btn(self):
        cfg = self.state.snapshot()
        self.start_btn.configure(text="Pause watching" if not cfg["paused"] else "Start watching")

    def _set_status(self, text):
        self.status_label.configure(text=text)

    def _update_region_label(self):
        region = self.state.get("region")
        self.region_label.configure(
            text=f"Area: {region[2]}x{region[3]} at {region[0]},{region[1]}" if region
            else "Area: not set")

    def _save(self):
        settings.save(self.state.snapshot())

    def _push_cmd(self, name, arg=None):
        self.vision_worker.cmd_q.put((name, arg))

    # ------------------------------------------------------------- actions

    def _toggle_start(self):
        cfg = self.state.snapshot()
        if cfg["paused"]:
            if not cfg["region"]:
                self._set_status("Select the board area first (button 1).")
                return
            if not (cfg["engine"] or engines.find_engine()):
                self._set_status("No engine yet — click 'Download Stockfish' first.")
                return
            self.state.set(paused=False)
            if cfg["overlay"] and cfg["region"]:
                self.overlay.show(cfg["region"])
            self._push_cmd("invalidate")
            self._set_status("Watching the board ...")
        else:
            self.state.set(paused=True)
            self.overlay.hide()
            self._set_status("Paused.")
        self._save()
        self._refresh_start_btn()

    def _select_area(self):
        self.overlay.hide()
        RegionSelector(self.root, self._on_region_selected)

    def _on_region_selected(self, region):
        self.state.set(region=list(region))
        self._save()
        self._update_region_label()
        self._push_cmd("invalidate")
        self._set_status("Board area set. Auto-aligning ...")
        self._push_cmd("autocalign")

    def _auto_align(self):
        if not self.state.get("region"):
            self._set_status("Select the board area first.")
            return
        self._push_cmd("autocalign")

    def _apply_align(self, region, info):
        self.state.set(region=list(region))
        self._save()
        self._update_region_label()
        if self.overlay.visible:
            self.overlay.show(region)
        self._set_status("Auto-align done: " + info)

    def _nudge(self, dx, dy):
        region = self.state.get("region")
        if not region:
            return
        x, y, w, h = region
        region = [x + dx, y + dy, w, h]
        self.state.set(region=region)
        self._save()
        self._update_region_label()
        if self.overlay.visible:
            self.overlay.show(region)
        self._push_cmd("invalidate")

    def _on_flip(self):
        self.state.set(flipped=bool(self.flip_var.get()))
        self._save()
        self._push_cmd("invalidate")

    def _on_template(self, _event=None):
        self.state.set(template=self.template_var.get())
        self._save()
        self._push_cmd("invalidate")

    def _on_mode(self):
        self.state.set(mode=self.mode_var.get())
        self._save()
        self._push_cmd("invalidate")

    def _on_turn(self):
        self.state.set(turn=self.turn_var.get())
        self._save()
        if self.mode_var.get() == "manual":
            self._push_cmd("analyze_now")

    def _on_movetime(self, _value=None):
        ms = int(float(self.movetime_var.get()))
        self.movetime_label.configure(text=f"{ms / 1000.0:.1f}s")
        self.state.set(movetime_ms=ms)
        self._save()

    def _on_overlay(self):
        self.state.set(overlay=bool(self.overlay_var.get()))
        self._save()
        cfg = self.state.snapshot()
        if not cfg["overlay"]:
            self.overlay.hide()
        elif cfg["region"] and not cfg["paused"]:
            self.overlay.show(cfg["region"])

    def _new_game(self):
        self._push_cmd("new_game")
        self._set_status("Starting from the standard position ...")

    def _sync(self):
        self._push_cmd("sync")
        self._set_status("Syncing to the current screen position ...")

    def _analyze_now(self):
        self._push_cmd("analyze_now")

    def _get_templates(self):
        self._set_status("Downloading templates ...")

        def job():
            try:
                downloads.ensure_templates(lambda msg: self.ui_q.put(("status", msg)))
                sets = vision.load_piece_sets(settings.TEMPLATES_DIR)
                self.ui_q.put(("templates", sets))
                self.ui_q.put(("status", f"Templates ready: {', '.join(sets) or 'none'}"))
            except Exception as exc:
                self.ui_q.put(("status", f"Template download failed: {exc}"))

        threading.Thread(target=job, daemon=True).start()

    def _download_engine(self):
        self._set_status("Downloading Stockfish ...")

        def job():
            try:
                path = downloads.download_stockfish(
                    settings.ENGINES_DIR, lambda msg: self.ui_q.put(("status", msg)))
                self.ui_q.put(("engine_path", path))
            except Exception as exc:
                self.ui_q.put(("status", f"Stockfish download failed: {exc}"))

        threading.Thread(target=job, daemon=True).start()

    def _browse_engine(self):
        path = filedialog.askopenfilename(
            title="Select a UCI engine executable",
            filetypes=[("Executables", "*.exe"), ("All files", "*.*")])
        if path:
            self.state.set(engine=path)
            self._save()
            self._engine_label_refresh()
            self._set_status(f"Engine set: {path}")

    def _engine_label_refresh(self):
        path = self.state.get("engine") or engines.find_engine()
        self.engine_label.configure(
            text=f"Engine: {os.path.basename(path)}" if path else "Engine: not found")

    # --------------------------------------------------------- message pump

    def _poll(self):
        try:
            while True:
                msg = self.ui_q.get_nowait()
                self._handle_msg(msg)
        except queue.Empty:
            pass
        self.root.after(120, self._poll)

    def _handle_msg(self, msg):
        kind = msg[0]
        if kind == "status":
            self._set_status(msg[1])
        elif kind == "preview":
            self._show_preview(msg[1], msg[2])
        elif kind == "turn":
            self._show_turn(msg[1], msg[2])
        elif kind == "analysis":
            self._show_analysis(msg[1], msg[2], msg[3])
        elif kind == "align":
            self._apply_align(msg[1], msg[2])
        elif kind == "engine":
            self._engine_label_refresh()
        elif kind == "engine_path":
            self.state.set(engine=msg[1])
            self._save()
            self._engine_label_refresh()
            self._set_status(f"Stockfish ready: {msg[1]}")
        elif kind == "templates":
            self._reload_templates(msg[1])
        elif kind == "desync":
            pass  # status message already posted

    def _reload_templates(self, sets):
        self.piece_sets = sets
        self.vision_worker.piece_sets = sets
        self.template_combo.configure(values=["auto"] + sorted(sets.keys()))
        self._push_cmd("invalidate")

    def _show_preview(self, pil_img, res):
        im = pil_img.copy()
        im.thumbnail((380, 380), Image.LANCZOS)
        self.preview_photo = ImageTk.PhotoImage(im)
        canvas = self.preview
        canvas.delete("all")
        canvas.create_image(2, 2, anchor="nw", image=self.preview_photo)
        dw, dh = im.size
        side = min(dw, dh)
        ox, oy = (dw - side) / 2.0, (dh - side) / 2.0
        cell = side / 8.0
        for i in range(9):
            x = 2 + ox + i * cell
            y = 2 + oy + i * cell
            canvas.create_line(x, 2 + oy, x, 2 + oy + side, fill="#33cc33")
            canvas.create_line(2 + ox, y, 2 + ox + side, y, fill="#33cc33")
        if res:
            codes = res["codes"]
            fnt = max(8, int(cell * 0.45))
            for row in range(8):
                for col in range(8):
                    code = codes[row][col]
                    if not code:
                        continue
                    cx = 2 + ox + (col + 0.5) * cell
                    cy = 2 + oy + (row + 0.5) * cell
                    letter = code[1].upper() if code[0] == "w" else code[1].lower()
                    color = "#ffffff" if code[0] == "w" else "#000000"
                    shadow = "#000000" if code[0] == "w" else "#ffffff"
                    for ddx, ddy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                        canvas.create_text(cx + ddx, cy + ddy, text=letter, fill=shadow,
                                           font=("Consolas", fnt, "bold"))
                    canvas.create_text(cx, cy, text=letter, fill=color,
                                       font=("Consolas", fnt, "bold"))
            warn = "  (poor fit — check alignment/theme)" if res["mean_err"] > 22 else ""
            self.theme_label.configure(
                text=f"Board read: {res['set']} · fit {res['mean_err']:.1f}{warn}")

    def _show_turn(self, board: chess.Board, history: str):
        self.turn_label.configure(
            text=("White to move" if board.turn == chess.WHITE else "Black to move"))
        self.history_label.configure(text=history or "")

    def _show_analysis(self, board: chess.Board, meta: str, results: list):
        self.last_board = board
        if not results:
            self._set_status(f"Analysis returned no result ({meta}).")
            return
        lines = []
        for i, res in enumerate(results[:3]):
            san = res.get("san") or res["san_pv"].split(" ")[0]
            lines.append(f"{i + 1}. {san:<6} {engines.format_score(res['score']):>6}   {res['san_pv']}")
        for i, lbl in enumerate(self.move_labels):
            lbl.configure(text=lines[i] if i < len(lines) else "")
        san1 = results[0].get("san") or results[0]["san_pv"].split(" ")[0]
        score1 = engines.format_score(results[0]["score"])
        self._set_status(f"Best: {san1} ({score1})  ·  {meta}")
        cfg = self.state.snapshot()
        if cfg["overlay"] and cfg["region"] and not cfg["paused"]:
            self.overlay.draw_move(cfg["region"], cfg["flipped"], results[0]["move"],
                                   f"{san1}  {score1}")

    # ------------------------------------------------------------- shutdown

    def _on_close(self):
        self.vision_worker.stop()
        self.analysis_worker.stop()
        self.overlay.destroy()
        self._save()
        try:
            self.root.destroy()
        except Exception:
            pass


class RegionSelector(tk.Toplevel):
    """Full (virtual) screen dimmer; drag a rectangle around the board."""

    def __init__(self, master: tk.Misc, on_done):
        super().__init__(master)
        self.on_done = on_done
        self.attributes("-topmost", True)
        try:
            self.attributes("-alpha", 0.45)
        except tk.TclError:
            pass
        self.overrideredirect(True)
        vx, vy = self.winfo_vrootx(), self.winfo_vrooty()
        vw, vh = self.winfo_vrootwidth(), self.winfo_vrootheight()
        self.geometry(f"{vw}x{vh}+{vx}+{vy}")
        self.canvas = tk.Canvas(self, bg="#111111", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_text(vw // 2, 40,
                                text="Drag a rectangle around the chess board   ·   Esc to cancel",
                                fill="#ffffff", font=("Segoe UI", 16, "bold"))
        self._hint = self.canvas.create_text(vw // 2, 74, text="",
                                             fill="#cfcfcf", font=("Segoe UI", 11))
        self._rect = None
        self._p0 = None
        self._g0 = None
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.bind("<Escape>", lambda _e: self.destroy())
        self.after(60, self._grab_focus)

    def _grab_focus(self):
        try:
            self.focus_force()
            self.grab_set()
        except tk.TclError:
            pass

    def _press(self, ev):
        self._p0 = (ev.x, ev.y)
        self._g0 = (ev.x_root, ev.y_root)
        if self._rect:
            self.canvas.delete(self._rect)
        self._rect = self.canvas.create_rectangle(ev.x, ev.y, ev.x, ev.y,
                                                  outline="#00ff66", width=2)

    def _drag(self, ev):
        if not self._p0 or not self._rect:
            return
        self.canvas.coords(self._rect, self._p0[0], self._p0[1], ev.x, ev.y)
        w = abs(ev.x - self._p0[0])
        h = abs(ev.y - self._p0[1])
        self.canvas.itemconfigure(self._hint, text=f"{w} x {h} px")

    def _release(self, ev):
        if not self._g0:
            return
        x1, y1 = ev.x_root, ev.y_root
        x0, y0 = self._g0
        x, y = min(x0, x1), min(y0, y1)
        w, h = abs(x1 - x0), abs(y1 - y0)
        if w >= 200 and h >= 150:
            self.on_done([x, y, w, h])
            self.destroy()
        else:
            self.canvas.itemconfigure(self._hint,
                                      text="Too small — drag a bigger rectangle (min 200 x 150)")
