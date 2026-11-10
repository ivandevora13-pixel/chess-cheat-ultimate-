"""Download piece templates and Stockfish.

Templates:
  cburnett - the default lichess set (fetched as PNG renders from Wikimedia
             Commons), also matches many other sites/apps.
  neo      - the default chess.com set (fetched from chess.com's static CDN).

Engine:
  latest official Stockfish Windows x86-64 build from GitHub releases.

CLI:  python downloads.py [templates|stockfish|all]
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
import zipfile

import settings

USER_AGENT = "ChessScreenAssistant/1.0 (personal non-commercial tool; contact: local user)"
TIMEOUT = 60


def http_get(url: str, timeout: int = TIMEOUT, progress=None, retries: int = 4) -> bytes:
    last_exc = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                total = resp.headers.get("Content-Length")
                total = int(total) if total else None
                chunks = []
                got = 0
                while True:
                    chunk = resp.read(262144)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    got += len(chunk)
                    if progress and total:
                        progress(got, total)
                return b"".join(chunks)
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code in (429, 500, 502, 503, 504):
                try:
                    delay = float(exc.headers.get("Retry-After") or 0)
                except Exception:
                    delay = 0.0
                if delay <= 0:
                    delay = 1.5 * (2 ** attempt)
                time.sleep(min(delay, 30.0))
                continue
            raise
        except Exception as exc:
            last_exc = exc
            time.sleep(1.0 + attempt)
    raise last_exc if last_exc else RuntimeError("request failed: " + url)


def _is_png(data: bytes) -> bool:
    return data[:8] == b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------------------
# piece templates

_CBURNETT_FILES = {
    "wK": "Chess_klt45.svg", "wQ": "Chess_qlt45.svg", "wR": "Chess_rlt45.svg",
    "wB": "Chess_blt45.svg", "wN": "Chess_nlt45.svg", "wP": "Chess_plt45.svg",
    "bK": "Chess_kdt45.svg", "bQ": "Chess_qdt45.svg", "bR": "Chess_rdt45.svg",
    "bB": "Chess_bdt45.svg", "bN": "Chess_ndt45.svg", "bP": "Chess_pdt45.svg",
}

_NEO_PIECES = {
    "wK": "wk", "wQ": "wq", "wR": "wr", "wB": "wb", "wN": "wn", "wP": "wp",
    "bK": "bk", "bQ": "bq", "bR": "br", "bB": "bb", "bN": "bn", "bP": "bp",
}


def download_cburnett(dest_dir: str, status=print) -> None:
    os.makedirs(dest_dir, exist_ok=True)
    for code, fname in _CBURNETT_FILES.items():
        dest = os.path.join(dest_dir, code + ".png")
        if os.path.isfile(dest):
            continue  # resume: keep files already downloaded
        api = ("https://commons.wikimedia.org/w/api.php?action=query&format=json"
               "&prop=imageinfo&iiprop=url&iiurlwidth=256&titles=File:" + fname)
        data = json.loads(http_get(api).decode("utf-8"))
        pages = data["query"]["pages"]
        page = next(iter(pages.values()))
        thumb = page["imageinfo"][0]["thumburl"]
        png = http_get(thumb)
        if not _is_png(png):
            raise RuntimeError(f"not a PNG: {thumb}")
        with open(dest, "wb") as f:
            f.write(png)
        status(f"  {code}.png")
        time.sleep(0.6)  # be polite to Wikimedia


def download_neo(dest_dir: str, status=print) -> None:
    os.makedirs(dest_dir, exist_ok=True)
    patterns = [
        "https://www.chess.com/chess-themes/pieces/neo/150/{p}.png",
        "https://images.chesscomfiles.com/chess-themes/pieces/neo/150/{p}.png",
    ]
    base = None
    for pat in patterns:
        try:
            test = http_get(pat.format(p="wp"), timeout=20)
            if _is_png(test):
                base = pat
                break
        except Exception:
            continue
    if base is None:
        raise RuntimeError("chess.com neo piece URL is not reachable")
    for code, piece in _NEO_PIECES.items():
        dest = os.path.join(dest_dir, code + ".png")
        if os.path.isfile(dest):
            continue  # resume
        data = http_get(base.format(p=piece), timeout=30)
        if not _is_png(data):
            raise RuntimeError("not a PNG from " + base)
        with open(dest, "wb") as f:
            f.write(data)
        status(f"  {code}.png")


def templates_present(name: str) -> bool:
    import vision
    folder = os.path.join(settings.TEMPLATES_DIR, name)
    return all(os.path.isfile(os.path.join(folder, c + ".png")) for c in vision.PIECE_CODES)


def ensure_templates(status=print) -> None:
    if not templates_present("cburnett"):
        status("Downloading cburnett piece templates (lichess/standard)...")
        download_cburnett(os.path.join(settings.TEMPLATES_DIR, "cburnett"), status)
    if not templates_present("neo"):
        status("Downloading chess.com neo piece templates...")
        download_neo(os.path.join(settings.TEMPLATES_DIR, "neo"), status)


# ---------------------------------------------------------------------------
# stockfish

def download_stockfish(dest_dir: str = settings.ENGINES_DIR, status=print) -> str:
    os.makedirs(dest_dir, exist_ok=True)
    meta = json.loads(http_get(
        "https://api.github.com/repos/official-stockfish/Stockfish/releases/latest",
        timeout=30).decode("utf-8"))
    assets = {a["name"]: a["browser_download_url"] for a in meta.get("assets", [])}

    preferred = [
        "stockfish-windows-x86-64-avx2.zip",
        "stockfish-windows-x86-64.zip",
        "stockfish-windows-x86-64-sse41-popcnt.zip",
    ]
    key = url = None
    for want in preferred:
        for name, cand in assets.items():
            if name.lower() == want:
                key, url = name, cand
                break
        if url:
            break
    if url is None:
        for name, cand in assets.items():
            low = name.lower()
            if "windows" in low and "x86-64" in low and low.endswith(".zip"):
                key, url = name, cand
                break
    if url is None:
        raise RuntimeError("no Windows x86-64 build found in the latest Stockfish release")

    status(f"Downloading {key} ...")
    def prog(got, total):
        status(f"Downloading {key} ... {100 * got // max(1, total)}%")
    data = http_get(url, timeout=900, progress=prog)

    exe_path = os.path.join(dest_dir, "stockfish.exe")
    if key.lower().endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            exes = [n for n in z.namelist() if n.lower().endswith(".exe")]
            if not exes:
                raise RuntimeError("no .exe inside " + key)
            exes.sort(key=lambda n: (0 if "avx2" in n.lower() else 1, len(n)))
            with z.open(exes[0]) as src, open(exe_path, "wb") as dst:
                dst.write(src.read())
    else:
        with open(exe_path, "wb") as f:
            f.write(data)
    status("Stockfish installed: " + exe_path)
    return exe_path


# ---------------------------------------------------------------------------
# CLI

def main(argv):
    what = argv[1] if len(argv) > 1 else "all"
    if what in ("templates", "all"):
        ensure_templates()
    if what in ("stockfish", "all"):
        download_stockfish()


if __name__ == "__main__":
    main(sys.argv)
