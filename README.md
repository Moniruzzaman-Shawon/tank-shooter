# Tank Shooter — Terminal Edition

A professional, object-oriented tank combat game rendered entirely in the terminal using ASCII art and ANSI colors.

```
████████████████████████████████████████████████████████████
█··························································█
█··►·······▓▓▓▓···········≈≈≈·····························█
█··········▓▓▓▓···········≈≈≈··············▲···············█
█·····················─····································█
█·····▓▓▓··························▓▓▓▓▓···················█
█·····▓▓▓··························▓▓▓▓▓···················█
█··························································█
█·····························◄········────················█
█··························································█
████████████████████████████████████████████████████████████
────────────────────────────────────────────────────────────
 HP [████████████████████] 100/100              SCORE:   350
 WAVE 2  •  Enemies: 2
```

## Features

- **Frame-buffer rendering** — zero-flicker output using a 2D character grid flushed once per tick
- **ANSI colors** — green player, magenta enemies, red projectiles, yellow walls, cyan water
- **Procedural maps** — destructible walls, water hazards, and safe spawn zones generated each run
- **Wave system** — escalating difficulty with more enemies, faster fire rates, and higher HP per wave
- **AI enemies** — tanks that track, chase, and fire at the player
- **Collision detection** — projectile↔tank, projectile↔wall (destructible walls break on impact), tank↔wall blocking
- **Explosion effects** — brief particle animations on tank destruction
- **HUD** — health bar, score, wave info, and controls reminder
- **Persistent leaderboard** — top 10 high scores saved to a local JSON file

## Requirements

- Python 3.8+
- A terminal that supports ANSI escape codes (virtually all modern terminals)
- No third-party dependencies

## Installation

```bash
git clone https://github.com/moniruzzamanshawon/tank-shooter.git
cd tank-shooter
```

## Usage

```bash
python3 tank_shooter.py
```

## Controls

| Key | Action |
|-----|--------|
| `W` | Move up |
| `A` | Move left |
| `S` | Move down |
| `D` | Move right |
| `Space` | Fire |
| `Q` | Quit |

## Architecture

| Class | Responsibility |
|-------|----------------|
| `FrameBuffer` | 2D character grid rendered once per tick via cursor-home — no flicker |
| `Map` | Procedural arena with borders, destructible walls, and water hazards |
| `Tank` | Player and AI entity — health, cooldowns, directional glyphs, movement, firing |
| `Projectile` | Fast-moving shells with wall destruction on impact |
| `Explosion` | Brief particle effect on tank death |
| `HUD` | Health bar, score, wave info overlay below the map |
| `Leaderboard` | JSON-backed persistent high-score table |
| `Game` | Main loop — input → update → collisions → render at 12 TPS |

## License

MIT
