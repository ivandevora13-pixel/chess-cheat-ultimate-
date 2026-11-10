"""Background threads: screen reading/tracking and engine analysis."""
from __future__ import annotations

import os
import queue
import threading
import time

import numpy as np
from PIL import Image

import chess

import capture
import engines
import vision
from tracker import Tracker


class VisionWorker(threading.Thread):
    """Captures the region, recognises the board, tracks the game."""

    def __init__(self, state, ui_q: queue.Queue, analysis_q: queue.Queue, piece_sets: dict):
        super().__init__(daemon=True, name="vision")
        self.state = state
        self.ui_q = ui_q
        self.analysis_q = analysis_q
        self.piece_sets = piece_sets
        self.cmd_q: queue.Queue = queue.Queue()
        self._stop = threading.Event()

        self.cap = capture.ScreenCapture()
        self.tracker = Tracker()
        self.last_small = None
        self.cand_key = None
        self.accept_key = None
        self.last_detected = None  # chess.Board, pieces only

    # -- lifecycle ----------------------------------------------------------

    def stop(self):
        self._stop.set()

    def post(self, *msg):
        self.ui_q.put(msg)

    # -- main loop ----------------------------------------------------------

    def run(self):
        while not self._stop.is_set():
            self._drain_cmds()
            cfg = self.state.snapshot()
            region = cfg["region"]
            if not region or cfg["paused"]:
                time.sleep(0.15)
                continue

            try:
                img = self.cap.grab_rgb(region)
            except Exception as exc:
                self.post("status", f"Screen capture failed: {exc}")
                time.sleep(0.8)
                continue

            small = img[::6, ::6].astype(np.int16)
            same = (self.last_small is not None
                    and self.last_small.shape == small.shape
                    and float(np.abs(small - self.last_small).mean()) < 1.0)
            self.last_small = small

            if same and self.cand_key == self.accept_key:
                time.sleep(0.12)
                continue  # nothing on screen changed, skip recognition

            try:
                res = vision.classify(img, cfg["flipped"], self.piece_sets, cfg["template"])
            except Exception as exc:
                self.post("status", f"Recognition failed: {exc}")
                time.sleep(0.5)
                continue

            self.post("preview", self._make_thumb(img), res)

            key = "".join(res["codes"][r][c] or "." for r in range(8) for c in range(8))
            if key == self.cand_key:
                if key != self.accept_key:
                    self.accept_key = key
                    self._on_placement(res, cfg)
            else:
                self.cand_key = key
            time.sleep(0.12 if same else 0.25)

    @staticmethod
    def _make_thumb(img: np.ndarray) -> Image.Image:
        im = Image.fromarray(img)
        im.thumbnail((380, 380), Image.LANCZOS)
        return im

    # -- game state ---------------------------------------------------------

    def _on_placement(self, res: dict, cfg: dict):
        detected = vision.codes_to_board(res["codes"], cfg["flipped"])
        if not vision.board_is_sane(detected):
            self.post("status", "Recognized position looks incomplete (missing a king). "
                                "Check the selected area or the template set.")
            return
        self.last_detected = detected

        if cfg["mode"] == "manual":
            board = self._board_from_detected(detected, cfg)
            self.post("turn", board, "")
            self._queue_analysis(board, "manual")
            return

        if self.tracker.board is None:
            self.post("status", "Board detected — click 'New game' (fresh game) "
                                "or 'Sync from screen' to start.")
            return

        status, san = self.tracker.try_update(detected)
        if status == "same":
            return
        if status == "ok":
            board = self.tracker.board.copy()
            self.post("turn", board, self._history_str())
            self._queue_analysis(board, f"{san} played")
        else:
            old_turn = self.tracker.board.turn
            self.tracker.sync_from_screen(detected, turn=not old_turn)
            board = self.tracker.board.copy()
            self.post("desync", True)
            self.post("turn", board, "")
            self.post("status", "A move was not recognized — auto-resynced. Castling rights "
                                "may be lost; use 'Sync from screen' if this repeats.")
            self._queue_analysis(board, "auto-resync")

    def _board_from_detected(self, detected: chess.Board, cfg: dict) -> chess.Board:
        turn = chess.WHITE if cfg["turn"] == "white" else chess.BLACK
        start = chess.Board()
        if detected.piece_map() == start.piece_map():
            board = start
            board.turn = turn
            return board
        board = detected.copy(stack=False)
        board.turn = turn
        board.castling_rights = 0
        board.ep_square = None
        board.clear_stack()
        return board

    def _history_str(self) -> str:
        h = self.tracker.san_history
        parts = []
        for i in range(0, len(h), 2):
            n = i // 2 + 1
            if i + 1 < len(h):
                parts.append(f"{n}.{h[i]} {h[i + 1]}")
            else:
                parts.append(f"{n}.{h[i]}")
        return " ".join(parts[-6:])

    def _queue_analysis(self, board: chess.Board, meta: str):
        # keep only the latest request
        try:
            while True:
                self.analysis_q.get_nowait()
        except queue.Empty:
            pass
        self.analysis_q.put((board.copy(), meta))

    # -- commands -----------------------------------------------------------

    def _drain_cmds(self):
        while True:
            try:
                cmd = self.cmd_q.get_nowait()
            except queue.Empty:
                return
            try:
                self._handle_cmd(*cmd)
            except Exception as exc:
                self.post("status", f"Command failed: {exc}")

    def _handle_cmd(self, name: str, arg=None):
        cfg = self.state.snapshot()
        if name == "invalidate":
            self.last_small = None
            self.cand_key = None
        elif name == "new_game":
            board = self.tracker.new_game(_turn_of(cfg))
            self.post("turn", board.copy(), "")
            self.post("status", "Tracking started from the standard position.")
            self._queue_analysis(board.copy(), "new game")
        elif name == "sync":
            if self.last_detected is None:
                self.post("status", "Nothing recognized yet — select the board area first.")
                return
            board = self.tracker.sync_from_screen(self.last_detected, _turn_of(cfg))
            self.post("turn", board.copy(), "")
            self.post("status", "Synced to the current on-screen position.")
            self._queue_analysis(board.copy(), "synced")
        elif name == "analyze_now":
            board = None
            if cfg["mode"] == "track" and self.tracker.board is not None:
                board = self.tracker.board.copy()
            elif self.last_detected is not None:
                board = self._board_from_detected(self.last_detected, cfg)
            if board is None:
                self.post("status", "Nothing to analyze yet — select the board area first.")
                return
            self._queue_analysis(board, "manual request")
        elif name == "autocalign":
            self._cmd_autocalign(cfg)

    def _cmd_autocalign(self, cfg: dict):
        region = cfg["region"]
        if not region:
            self.post("status", "Select the board area first.")
            return
        if not self.piece_sets:
            self.post("status", "No piece templates installed — click 'Get templates'.")
            return
        x, y, w, h = region
        side = min(w, h)
        mx = max(12, min(int(side * 0.12) + 6, 60))
        expanded = [x - mx, y - mx, w + 2 * mx, h + 2 * mx]
        self.post("status", "Auto-aligning ... (takes a few seconds)")
        try:
            img = self.cap.grab_rgb(expanded)
        except Exception as exc:
            self.post("status", f"Auto-align failed (capture error: {exc}).")
            return
        result = vision.auto_align(img, w, h, mx, cfg["flipped"],
                                   self.piece_sets, cfg["template"])
        if result is None:
            self.post("status", "Auto-align failed.")
            return
        ax, ay, aw, ah, best, base = result
        improved = base is None or best < base - 1e-6
        if improved:
            new_region = [x - mx + ax, y - mx + ay, aw, ah]
        else:
            new_region = list(region)
        note = f"fit {best:.1f}"
        if improved:
            note += f", region moved/resized to {aw}x{ah} at {new_region[0]},{new_region[1]}"
        else:
            note += " (no change needed)"
        self.post("align", new_region, note)
        self.last_small = None
        self.cand_key = None


def _turn_of(cfg: dict) -> int:
    return chess.WHITE if cfg["turn"] == "white" else chess.BLACK


class AnalysisWorker(threading.Thread):
    """Runs the engine on the latest requested position."""

    def __init__(self, state, ui_q: queue.Queue, analysis_q: queue.Queue):
        super().__init__(daemon=True, name="analysis")
        self.state = state
        self.ui_q = ui_q
        self.analysis_q = analysis_q
        self.multi_q = analysis_q  # alias for clarity
        self._stop = threading.Event()
        self.analyzer: engines.Analyzer | None = None
        self._engine_missing_posted = False

    def stop(self):
        self._stop.set()

    def run(self):
        while not self._stop.is_set():
            try:
                board, meta = self.analysis_q.get(timeout=0.2)
            except queue.Empty:
                continue
            # drain: only analyse the newest request
            try:
                while True:
                    board, meta = self.analysis_q.get_nowait()
            except queue.Empty:
                pass

            cfg = self.state.snapshot()
            path = cfg["engine"] or engines.find_engine()
            if not path:
                if not self._engine_missing_posted:
                    self._engine_missing_posted = True
                    self.ui_q.put(("status", "No chess engine found — click 'Download Stockfish' "
                                             "or 'Browse...' to pick an engine exe."))
                continue
            self._engine_missing_posted = False

            try:
                if (self.analyzer is None
                        or os.path.normcase(self.analyzer.path) != os.path.normcase(path)):
                    if self.analyzer is not None:
                        self.analyzer.close()
                    self.ui_q.put(("status", "Starting engine ..."))
                    self.analyzer = engines.Analyzer(path)
                    self.ui_q.put(("engine", path))
                results = self.analyzer.analyse(board, cfg["movetime_ms"])
                self.ui_q.put(("analysis", board, meta, results))
            except Exception as exc:
                if self.analyzer is not None:
                    self.analyzer.close()
                self.analyzer = None
                self.ui_q.put(("status", f"Engine error: {exc}"))
