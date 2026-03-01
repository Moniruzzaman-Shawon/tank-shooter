#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        TANK SHOOTER — Terminal Edition                      ║
║                                                                            ║
║  A professional, object-oriented tank combat game rendered entirely in the  ║
║  terminal using a frame-buffer approach with ANSI color support.           ║
║                                                                            ║
║  Controls:  W/A/S/D — Move    SPACE — Fire    Q — Quit                     ║
║                                                                            ║
║  Architecture:                                                             ║
║    • FrameBuffer  — 2D character grid rendered once per tick (no flicker)  ║
║    • Vector2      — Lightweight 2D coordinate / direction helper           ║
║    • Tank         — Player and AI tank entities with health & cooldowns    ║
║    • Projectile   — Fast-moving shells with owner tracking                 ║
║    • Map          — Obstacle layout, boundary walls, and spawn points      ║
║    • HUD          — Heads-up display (health bar, score, wave info)        ║
║    • Leaderboard  — Persistent high-score tracking across sessions         ║
║    • Game         — Main loop, input handling, physics, and orchestration  ║
║                                                                            ║
║  Author : Moniruzzaman Shawon                                              ║
║  License: MIT                                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import copy
import json
import os
import random
import sys
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# Platform-specific non-blocking keyboard input
# ─────────────────────────────────────────────────────────────────────────────
# On Unix/macOS we use termios + tty to put stdin into raw/non-blocking mode.
# On Windows we fall back to msvcrt.  This lets the game loop keep running
# even when no key is pressed.
# ─────────────────────────────────────────────────────────────────────────────

if os.name == "nt":
    import msvcrt

    def _setup_terminal() -> None:
        """Windows: no terminal setup needed — msvcrt handles raw input."""
        pass

    def _restore_terminal() -> None:
        """Windows: no teardown needed."""
        pass

    def _get_key() -> Optional[str]:
        """Return a single key press or None if nothing is buffered."""
        if msvcrt.kbhit():
            ch = msvcrt.getch()
            return ch.decode("utf-8", errors="ignore")
        return None
else:
    import select
    import termios
    import tty

    _original_terminal_settings = None

    def _setup_terminal() -> None:
        """Put the terminal into raw mode so we can read single key presses."""
        global _original_terminal_settings
        _original_terminal_settings = termios.tcgetattr(sys.stdin)
        tty.setcbreak(sys.stdin.fileno())

    def _restore_terminal() -> None:
        """Restore the terminal to its original cooked mode on exit."""
        if _original_terminal_settings is not None:
            termios.tcsetattr(
                sys.stdin, termios.TCSADRAIN, _original_terminal_settings
            )

    def _get_key() -> Optional[str]:
        """Non-blocking single-character read from stdin."""
        if select.select([sys.stdin], [], [], 0.0)[0]:
            return sys.stdin.read(1)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# ANSI Escape Code Helpers
# ─────────────────────────────────────────────────────────────────────────────
# We use raw ANSI sequences so there is zero dependency on third-party libs.
# Every colored string is book-ended with a RESET so colours never leak.
# ─────────────────────────────────────────────────────────────────────────────

class Color:
    """ANSI 256-color and attribute escape sequences."""

    RESET       = "\033[0m"
    BOLD        = "\033[1m"
    DIM         = "\033[2m"

    # Foreground
    BLACK       = "\033[30m"
    RED         = "\033[91m"
    GREEN       = "\033[92m"
    YELLOW      = "\033[93m"
    BLUE        = "\033[94m"
    MAGENTA     = "\033[95m"
    CYAN        = "\033[96m"
    WHITE       = "\033[97m"
    GRAY        = "\033[90m"

    # Background
    BG_RED      = "\033[41m"
    BG_GREEN    = "\033[42m"
    BG_YELLOW   = "\033[43m"
    BG_BLUE     = "\033[44m"
    BG_GRAY     = "\033[100m"
    BG_BLACK    = "\033[40m"

    @staticmethod
    def colorize(text: str, *codes: str) -> str:
        """Wrap *text* with the given ANSI codes and append a RESET."""
        return "".join(codes) + text + Color.RESET


# ─────────────────────────────────────────────────────────────────────────────
# Direction Enum
# ─────────────────────────────────────────────────────────────────────────────

class Direction(Enum):
    """Cardinal movement directions with associated (dy, dx) deltas.

    The coordinate system is row-major: y increases downward, x increases
    rightward — standard for terminal grids.
    """
    UP    = (-1,  0)
    DOWN  = ( 1,  0)
    LEFT  = ( 0, -1)
    RIGHT = ( 0,  1)

    @property
    def dy(self) -> int:
        return self.value[0]

    @property
    def dx(self) -> int:
        return self.value[1]


# Map from WASD keys to Direction enums for quick lookup during input.
KEY_TO_DIRECTION: dict[str, Direction] = {
    "w": Direction.UP,
    "s": Direction.DOWN,
    "a": Direction.LEFT,
    "d": Direction.RIGHT,
}


# ─────────────────────────────────────────────────────────────────────────────
# FrameBuffer — the "graphics engine"
# ─────────────────────────────────────────────────────────────────────────────

class FrameBuffer:
    """A 2D character grid that is composed off-screen then flushed at once.

    This eliminates the flicker you'd get from printing individual characters.
    Each cell stores a (character, color_prefix) tuple so colour information
    stays coupled with the glyph it decorates.

    Rendering pipeline each tick:
        1. clear()          — fill every cell with the background character
        2. draw_*() calls   — stamp entities onto the buffer
        3. render()         — convert the buffer to a single string and print
    """

    # The default background fill character
    BG_CHAR = " "

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        # Each cell: (display_char, ansi_prefix)
        self._buffer: list[list[tuple[str, str]]] = []
        self.clear()

    # -- buffer operations ---------------------------------------------------

    def clear(self) -> None:
        """Reset every cell to the default background."""
        self._buffer = [
            [(self.BG_CHAR, "")] * self.width for _ in range(self.height)
        ]

    def put(self, y: int, x: int, char: str, color: str = "") -> None:
        """Write a single character into the buffer if within bounds."""
        if 0 <= y < self.height and 0 <= x < self.width:
            self._buffer[y][x] = (char, color)

    def put_string(self, y: int, x: int, text: str, color: str = "") -> None:
        """Write a horizontal string starting at (y, x)."""
        for i, ch in enumerate(text):
            self.put(y, x + i, ch, color)

    # -- rendering -----------------------------------------------------------

    def render(self) -> str:
        """Flatten the 2D buffer into a single string ready for printing.

        Moves the cursor to the top-left corner (ANSI home) before writing
        so the frame overwrites the previous one in-place.
        """
        lines: list[str] = []
        for row in self._buffer:
            parts: list[str] = []
            for char, color_prefix in row:
                if color_prefix:
                    parts.append(f"{color_prefix}{char}{Color.RESET}")
                else:
                    parts.append(char)
            lines.append("".join(parts))
        # \033[H moves cursor to row 1, col 1 (home position)
        return "\033[H" + "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Map — terrain, walls, and obstacle layout
# ─────────────────────────────────────────────────────────────────────────────

# Visual legend:
#   ░  — empty floor          ▓ or # — destructible wall
#   █ — indestructible border  ≈ — water (impassable)

class CellType(Enum):
    """Types of terrain that can occupy a map cell."""
    EMPTY       = auto()
    WALL        = auto()   # destructible
    BORDER      = auto()   # indestructible boundary
    WATER       = auto()   # impassable decoration


class Map:
    """Holds the static terrain grid and provides spatial queries.

    The map is surrounded by an indestructible BORDER.  Interior obstacles
    are a mix of destructible WALLs and WATER hazards arranged to create
    interesting combat arenas.

    Attributes:
        width, height : outer dimensions including the border
        grid          : 2D list of CellType values
    """

    # Appearance mapping: CellType → (character, ANSI colour)
    CELL_VISUALS: dict[CellType, tuple[str, str]] = {
        CellType.EMPTY:  ("·", Color.GRAY + Color.DIM),
        CellType.WALL:   ("▓", Color.YELLOW + Color.BOLD),
        CellType.BORDER: ("█", Color.WHITE + Color.DIM),
        CellType.WATER:  ("≈", Color.CYAN),
    }

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.grid: list[list[CellType]] = [
            [CellType.EMPTY] * width for _ in range(height)
        ]
        self._build_border()
        self._place_obstacles()

    # -- construction --------------------------------------------------------

    def _build_border(self) -> None:
        """Ring the map with indestructible BORDER cells."""
        for x in range(self.width):
            self.grid[0][x] = CellType.BORDER
            self.grid[self.height - 1][x] = CellType.BORDER
        for y in range(self.height):
            self.grid[y][0] = CellType.BORDER
            self.grid[y][self.width - 1] = CellType.BORDER

    def _place_obstacles(self) -> None:
        """Procedurally scatter walls and water inside the arena.

        Obstacles are placed in clusters to form cover points rather than
        random noise.  A simple strategy: place rectangular blocks at
        pseudo-random positions, keeping a safe zone around the spawn
        corners.
        """
        random.seed()  # truly random each run

        # Helper: carve a filled rectangle of a given cell type
        def _rect(cy: int, cx: int, h: int, w: int, cell: CellType) -> None:
            for dy in range(h):
                for dx in range(w):
                    ry, rx = cy + dy, cx + dx
                    if 2 <= ry < self.height - 2 and 2 <= rx < self.width - 2:
                        self.grid[ry][rx] = cell

        # Spawn safe-zones: top-left quadrant and bottom-right quadrant
        safe_tl = set()
        safe_br = set()
        for dy in range(5):
            for dx in range(7):
                safe_tl.add((1 + dy, 1 + dx))
                safe_br.add((self.height - 2 - dy, self.width - 2 - dx))

        # Place 6–10 wall clusters
        for _ in range(random.randint(6, 10)):
            cy = random.randint(3, self.height - 5)
            cx = random.randint(3, self.width - 7)
            bh = random.randint(1, 3)
            bw = random.randint(2, 5)
            # Skip if overlapping a safe zone
            if (cy, cx) in safe_tl or (cy, cx) in safe_br:
                continue
            _rect(cy, cx, bh, bw, CellType.WALL)

        # Place 2–4 water features
        for _ in range(random.randint(2, 4)):
            cy = random.randint(4, self.height - 6)
            cx = random.randint(4, self.width - 8)
            bh = random.randint(1, 2)
            bw = random.randint(3, 6)
            if (cy, cx) in safe_tl or (cy, cx) in safe_br:
                continue
            _rect(cy, cx, bh, bw, CellType.WATER)

    # -- queries -------------------------------------------------------------

    def is_passable(self, y: int, x: int) -> bool:
        """Return True if a tank or projectile can move into (y, x)."""
        if not (0 <= y < self.height and 0 <= x < self.width):
            return False
        return self.grid[y][x] == CellType.EMPTY

    def is_destructible(self, y: int, x: int) -> bool:
        """Return True if the cell at (y, x) is a destructible wall."""
        if not (0 <= y < self.height and 0 <= x < self.width):
            return False
        return self.grid[y][x] == CellType.WALL

    def destroy_cell(self, y: int, x: int) -> None:
        """Replace a destructible wall with empty floor."""
        if self.is_destructible(y, x):
            self.grid[y][x] = CellType.EMPTY

    def draw(self, fb: FrameBuffer) -> None:
        """Stamp the entire terrain grid onto the frame buffer."""
        for y in range(self.height):
            for x in range(self.width):
                char, color = self.CELL_VISUALS[self.grid[y][x]]
                fb.put(y, x, char, color)

    def get_spawn_point(self, region: str = "top_left") -> tuple[int, int]:
        """Return an empty cell in the requested region for spawning."""
        if region == "top_left":
            candidates = [
                (y, x)
                for y in range(2, 6)
                for x in range(2, 8)
                if self.grid[y][x] == CellType.EMPTY
            ]
        elif region == "bottom_right":
            candidates = [
                (y, x)
                for y in range(self.height - 6, self.height - 2)
                for x in range(self.width - 8, self.width - 2)
                if self.grid[y][x] == CellType.EMPTY
            ]
        else:
            # Random interior position
            candidates = [
                (y, x)
                for y in range(2, self.height - 2)
                for x in range(2, self.width - 2)
                if self.grid[y][x] == CellType.EMPTY
            ]
        return random.choice(candidates) if candidates else (2, 2)


# ─────────────────────────────────────────────────────────────────────────────
# Projectile — fast-moving shell fired by tanks
# ─────────────────────────────────────────────────────────────────────────────

class Projectile:
    """A single shell travelling in a cardinal direction.

    Projectiles move one cell per tick (effectively instant compared to
    tank speed).  On collision with a wall or tank they are marked dead
    and removed on the next sweep.

    Attributes:
        y, x      : current grid position
        direction : travel direction (Direction enum)
        owner_id  : which tank fired this (to prevent self-damage)
        alive     : set to False when the shell impacts something
        damage    : hit-point cost inflicted on a struck tank
    """

    # Visual per direction — keeps the shell oriented correctly
    DIR_CHARS: dict[Direction, str] = {
        Direction.UP:    "│",
        Direction.DOWN:  "│",
        Direction.LEFT:  "─",
        Direction.RIGHT: "─",
    }

    def __init__(
        self,
        y: int,
        x: int,
        direction: Direction,
        owner_id: int,
        damage: int = 25,
    ) -> None:
        self.y = y
        self.x = x
        self.direction = direction
        self.owner_id = owner_id
        self.alive = True
        self.damage = damage
        # Shells move multiple cells per game tick for speed
        self.speed = 2

    def advance(self, game_map: Map) -> None:
        """Move the projectile forward by *speed* cells, checking each step."""
        for _ in range(self.speed):
            if not self.alive:
                break
            ny = self.y + self.direction.dy
            nx = self.x + self.direction.dx

            # Destroy destructible walls on impact
            if game_map.is_destructible(ny, nx):
                game_map.destroy_cell(ny, nx)
                self.alive = False
                return

            # Stopped by any impassable terrain
            if not game_map.is_passable(ny, nx):
                self.alive = False
                return

            self.y, self.x = ny, nx

    def draw(self, fb: FrameBuffer) -> None:
        """Render the shell with a bright red/orange colour."""
        if self.alive:
            char = self.DIR_CHARS.get(self.direction, "*")
            fb.put(self.y, self.x, char, Color.RED + Color.BOLD)


# ─────────────────────────────────────────────────────────────────────────────
# Explosion — brief particle effect on tank death
# ─────────────────────────────────────────────────────────────────────────────

class Explosion:
    """A short-lived visual effect at a (y, x) position.

    Cycles through animation frames before marking itself as finished.
    """

    FRAMES = ["✸", "✦", "•", " "]

    def __init__(self, y: int, x: int) -> None:
        self.y = y
        self.x = x
        self.tick = 0
        self.alive = True

    def update(self) -> None:
        self.tick += 1
        if self.tick >= len(self.FRAMES):
            self.alive = False

    def draw(self, fb: FrameBuffer) -> None:
        if self.alive:
            char = self.FRAMES[self.tick]
            fb.put(self.y, self.x, char, Color.YELLOW + Color.BOLD)


# ─────────────────────────────────────────────────────────────────────────────
# Tank — player and AI entities
# ─────────────────────────────────────────────────────────────────────────────

class Tank:
    """A tank entity with position, health, fire cooldown, and rendering.

    Both the player and AI enemies share this class.  The AI-specific
    decision logic lives in a separate method (``ai_update``) that is
    only called for non-player tanks.

    Tank visual (3-wide depending on facing direction):
        UP:    ▲        DOWN:  ▼        LEFT:  ◄[O]    RIGHT: [O]►
               [O]             [O]

    For simplicity we render the tank as a single cell with a directional
    character — this avoids multi-cell collision complexity while still
    looking distinct.

    Attributes:
        id         : unique integer identifier
        y, x       : grid position
        facing     : current Direction the turret points
        hp         : remaining hit points (0 = destroyed)
        max_hp     : starting / cap hit points
        fire_cd    : ticks remaining until the tank can fire again
        fire_rate  : ticks between consecutive shots
        is_player  : True for the human-controlled tank
        alive      : convenience flag derived from hp
        color      : ANSI colour string for rendering
    """

    _next_id: int = 0

    # Direction → visual glyph
    DIR_GLYPHS: dict[Direction, str] = {
        Direction.UP:    "▲",
        Direction.DOWN:  "▼",
        Direction.LEFT:  "◄",
        Direction.RIGHT: "►",
    }

    def __init__(
        self,
        y: int,
        x: int,
        *,
        is_player: bool = False,
        hp: int = 100,
        fire_rate: int = 5,
        color: str = "",
    ) -> None:
        self.id = Tank._next_id
        Tank._next_id += 1

        self.y = y
        self.x = x
        self.facing = Direction.UP
        self.hp = hp
        self.max_hp = hp
        self.fire_cd: int = 0          # ready to fire immediately
        self.fire_rate = fire_rate
        self.is_player = is_player
        self.color = color or (Color.GREEN + Color.BOLD if is_player else Color.MAGENTA + Color.BOLD)
        self.move_cd: int = 0           # movement cooldown (AI pacing)
        self.move_rate: int = 2         # ticks between AI moves

    @property
    def alive(self) -> bool:
        return self.hp > 0

    # -- actions -------------------------------------------------------------

    def try_move(self, direction: Direction, game_map: Map) -> bool:
        """Attempt to move one cell in *direction*. Returns True on success."""
        self.facing = direction
        ny = self.y + direction.dy
        nx = self.x + direction.dx
        if game_map.is_passable(ny, nx):
            self.y, self.x = ny, nx
            return True
        return False

    def try_fire(self) -> Optional[Projectile]:
        """Fire a projectile if the cooldown has expired."""
        if self.fire_cd > 0:
            return None
        self.fire_cd = self.fire_rate
        # Spawn the shell one cell ahead of the barrel
        py = self.y + self.facing.dy
        px = self.x + self.facing.dx
        return Projectile(py, px, self.facing, self.id)

    def take_damage(self, amount: int) -> None:
        """Reduce HP, clamped to zero."""
        self.hp = max(0, self.hp - amount)

    def tick_cooldowns(self) -> None:
        """Decrement all cooldown timers once per game tick."""
        if self.fire_cd > 0:
            self.fire_cd -= 1
        if self.move_cd > 0:
            self.move_cd -= 1

    # -- AI behaviour --------------------------------------------------------

    def ai_update(
        self,
        game_map: Map,
        player: "Tank",
        projectiles: list[Projectile],
    ) -> Optional[Projectile]:
        """Simple but effective AI decision-making.

        Strategy priority:
            1. If aligned with the player horizontally or vertically → fire.
            2. Otherwise, move toward the player using Manhattan approach.
            3. Randomly jitter direction to avoid getting stuck.

        Returns a Projectile if the AI decides to fire, else None.
        """
        if not self.alive:
            return None

        shot: Optional[Projectile] = None
        dy = player.y - self.y
        dx = player.x - self.x

        # --- Firing logic: shoot if roughly aligned -------------------------
        aligned = False
        if abs(dx) <= 1 and dy != 0:
            # Vertically aligned
            self.facing = Direction.DOWN if dy > 0 else Direction.UP
            aligned = True
        elif abs(dy) <= 1 and dx != 0:
            # Horizontally aligned
            self.facing = Direction.RIGHT if dx > 0 else Direction.LEFT
            aligned = True

        if aligned:
            proj = self.try_fire()
            if proj:
                shot = proj

        # --- Movement logic: close distance ---------------------------------
        if self.move_cd <= 0:
            self.move_cd = self.move_rate

            # Pick a primary direction toward the player
            choices: list[Direction] = []
            if abs(dy) >= abs(dx):
                choices.append(Direction.DOWN if dy > 0 else Direction.UP)
                if dx != 0:
                    choices.append(Direction.RIGHT if dx > 0 else Direction.LEFT)
            else:
                choices.append(Direction.RIGHT if dx > 0 else Direction.LEFT)
                if dy != 0:
                    choices.append(Direction.DOWN if dy > 0 else Direction.UP)

            # Add a random direction for unpredictability
            choices.append(random.choice(list(Direction)))

            for d in choices:
                if self.try_move(d, game_map):
                    break

        return shot

    # -- rendering -----------------------------------------------------------

    def draw(self, fb: FrameBuffer) -> None:
        """Stamp the tank glyph onto the frame buffer."""
        if not self.alive:
            return
        glyph = self.DIR_GLYPHS.get(self.facing, "O")
        fb.put(self.y, self.x, glyph, self.color)


# ─────────────────────────────────────────────────────────────────────────────
# HUD — Heads-Up Display overlay
# ─────────────────────────────────────────────────────────────────────────────

class HUD:
    """Renders score, health bar, and wave information below the map.

    The HUD occupies a fixed number of rows beneath the game grid.  It
    never overlaps with the play area.
    """

    # How many rows the HUD needs
    HEIGHT = 5

    @staticmethod
    def draw(
        fb: FrameBuffer,
        player: Tank,
        score: int,
        wave: int,
        enemies_left: int,
        map_height: int,
        map_width: int,
        message: str = "",
    ) -> None:
        """Compose the HUD onto the frame buffer rows below the map."""
        base_y = map_height  # first row after the map grid

        # Row 0: separator line
        separator = "─" * map_width
        fb.put_string(base_y, 0, separator, Color.GRAY)

        # Row 1: health bar
        bar_width = 20
        filled = int((player.hp / player.max_hp) * bar_width) if player.alive else 0
        bar = "█" * filled + "░" * (bar_width - filled)
        hp_color = Color.GREEN if player.hp > 50 else (Color.YELLOW if player.hp > 25 else Color.RED)
        label = f" HP [{bar}] {player.hp:>3}/{player.max_hp}"
        fb.put_string(base_y + 1, 0, label, hp_color + Color.BOLD)

        # Row 1 continued: score on the right
        score_str = f"SCORE: {score:>5}"
        fb.put_string(base_y + 1, map_width - len(score_str), score_str, Color.CYAN + Color.BOLD)

        # Row 2: wave info
        wave_str = f" WAVE {wave}  •  Enemies: {enemies_left}"
        fb.put_string(base_y + 2, 0, wave_str, Color.WHITE)

        # Row 3: controls reminder / message
        if message:
            fb.put_string(base_y + 3, 1, message, Color.YELLOW + Color.BOLD)
        else:
            controls = " [W/A/S/D] Move   [SPACE] Fire   [Q] Quit"
            fb.put_string(base_y + 3, 0, controls, Color.GRAY)


# ─────────────────────────────────────────────────────────────────────────────
# Leaderboard — persistent high-score tracking
# ─────────────────────────────────────────────────────────────────────────────

LEADERBOARD_PATH = Path(__file__).parent / ".tank_shooter_scores.json"
MAX_LEADERBOARD_ENTRIES = 10


@dataclass
class ScoreEntry:
    """A single leaderboard record."""
    name: str
    score: int
    wave: int


class Leaderboard:
    """Load, update, and display a JSON-backed high-score table.

    Scores are stored in a JSON file next to the game script so they
    persist across sessions.
    """

    def __init__(self) -> None:
        self.entries: list[ScoreEntry] = []
        self._load()

    def _load(self) -> None:
        """Read scores from disk, silently starting fresh on any error."""
        try:
            if LEADERBOARD_PATH.exists():
                data = json.loads(LEADERBOARD_PATH.read_text())
                self.entries = [ScoreEntry(**e) for e in data]
        except (json.JSONDecodeError, TypeError, KeyError):
            self.entries = []

    def _save(self) -> None:
        """Persist the current leaderboard to disk."""
        data = [{"name": e.name, "score": e.score, "wave": e.wave} for e in self.entries]
        LEADERBOARD_PATH.write_text(json.dumps(data, indent=2))

    def qualifies(self, score: int) -> bool:
        """Return True if *score* would make the top N."""
        if len(self.entries) < MAX_LEADERBOARD_ENTRIES:
            return True
        return score > self.entries[-1].score

    def add(self, name: str, score: int, wave: int) -> None:
        """Insert a new entry and trim to the max size."""
        self.entries.append(ScoreEntry(name=name, score=score, wave=wave))
        self.entries.sort(key=lambda e: e.score, reverse=True)
        self.entries = self.entries[:MAX_LEADERBOARD_ENTRIES]
        self._save()

    def render_table(self, highlight_score: Optional[int] = None) -> list[str]:
        """Return the leaderboard as a list of pre-formatted strings."""
        lines: list[str] = []
        lines.append(f"{'RANK':<6}{'NAME':<16}{'SCORE':>7}{'WAVE':>6}")
        lines.append("─" * 36)
        for i, entry in enumerate(self.entries, start=1):
            marker = " ◄" if highlight_score is not None and entry.score == highlight_score else ""
            lines.append(f" {i:<5}{entry.name:<16}{entry.score:>7}{entry.wave:>6}{marker}")
        if not self.entries:
            lines.append("  (no scores yet)")
        return lines


# ─────────────────────────────────────────────────────────────────────────────
# Game — main loop and orchestration
# ─────────────────────────────────────────────────────────────────────────────

class Game:
    """Top-level game controller.

    Responsibilities:
        • Initialise the map, player, and first wave of enemies.
        • Run the fixed-timestep main loop (~15 ticks/sec).
        • Dispatch input, update entities, resolve collisions, render.
        • Manage wave progression and scoring.
        • Show game-over / leaderboard screen.

    The tick rate is intentionally modest so the game is comfortable in
    any terminal — even over SSH.
    """

    # Arena dimensions (characters).  Adjust to taste.
    MAP_WIDTH  = 60
    MAP_HEIGHT = 24

    # Tick rate
    TPS = 12  # ticks per second

    def __init__(self) -> None:
        # -- world -----------------------------------------------------------
        self.game_map = Map(self.MAP_WIDTH, self.MAP_HEIGHT)
        self.fb = FrameBuffer(self.MAP_WIDTH, self.MAP_HEIGHT + HUD.HEIGHT)

        # -- entities --------------------------------------------------------
        spawn = self.game_map.get_spawn_point("top_left")
        self.player = Tank(spawn[0], spawn[1], is_player=True, fire_rate=3)
        self.enemies: list[Tank] = []
        self.projectiles: list[Projectile] = []
        self.explosions: list[Explosion] = []

        # -- game state ------------------------------------------------------
        self.score: int = 0
        self.wave: int = 0
        self.running: bool = True
        self.message: str = ""
        self.message_ttl: int = 0      # ticks until message clears

        # -- leaderboard -----------------------------------------------------
        self.leaderboard = Leaderboard()

        # Kick off the first wave
        self._next_wave()

    # -- wave management -----------------------------------------------------

    def _next_wave(self) -> None:
        """Spawn the next wave of enemy tanks."""
        self.wave += 1
        count = min(2 + self.wave, 8)  # cap at 8 enemies
        self.message = f"WAVE {self.wave} — {count} enemies incoming!"
        self.message_ttl = 20

        for _ in range(count):
            pos = self.game_map.get_spawn_point("random")
            # Ensure enemies don't spawn on top of the player
            while abs(pos[0] - self.player.y) < 5 and abs(pos[1] - self.player.x) < 5:
                pos = self.game_map.get_spawn_point("random")
            enemy = Tank(
                pos[0], pos[1],
                is_player=False,
                hp=50 + self.wave * 10,
                fire_rate=max(8 - self.wave, 3),
                color=Color.MAGENTA + Color.BOLD,
            )
            enemy.move_rate = max(3 - self.wave // 3, 1)
            self.enemies.append(enemy)

    # -- input ---------------------------------------------------------------

    def _handle_input(self) -> None:
        """Poll for a keypress and translate it into a game action."""
        key = _get_key()
        if key is None:
            return

        key = key.lower()

        if key == "q":
            self.running = False
            return

        if key in KEY_TO_DIRECTION:
            self.player.try_move(KEY_TO_DIRECTION[key], self.game_map)

        if key == " ":
            proj = self.player.try_fire()
            if proj:
                self.projectiles.append(proj)

    # -- physics / collision -------------------------------------------------

    def _update_projectiles(self) -> None:
        """Advance all projectiles and resolve hits against tanks."""
        for proj in self.projectiles:
            if not proj.alive:
                continue
            proj.advance(self.game_map)
            if not proj.alive:
                continue  # hit a wall during advance

            # Check hit against player
            if proj.owner_id != self.player.id and proj.y == self.player.y and proj.x == self.player.x:
                self.player.take_damage(proj.damage)
                proj.alive = False
                self.explosions.append(Explosion(self.player.y, self.player.x))
                continue

            # Check hit against enemies
            for enemy in self.enemies:
                if not enemy.alive:
                    continue
                if proj.owner_id != enemy.id and proj.y == enemy.y and proj.x == enemy.x:
                    enemy.take_damage(proj.damage)
                    proj.alive = False
                    self.explosions.append(Explosion(enemy.y, enemy.x))
                    if not enemy.alive:
                        self.score += 100 + self.wave * 25
                        self.message = f"+{100 + self.wave * 25} points!"
                        self.message_ttl = 12
                    break

        # Purge dead projectiles
        self.projectiles = [p for p in self.projectiles if p.alive]

    def _update_enemies(self) -> None:
        """Tick every AI enemy and collect their shots."""
        for enemy in self.enemies:
            enemy.tick_cooldowns()
            shot = enemy.ai_update(self.game_map, self.player, self.projectiles)
            if shot:
                self.projectiles.append(shot)

        # Remove destroyed enemies
        self.enemies = [e for e in self.enemies if e.alive]

    # -- rendering -----------------------------------------------------------

    def _render(self) -> None:
        """Compose a complete frame and flush it to the terminal."""
        self.fb.clear()

        # Layer 0: terrain
        self.game_map.draw(self.fb)

        # Layer 1: projectiles (below tanks so tanks are always visible)
        for proj in self.projectiles:
            proj.draw(self.fb)

        # Layer 2: explosions
        for exp in self.explosions:
            exp.draw(self.fb)

        # Layer 3: tanks (topmost game-object layer)
        for enemy in self.enemies:
            enemy.draw(self.fb)
        self.player.draw(self.fb)

        # Layer 4: HUD
        msg = self.message if self.message_ttl > 0 else ""
        HUD.draw(
            self.fb,
            self.player,
            self.score,
            self.wave,
            len(self.enemies),
            self.MAP_HEIGHT,
            self.MAP_WIDTH,
            message=msg,
        )

        # Flush to terminal
        sys.stdout.write(self.fb.render())
        sys.stdout.flush()

    # -- game-over screen ----------------------------------------------------

    def _show_game_over(self) -> None:
        """Display the final score, prompt for name, and show leaderboard."""
        _restore_terminal()

        # Clear screen
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()

        print()
        print(Color.colorize("  ╔══════════════════════════════════════╗", Color.RED, Color.BOLD))
        print(Color.colorize("  ║           G A M E   O V E R         ║", Color.RED, Color.BOLD))
        print(Color.colorize("  ╚══════════════════════════════════════╝", Color.RED, Color.BOLD))
        print()
        print(Color.colorize(f"  Final Score : {self.score}", Color.CYAN, Color.BOLD))
        print(Color.colorize(f"  Wave Reached: {self.wave}", Color.CYAN))
        print()

        # Leaderboard entry
        if self.leaderboard.qualifies(self.score) and self.score > 0:
            print(Color.colorize("  NEW HIGH SCORE!", Color.YELLOW, Color.BOLD))
            name = input("  Enter your name: ").strip()[:15] or "Anonymous"
            self.leaderboard.add(name, self.score, self.wave)
            print()

        # Display leaderboard
        print(Color.colorize("  ── LEADERBOARD ──", Color.YELLOW, Color.BOLD))
        for line in self.leaderboard.render_table(highlight_score=self.score):
            print(f"  {line}")
        print()
        print(Color.colorize("  Press ENTER to exit...", Color.GRAY))
        input()

    # -- title screen --------------------------------------------------------

    @staticmethod
    def _show_title() -> None:
        """Display an ASCII art title screen before the game starts."""
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()

        title_art = r"""
    ╔═══════════════════════════════════════════════════════════╗
    ║                                                           ║
    ║   ████████╗ █████╗ ███╗   ██╗██╗  ██╗                    ║
    ║   ╚══██╔══╝██╔══██╗████╗  ██║██║ ██╔╝                    ║
    ║      ██║   ███████║██╔██╗ ██║█████╔╝                     ║
    ║      ██║   ██╔══██║██║╚██╗██║██╔═██╗                     ║
    ║      ██║   ██║  ██║██║ ╚████║██║  ██╗                    ║
    ║      ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═══╝╚═╝  ╚═╝                    ║
    ║                                                           ║
    ║          ███████╗██╗  ██╗ ██████╗  ██████╗ ████████╗      ║
    ║          ██╔════╝██║  ██║██╔═══██╗██╔═══██╗╚══██╔══╝      ║
    ║          ███████╗███████║██║   ██║██║   ██║   ██║         ║
    ║          ╚════██║██╔══██║██║   ██║██║   ██║   ██║         ║
    ║          ███████║██║  ██║╚██████╔╝╚██████╔╝   ██║         ║
    ║          ╚══════╝╚═╝  ╚═╝ ╚═════╝  ╚═════╝   ╚═╝         ║
    ║                                                           ║
    ║              ── Terminal Edition ──                        ║
    ║                                                           ║
    ║    Controls:                                              ║
    ║      W/A/S/D .... Move your tank                          ║
    ║      SPACE ...... Fire                                    ║
    ║      Q .......... Quit                                    ║
    ║                                                           ║
    ╚═══════════════════════════════════════════════════════════╝
        """

        for line in title_art.strip().split("\n"):
            print(Color.colorize(line, Color.GREEN, Color.BOLD))

        print()
        print(Color.colorize("           Press ENTER to start...", Color.YELLOW, Color.BOLD))
        input()

    # -- main loop -----------------------------------------------------------

    def run(self) -> None:
        """Entry point: title → game loop → game over → exit."""
        self._show_title()

        # Prepare terminal for raw input
        _setup_terminal()

        # Hide cursor and clear screen
        sys.stdout.write("\033[?25l")  # hide cursor
        sys.stdout.write("\033[2J")    # clear screen
        sys.stdout.flush()

        tick_duration = 1.0 / self.TPS

        try:
            while self.running:
                tick_start = time.monotonic()

                # 1. Input
                self._handle_input()

                # 2. Update
                self.player.tick_cooldowns()
                self._update_enemies()
                self._update_projectiles()

                # Update explosions
                for exp in self.explosions:
                    exp.update()
                self.explosions = [e for e in self.explosions if e.alive]

                # Decay message timer
                if self.message_ttl > 0:
                    self.message_ttl -= 1

                # Check for wave completion
                if not self.enemies:
                    self._next_wave()

                # Check player death
                if not self.player.alive:
                    self.running = False

                # 3. Render
                self._render()

                # 4. Frame pacing — sleep for the remainder of the tick
                elapsed = time.monotonic() - tick_start
                sleep_time = tick_duration - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)

        except KeyboardInterrupt:
            pass
        finally:
            # Restore terminal state no matter how we exit
            sys.stdout.write("\033[?25h")  # show cursor
            sys.stdout.flush()
            _restore_terminal()

        # Game-over sequence (terminal is back to normal here)
        self._show_game_over()


# ─────────────────────────────────────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    """Launch the game."""
    game = Game()
    game.run()


if __name__ == "__main__":
    main()
