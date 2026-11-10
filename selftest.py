"""Self test: template download, board recognition, tracking and engine.

Renders synthetic boards from the piece templates themselves (same sprites the
matcher uses), which validates the whole vision pipeline end to end:
grid split, background estimation, template matching, empty detection,
flipped orientation, misaligned crops, highlight tints, move tracking, engine.

Run:  python selftest.py
"""
from __future__ import annotations

import os
import sys

import numpy as np
from PIL import Image, ImageDraw

import chess

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import downloads  # noqa: E402
import engines  # noqa: E402
import settings  # noqa: E402
import vision  # noqa: E402
from tracker import Tracker  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = ""):
    global PASS, FAIL
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f"  — {detail}" if detail else ""))
    if ok:
        PASS += 1
    else:
        FAIL += 1


def ensure_templates():
    have = all(downloads.templates_present(n) for n in ("cburnett", "neo"))
    if not have:
        print("Templates missing, downloading ...")
        downloads.ensure_templates()
    for name in ("cburnett", "neo"):
        check(f"templates/{name} present", downloads.templates_present(name))


def render_board(pset: vision.PieceSet, board: chess.Board, sq: int = 72,
                 light=(240, 217, 181), dark=(181, 136, 99),
                 flip: bool = False, tint_squares=None, tint=(255, 255, 0), tint_a=0.30):
    side = sq * 8
    img = Image.new("RGB", (side, side))
    draw = ImageDraw.Draw(img)
    tint_squares = tint_squares or set()
    for row in range(8):
        for col in range(8):
            square = vision.rc_to_square(row, col, flip)
            base = light if (row + col) % 2 == 0 else dark
            if square in tint_squares:
                base = tuple(int(c * (1 - tint_a) + t * tint_a) for c, t in zip(base, tint))
            draw.rectangle([col * sq, row * sq, (col + 1) * sq, (row + 1) * sq], fill=base)
    for square in chess.SQUARES:
        piece = board.piece_at(square)
        if piece is None:
            continue
        row, col = vision.square_to_rc(square, flip)
        code = vision.CODE_FROM_SYMBOL[piece.symbol()]
        sprite = pset.raw[code].resize((sq, sq), Image.LANCZOS)
        img.paste(sprite, (col * sq, row * sq), sprite)
    return np.asarray(img.convert("RGB"))


def expected_codes(board: chess.Board, flip: bool):
    grid = [[""] * 8 for _ in range(8)]
    for square in chess.SQUARES:
        piece = board.piece_at(square)
        if piece is None:
            continue
        row, col = vision.square_to_rc(square, flip)
        grid[row][col] = vision.CODE_FROM_SYMBOL[piece.symbol()]
    return grid


def compare(codes, expected):
    holes = []
    for row in range(8):
        for col in range(8):
            if codes[row][col] != expected[row][col]:
                holes.append((chess.square_name(vision.rc_to_square(row, col, False)),
                              expected[row][col] or "empty", codes[row][col] or "empty"))
    return holes


def test_recognition(piece_sets):
    start = chess.Board()
    for set_name, sq, light, dark in (
        ("cburnett", 72, (240, 217, 181), (181, 136, 99)),
        ("neo", 96, (238, 238, 210), (118, 150, 86)),
    ):
        img = render_board(piece_sets[set_name], start, sq, light, dark)
        res = vision.classify(img, False, piece_sets, set_name)
        holes = compare(res["codes"], expected_codes(start, False))
        check(f"start position / {set_name} ({sq}px squares)", len(holes) <= 1,
              f"{64 - len(holes)}/64 correct" + (f", errors: {holes}" if holes else ""))

    # auto set selection
    img = render_board(piece_sets["cburnett"], start, 64, (240, 217, 181), (181, 136, 99))
    res = vision.classify(img, False, piece_sets, "auto")
    check("auto theme picks cburnett on a cburnett board", res["set"] == "cburnett",
          f"picked {res['set']}")
    img = render_board(piece_sets["neo"], start, 90, (238, 238, 210), (118, 150, 86))
    res = vision.classify(img, False, piece_sets, "auto")
    check("auto theme picks neo on a neo board", res["set"] == "neo", f"picked {res['set']}")

    # mid-game, flipped, with highlight tints on two squares
    board = chess.Board()
    for uci in ("e2e4", "e7e5", "g1f3", "b8c6", "f1b5", "a7a6", "b5a4", "g8f6", "e1g1"):
        board.push_uci(uci)
    img = render_board(piece_sets["cburnett"], board, 72, (240, 217, 181), (181, 136, 99),
                       flip=True, tint_squares={chess.C1, chess.G1})
    res = vision.classify(img, True, piece_sets, "cburnett")
    holes = compare(res["codes"], expected_codes(board, True))
    check("midgame + flipped + highlighted squares", len(holes) <= 1,
          f"{64 - len(holes)}/64 correct" + (f", errors: {holes}" if holes else ""))

    # user's region a few px off (modest drag error, no auto-align yet)
    img = render_board(piece_sets["cburnett"], start, 72, (240, 217, 181), (181, 136, 99))
    big = np.full((700, 700, 3), 70, dtype=np.uint8)
    big[20:596, 18:594] = img  # board sits inside the assumed region, off by (3, 5)
    crop = np.ascontiguousarray(big[15:15 + 582, 15:15 + 582])
    res = vision.classify(crop, False, piece_sets, "cburnett")
    holes = compare(res["codes"], expected_codes(start, False))
    check("modest region error (6 px too large, shifted) recovery", len(holes) <= 2,
          f"{64 - len(holes)}/64 correct" + (f", errors: {holes}" if holes else ""))
    # a badly oversized region is recovered by auto-align (verified separately)

    # auto-align must find a deliberate 9 px shift
    img = render_board(piece_sets["cburnett"], start, 72, (240, 217, 181), (181, 136, 99))
    pad = 40
    canvas = np.zeros((72 * 8 + 2 * pad, 72 * 8 + 2 * pad, 3), dtype=np.uint8)
    canvas[:] = (60, 60, 60)
    # board drawn 9 px lower and 7 px left than the user's assumed region
    canvas[pad + 9: pad + 9 + 576, pad - 7: pad - 7 + 576] = img
    result = vision.auto_align(canvas, 576, 576, pad, False, piece_sets, "cburnett")
    check("auto-align finds a full-fit region", result is not None and result[4] < 1.0,
          f"result: {result[4] if result else None} (base {result[5] if result else None})")
    if result:
        ax, ay, aw, ah, _best, _base = result
        aligned = np.ascontiguousarray(canvas[ay:ay + ah, ax:ax + aw])
        res = vision.classify(aligned, False, piece_sets, "cburnett")
        holes = compare(res["codes"], expected_codes(start, False))
        check("aligned region reads the whole position", len(holes) == 0,
              f"{64 - len(holes)}/64 correct, region {aw}x{ah}" + (f", errors: {holes}" if holes else ""))

    # auto-align must also fix a region a bit too small (610 px on a 576 board)
    small_region = (pad + 20, pad + 20, 610, 610)
    canvas2 = np.full((700, 700, 3), 70, dtype=np.uint8)
    canvas2[62:62 + 576, 62:62 + 576] = img
    result = vision.auto_align(canvas2, 610, 610, 20, False, piece_sets, "cburnett")
    ok = result is not None and abs(result[2] - 576) <= 4 and abs(result[3] - 576) <= 4 and result[4] < 1.0
    check("auto-align corrects a too-large region size", ok,
          f"region {result[2]}x{result[3]} fit {result[4]:.1f}" if result else "no result")


def test_tracker():
    tracker = Tracker()
    board = tracker.new_game().copy()
    check("tracker starts at standard position", tracker.board.fen().startswith(chess.STARTING_FEN.split(" ")[0]))

    moves = ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5"]
    ok_all = True
    for uci in moves:
        board.push_uci(uci)
        detected = chess.Board(None)
        for square, piece in board.piece_map().items():
            detected.set_piece_at(square, piece)
        status, san = tracker.try_update(detected)
        if status != "ok":
            ok_all = False
            break
    check("tracker follows a real game including turns", ok_all,
          "history: " + " ".join(tracker.san_history))
    check("tracker side to move correct after 5 plies", tracker.board.turn == chess.BLACK)

    # desync on garbage position
    garbage = chess.Board(None)
    for square, piece in board.piece_map().items():
        garbage.set_piece_at(square, piece)
    garbage.remove_piece_at(chess.A1)
    garbage.set_piece_at(chess.D4, chess.Piece.from_symbol("Q"))
    status, _ = tracker.try_update(garbage)
    check("tracker reports desync for unrecognized change", status == "desync")

    # sync from screen keeps playing
    tracker.sync_from_screen(garbage, chess.WHITE)
    check("sync_from_screen rebuilds position", tracker.board.piece_map() == garbage.piece_map())
    check("sync_from_screen resets castling rights", tracker.board.castling_rights == 0)


def test_engine():
    path = engines.find_engine()
    if not path:
        check("engine available (skipped — no stockfish yet)", True,
              "run: python downloads.py stockfish")
        return
    analyzer = engines.Analyzer(path)
    try:
        results = analyzer.analyse(chess.Board(), movetime_ms=600)
        check("engine analyses the start position", len(results) >= 1 and len(results[0]["san"]) >= 2,
              f"best: {results[0]['san']} ({engines.format_score(results[0]['score'])})")
        mid = chess.Board()
        mid.push_uci("e2e4")
        mid.push_uci("e7e5")
        results = analyzer.analyse(mid, movetime_ms=600)
        check("engine analyses a simple opening line", len(results) >= 1,
              f"best: {results[0]['san_pv']} ({engines.format_score(results[0]['score'])})")
    finally:
        analyzer.close()


def main() -> int:
    print("== templates ==")
    ensure_templates()
    piece_sets = vision.load_piece_sets(settings.TEMPLATES_DIR)
    if not piece_sets:
        print("no piece sets loaded — aborting")
        return 1

    print("== recognition ==")
    test_recognition(piece_sets)

    print("== tracking ==")
    test_tracker()

    print("== engine ==")
    test_engine()

    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
