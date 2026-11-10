"""Chess board recognition from a screen capture.

The user selects the board region. We split it into an 8x8 grid and classify
each square by template matching against a "piece set" (12 PNG sprites with
alpha; cburnett for lichess/others, neo for chess.com).

Each square's background colour is estimated independently from a thin border
ring, which keeps recognition robust when the site highlights squares
(last move / selected / check tints).

All RGB, no OpenCV: Pillow + numpy only.
"""
from __future__ import annotations

import os

import numpy as np
from PIL import Image

import chess

PIECE_CODES = ["wK", "wQ", "wR", "wB", "wN", "wP", "bK", "bQ", "bR", "bB", "bN", "bP"]
SYMBOL = {code: sym for code, sym in zip(PIECE_CODES, "KQRBNPkqrbnp")}
CODE_FROM_SYMBOL = {sym: code for code, sym in SYMBOL.items()}

DEFAULT_PARAMS = {
    "inset": 0.06,        # fraction cropped off each square edge before matching
    "offset_frac": 0.05,  # offset step as a fraction of the square
    "offset_steps": 3,    # per-square search reaches +/- offset_frac * offset_steps
    "empty_margin": 4.0,  # mean-abs-error a piece match must beat "empty" by
    "refine_top": 3,      # how many candidate templates get offset-refined
    "sample_step": 1,     # classify every N-th square (used for fast auto-align)
}


class PieceSet:
    """A folder of 12 piece PNGs named wK.png ... bP.png."""

    def __init__(self, name: str, folder: str):
        self.name = name
        self.raw = {}
        for code in PIECE_CODES:
            path = os.path.join(folder, code + ".png")
            img = Image.open(path).convert("RGBA")
            self.raw[code] = img
        self._scaled = {}

    def scaled(self, size_wh):
        """Return (codes, premultiplied RGB stack, 1-alpha stack) for a square
        (or near-square) size. Sprites are scaled to the FULL square size; the
        matcher then compares the centre crop of the composite.

        premul = rgb * alpha, so a composite over background b is
        premul + (1 - alpha) * b.
        """
        key = tuple(size_wh)
        cached = self._scaled.get(key)
        if cached is not None:
            return cached
        prem, inva = [], []
        for code in PIECE_CODES:
            img = self.raw[code].resize(key, Image.LANCZOS)
            arr = np.asarray(img, dtype=np.float32)  # 0..255
            alpha = arr[..., 3:4] / 255.0
            # premultiplied RGB in 0..255 units, matching the captured image
            prem.append(arr[..., :3] * alpha)
            inva.append(1.0 - alpha)
        result = (list(PIECE_CODES), np.stack(prem), np.stack(inva))
        self._scaled[key] = result
        return result


def load_piece_sets(templates_dir: str) -> dict:
    sets = {}
    if not os.path.isdir(templates_dir):
        return sets
    for name in sorted(os.listdir(templates_dir)):
        folder = os.path.join(templates_dir, name)
        if not os.path.isdir(folder):
            continue
        try:
            sets[name] = PieceSet(name, folder)
        except Exception:
            continue  # incomplete/invalid folder, skip
    return sets


# ---------------------------------------------------------------------------
# classification


def _classify_square(padded: np.ndarray, cx: int, cy: int, Sx: int, Sy: int,
                     pset: PieceSet, p: dict):
    """Classify one square taken from `padded` with its top-left at (cx, cy).

    `padded` extends `off` pixels beyond the square on each side (where
    available), which is what allows the small per-square alignment search.
    Returns (code_or_empty, error, margin).
    """
    sq = padded[cy:cy + Sy, cx:cx + Sx]
    ring = max(2, min(Sx, Sy) // 15)
    bg = np.median(
        np.concatenate([
            sq[:ring, :, :].reshape(-1, 3),
            sq[-ring:, :, :].reshape(-1, 3),
            sq[:, :ring, :].reshape(-1, 3),
            sq[:, -ring:, :].reshape(-1, 3),
        ], axis=0),
        axis=0,
    ).astype(np.float32)

    ix = max(1, round(Sx * p["inset"]))
    iy = max(1, round(Sy * p["inset"]))
    cw, ch = Sx - 2 * ix, Sy - 2 * iy
    if cw < 8 or ch < 8:
        return "", float("inf"), 0.0
    center = sq[iy:iy + ch, ix:ix + cw]

    d_empty = float(np.abs(center - bg).mean())

    codes, prem, inva = pset.scaled((Sx, Sy))
    dists = np.empty(len(codes), dtype=np.float32)
    comps = []
    for i in range(len(codes)):
        comp = prem[i] + inva[i] * bg
        comp_crop = np.ascontiguousarray(comp[iy:iy + ch, ix:ix + cw])
        comps.append(comp_crop)
        dists[i] = np.abs(comp_crop - center).mean()

    order = np.argsort(dists)
    off = max(1, round(min(Sx, Sy) * p["offset_frac"]))
    steps = max(1, int(p.get("offset_steps", 3)))
    offs = sorted({k * off for k in range(-steps, steps + 1)})
    ph, pw = padded.shape[:2]
    best_idx = -1
    best_d = float("inf")
    for idx in order[: max(1, int(p["refine_top"]))]:
        d = float(dists[idx])
        comp_crop = comps[idx]
        for dy in offs:
            wy = cy + dy + iy
            if wy < 0 or wy + ch > ph:
                continue
            for dx in offs:
                wx = cx + dx + ix
                if wx < 0 or wx + cw > pw:
                    continue
                if dy == 0 and dx == 0:
                    continue
                wnd = padded[wy:wy + ch, wx:wx + cw]
                dd = float(np.abs(comp_crop - wnd).mean())
                if dd < d:
                    d = dd
        if d < best_d:
            best_d = d
            best_idx = idx

    if best_idx >= 0 and best_d < d_empty - p["empty_margin"]:
        return codes[best_idx], best_d, d_empty - best_d
    return "", d_empty, (best_d - d_empty) if best_idx >= 0 else 0.0


def _read_board(img_rgb: np.ndarray, flip: bool, pset: PieceSet, p: dict, sample_step: int = 1) -> dict:
    img = img_rgb.astype(np.float32)
    h, w = img.shape[:2]
    side = min(h, w)
    ox = (w - side) // 2
    oy = (h - side) // 2
    bounds = [round(i * side / 8) for i in range(9)]

    codes = [[""] * 8 for _ in range(8)]
    errs = np.full((8, 8), np.nan)
    margins = np.zeros((8, 8))
    n = 0
    total = 0.0

    off = max(1, round(side / 8 * p["offset_frac"]))
    for row in range(0, 8, sample_step):
        for col in range(0, 8, sample_step):
            x0 = ox + bounds[col]
            x1 = ox + bounds[col + 1]
            y0 = oy + bounds[row]
            y1 = oy + bounds[row + 1]
            Sx, Sy = x1 - x0, y1 - y0
            if min(Sx, Sy) < 12:
                continue
            px0 = max(0, x0 - off)
            py0 = max(0, y0 - off)
            px1 = min(w, x1 + off)
            py1 = min(h, y1 + off)
            padded = img[py0:py1, px0:px1]
            code, err, margin = _classify_square(padded, x0 - px0, y0 - py0, Sx, Sy, pset, p)
            codes[row][col] = code
            errs[row][col] = err
            margins[row][col] = margin
            n += 1
            total += err

    return {
        "codes": codes,
        "errs": errs,
        "margins": margins,
        "mean_err": total / max(1, n),
        "n": n,
    }


def classify(img_rgb: np.ndarray, flip: bool, piece_sets: dict, choose: str = "auto", params: dict | None = None) -> dict:
    """Read the whole board. Returns dict with codes/errs/margins/mean_err/set."""
    p = dict(DEFAULT_PARAMS)
    p.update(params or {})
    names = list(piece_sets.keys())
    if not names:
        raise RuntimeError("no piece templates available (templates/ folder is empty)")
    to_try = names if (choose == "auto" or choose not in piece_sets) else [choose]
    best = None
    for name in to_try:
        res = _read_board(img_rgb, flip, piece_sets[name], p, p["sample_step"])
        res["set"] = name
        if best is None or res["mean_err"] < best["mean_err"]:
            best = res
    return best


# ---------------------------------------------------------------------------
# auto-alignment


def detect_board_edges(img_rgb: np.ndarray, rw: int, rh: int, mx: int):
    """Locate the board boundary using strong, board-wide gradient lines.

    The page background differs from the board's outer squares on chess.com,
    lichess and most sites, so the board's left/right/top/bottom edges produce
    the strongest column/row gradient peaks. Returns (x, y, w, h) in
    expanded-image coordinates or None.
    """
    g = img_rgb.astype(np.float32).mean(axis=2)
    H, W = g.shape
    prof_x = np.abs(np.diff(g, axis=1)).mean(axis=0)  # boundary between cols i, i+1
    prof_y = np.abs(np.diff(g, axis=0)).mean(axis=1)
    if len(prof_x) < 32 or len(prof_y) < 32:
        return None

    def peaks(profile):
        half = len(profile) // 2
        i1 = int(np.argmax(profile[:half]))
        i2 = half + int(np.argmax(profile[half:]))
        med = float(np.median(profile))
        strong = max(1.5 * med, 5.0)
        if float(profile[i1]) < strong or float(profile[i2]) < strong:
            return None
        return i1, i2

    px = peaks(prof_x)
    py = peaks(prof_y)
    if px is None and py is None:
        return None
    x0, x1 = (px[0] + 1, px[1] + 1) if px is not None else (mx, mx + rw)
    y0, y1 = (py[0] + 1, py[1] + 1) if py is not None else (mx, mx + rh)
    w, h = x1 - x0, y1 - y0
    if not (0.8 * rw <= w <= 1.25 * rw and 0.8 * rh <= h <= 1.25 * rh):
        return None
    return x0, y0, w, h


def auto_align(img_rgb: np.ndarray, rw: int, rh: int, mx: int, flip: bool,
               piece_sets: dict, choose: str = "auto"):
    """Find the best board region inside an expanded capture.

    Tries: the user's region (translated), the region snapped to the board's
    visible boundaries, and slightly resized variants, then a short polish.
    Returns (x, y, w, h, best_score, base_score) in expanded-image
    coordinates, or None on failure.
    """
    names = list(piece_sets.keys())
    if not names:
        return None
    to_try = names if (choose == "auto" or choose not in piece_sets) else [choose]
    p = dict(DEFAULT_PARAMS, sample_step=2, offset_steps=2)  # keep auto-align fast

    def score_at(x, y, w, h):
        if x < 0 or y < 0 or x + w > img_rgb.shape[1] or y + h > img_rgb.shape[0]:
            return None
        sub = img_rgb[y:y + h, x:x + w]
        best = None
        for name in to_try:
            res = _read_board(sub, flip, piece_sets[name], p, 2)
            if best is None or res["mean_err"] < best:
                best = res["mean_err"]
        return best

    coarse = max(2, mx // 4)
    scored = []

    # 1) translate the user's region
    for dx in range(-mx, mx + 1, coarse):
        for dy in range(-mx, mx + 1, coarse):
            s = score_at(mx + dx, mx + dy, rw, rh)
            if s is not None:
                scored.append((s, mx + dx, mx + dy, rw, rh))

    # 2) snap to the board's visible boundaries (fixes size errors too)
    edge = detect_board_edges(img_rgb, rw, rh, mx)
    if edge is not None:
        s = score_at(*edge)
        if s is not None:
            scored.append((s, *edge))

    # 3) slightly resized variants around the user's region
    for delta in (-16, -8, 8, 16):
        w2, h2 = rw + delta, rh + delta
        if w2 < 64 or h2 < 64:
            continue
        x2 = mx - delta // 2
        y2 = mx - delta // 2
        for ddx in (-coarse, 0, coarse):
            for ddy in (-coarse, 0, coarse):
                s = score_at(x2 + ddx, y2 + ddy, w2, h2)
                if s is not None:
                    scored.append((s, x2 + ddx, y2 + ddy, w2, h2))

    if not scored:
        return None
    scored.sort(key=lambda t: t[0])
    best = scored[0]

    # 4) one polish pass: small translation + size adjustments
    step = max(2, coarse // 2)
    bx, by, bw, bh = best[1], best[2], best[3], best[4]
    for dx in (-step, 0, step):
        for dy in (-step, 0, step):
            for dw in (-2 * step, 0, 2 * step):
                w2, h2 = bw + dw, bh + dw
                if w2 < 64 or h2 < 64:
                    continue
                s = score_at(bx + dx, by + dy, w2, h2)
                if s is not None and s < best[0] - 1e-6:
                    best = (s, bx + dx, by + dy, w2, h2)

    base = score_at(mx, mx, rw, rh)
    return best[1], best[2], best[3], best[4], best[0], base


# ---------------------------------------------------------------------------
# coordinate mapping and board construction


def rc_to_square(row: int, col: int, flip: bool) -> int:
    """Screen grid cell (row 0 = top) -> python-chess square."""
    if not flip:
        return chess.square(col, 7 - row)
    return chess.square(7 - col, row)


def square_to_rc(square: int, flip: bool):
    file = chess.square_file(square)
    rank = chess.square_rank(square)
    if not flip:
        return 7 - rank, file
    return rank, 7 - file


def codes_to_board(codes, flip: bool) -> chess.Board:
    board = chess.Board(None)
    for row in range(8):
        for col in range(8):
            code = codes[row][col]
            if not code:
                continue
            board.set_piece_at(rc_to_square(row, col, flip), chess.Piece.from_symbol(SYMBOL[code]))
    return board


def board_is_sane(board: chess.Board) -> bool:
    return (len(board.pieces(chess.KING, chess.WHITE)) == 1
            and len(board.pieces(chess.KING, chess.BLACK)) == 1)
