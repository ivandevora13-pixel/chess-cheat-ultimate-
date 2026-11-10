# Chess Screen Assistant

A Windows desktop app that watches a chess board on your screen (chess.com,
lichess, streams, other apps), recognizes the position, and shows Stockfish's
best move — as a list in the control panel and as a click-through arrow
overlay drawn directly on the board.

> **Fair play notice**: use this for study, analysis, practicing, and watching
> games. Using engine assistance in online rated games violates the rules of
> chess.com / lichess and gets accounts banned.

---

## Quick start

1. Double-click **`run.bat`** (creates a virtual environment and installs
   dependencies on first run, then starts the app).
2. In the app:
   - **1. Select board area** — the screen dims; drag a rectangle around the
     chess board (it doesn't need to be pixel-perfect; auto-align runs
     automatically afterwards).
   - If no engine is detected, click **Download Stockfish** once.
   - Click **Start watching**.
   - In **Game**, pick **New game (from start)** for a fresh game, or
     **Sync from screen** + the correct *side to move* when you join a game
     already in progress (or to analyse a stream / any static position).
3. Check the preview: the grid and the recognized piece letters should match
   the real board. The **fit** number under the preview should be low
   (roughly below 15; "poor fit" warns above 22).

The best move, evaluation (White's point of view) and the top-3 lines appear
in the panel; the arrow overlay appears on the board when it's watching.

## Controls

| Control | What it does |
| --- | --- |
| Select board area | Re-drag the region around the board. |
| Auto-align | Snaps the region to the visible board edges and re-fits the grid (takes a few seconds). Runs automatically after each selection. |
| Nudge arrows | Shift the region by 1 or 10 pixels when something is slightly off. |
| Board is flipped | Enable when the board is viewed from Black's side (playing as Black / black at the bottom). |
| Theme | Piece template set: `auto` (recommended), `cburnett` (lichess + many sites/apps), `neo` (chess.com default), or any custom set you add (below). |
| Get templates | (Re-)downloads the bundled template sets. |
| Track moves automatically | The app follows a real game, flipping the side to move itself and detecting moves (including castling) by matching the screen against legal moves. |
| Manual | Treats every screen position as an isolated position; you set the side to move yourself with the White/Black radio. |
| New game / Sync from screen / Analyze now | Start tracking from move 1 / re-sync to the current screen (after a missed move or joining mid-game) / force re-analysis. |
| Think time | How long Stockfish searches per position (0.2–3.0 s). |
| Show arrow overlay | Toggles the on-board best-move arrow. |

Space bar on the main window toggles watching on/off.

## Custom themes (any site / app)

The bundled sets cover lichess (cburnett) and chess.com (neo). For any other
piece art:

1. Create a folder `templates/<yourname>/`.
2. Put 12 PNGs in it, named exactly: `wK wQ wR wB wN wP bK bQ bR bB bN bP`
   plus `.png` — one sprite per piece, each cropped tightly to its square.
   Transparent PNGs are ideal; a crop taken from your own screenshot of that
   theme also works.
3. Restart the app; the set appears in the Theme dropdown.

## Troubleshooting

- **Fit score high / wrong letters** — click Auto-align, or nudge the region;
  make sure the whole board (all 64 squares) is inside the region and not
  covered by other windows. Wrong-looking letters for a correct region
  usually mean the wrong theme: try the other set explicitly.
- **"A move was not recognized — auto-resynced"** — a piece was misread
  (animation mid-capture, unusual position). The app keeps going; castling
  rights may have been lost, click **Sync from screen** if you care about
  perfect engine context in a castling situation.
- **Arrow in the wrong place** — press *Board is flipped* if you play Black;
  then Auto-align/nudge again if needed.
- **No best move after Sync** — check the "side to move" radio matches whose
  turn it is on screen.
- **Engine errors** — click Browse... and pick any UCI engine exe
  (Stockfish strongly recommended).

## How it works

`mss` captures the region → `vision.py` splits it into 64 squares, estimates
each square's background colour locally (so last-move/check highlights don't
break recognition), and classifies each square by alpha-composited template
matching against the piece sprites, with a small per-square alignment search.
`tracker.py` keeps a python-chess board in sync by matching every detected
change against the list of legal moves (this is what makes turn order,
castling rights and en passant correct). `engines.py` drives Stockfish via
UCI with MultiPV=3, and `overlay.py` draws a click-through, always-on-top
arrow on the board.

## Files

```
run.bat            launcher (setup + start)
main.py            entry point (sets DPI awareness, starts the UI)
ui.py              control panel, region selector, overlay handling
vision.py          board recognition (template matching, auto-align)
worker.py          capture/recognition thread + engine thread
tracker.py         live game tracking via legal-move matching
engines.py         UCI engine wrapper + discovery
overlay.py         click-through arrow overlay
downloads.py       template + Stockfish downloads (also a CLI:
                   python downloads.py [templates|stockfish|all])
settings.py        config persistence (config.json)
selftest.py        offline test suite (python selftest.py)
templates/         piece sprite sets
engines/           stockfish.exe (downloaded)
```
