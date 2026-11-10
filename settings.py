"""Config persistence and shared runtime state."""
from __future__ import annotations

import json
import os
import threading

APP_NAME = "Chess Screen Assistant"
APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
TEMPLATES_DIR = os.path.join(APP_DIR, "templates")
ENGINES_DIR = os.path.join(APP_DIR, "engines")

DEFAULTS = {
    "region": None,        # [x, y, w, h] physical screen pixels
    "flipped": False,      # True when the board is viewed from Black's side
    "template": "auto",    # piece template set name or "auto"
    "movetime_ms": 900,
    "mode": "track",       # "track" (follow a game) or "manual" (analyse as-is)
    "turn": "white",       # side to move, used by Sync / New game / manual mode
    "overlay": True,
    "engine": None,        # explicit engine exe path
    "paused": True,
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        for key in DEFAULTS:
            if key in data:
                cfg[key] = data[key]
    except Exception:
        pass
    return cfg


def save(cfg: dict) -> None:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({k: cfg.get(k, DEFAULTS[k]) for k in DEFAULTS}, f, indent=2)
    except Exception:
        pass


class State:
    """Thread-safe snapshot/set wrapper around the config dict."""

    def __init__(self, cfg: dict):
        self._cfg = dict(cfg)
        self._lock = threading.Lock()

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._cfg)

    def get(self, key, default=None):
        with self._lock:
            return self._cfg.get(key, default)

    def set(self, **kwargs) -> dict:
        with self._lock:
            self._cfg.update(kwargs)
            return dict(self._cfg)
