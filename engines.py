"""UCI engine discovery and analysis wrapper (Stockfish by default)."""
from __future__ import annotations

import glob
import os
import shutil
import sys

import chess
import chess.engine


def find_engine(preferred: str | None = None) -> str | None:
    if preferred and os.path.isfile(preferred):
        return preferred
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in sorted(glob.glob(os.path.join(here, "engines", "*.exe"))):
        if os.path.isfile(cand):
            return cand
    which = shutil.which("stockfish")
    if which:
        return which
    if sys.platform == "win32":
        for cand in (r"C:\Program Files\Stockfish\stockfish.exe",):
            if os.path.isfile(cand):
                return cand
    return None


class Analyzer:
    def __init__(self, path: str):
        self.path = path
        self.engine = chess.engine.SimpleEngine.popen_uci(path, timeout=30)
        try:
            threads = max(1, (os.cpu_count() or 4) // 2)
            self.engine.configure({"Threads": threads, "Hash": 256})
        except Exception:
            pass

    def analyse(self, board: chess.Board, movetime_ms: int, multipv: int = 3) -> list[dict]:
        limit = chess.engine.Limit(time=max(0.05, movetime_ms / 1000.0))
        infos = self.engine.analyse(board, limit, multipv=multipv)
        if isinstance(infos, dict):
            infos = [infos]
        out = []
        for info in infos:
            pv = info.get("pv")
            score = info.get("score")
            if not pv or score is None:
                continue
            pv = list(pv)
            try:
                san_pv = board.variation_san(pv[:6])
            except Exception:
                san_pv = pv[0].uci()
            try:
                san = board.san(pv[0])
            except Exception:
                san = pv[0].uci()
            out.append({
                "move": pv[0],
                "san": san,
                "score": score.pov(chess.WHITE),
                "san_pv": san_pv,
                "pv": pv,
                "depth": info.get("depth"),
            })
        return out

    def close(self):
        try:
            self.engine.quit()
        except Exception:
            pass


def format_score(score) -> str:
    """Format a python-chess Score (White POV) as text."""
    if score is None:
        return "?"
    if score.is_mate():
        m = score.mate()
        if m is None:
            return "?"
        return f"M{m}" if m > 0 else f"-M{abs(m)}"
    cp = score.score()
    if cp is None:
        return "?"
    return f"{cp / 100.0:+.2f}"
