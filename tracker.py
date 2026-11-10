"""Live game tracking.

Keeps a python-chess board in sync with the screen by matching every detected
position change against the list of legal moves. That way side-to-move,
castling rights and en-passant are all handled correctly, and a mismatch is
reported as "desync" instead of silently analysing a wrong position.
"""
from __future__ import annotations

import chess


class Tracker:
    def __init__(self):
        self.board: chess.Board | None = None
        self.san_history: list[str] = []
        self.last_error: str | None = None

    # -- setup --------------------------------------------------------------

    def new_game(self, turn: int = chess.WHITE) -> chess.Board:
        self.board = chess.Board()
        if turn == chess.BLACK:
            self.board.turn = chess.BLACK
        self.san_history = []
        self.last_error = None
        return self.board

    def sync_from_screen(self, detected: chess.Board, turn: int = chess.WHITE) -> chess.Board:
        """Rebuild from an arbitrary detected position (mid-game, stream, ...)."""
        start = chess.Board()
        if detected.piece_map() == start.piece_map():
            board = start
            board.turn = turn
        else:
            board = detected.copy(stack=False)
            board.turn = turn
            board.castling_rights = 0
            board.ep_square = None
            board.clear_stack()
        self.board = board
        self.san_history = []
        self.last_error = None
        return self.board

    # -- updates ------------------------------------------------------------

    def try_update(self, detected: chess.Board):
        """Match a detected position change against legal moves.

        Returns (status, san) with status in {"idle", "same", "ok", "desync"}.
        """
        if self.board is None:
            return "idle", None
        if self.board.piece_map() == detected.piece_map():
            return "same", None
        for move in list(self.board.legal_moves):
            san = self.board.san(move)
            self.board.push(move)
            if self.board.piece_map() == detected.piece_map():
                self.san_history.append(san)
                self.last_error = None
                return "ok", san
            self.board.pop()
        self.last_error = "unrecognized position change"
        return "desync", None
