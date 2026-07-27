# DualSense Quick Menu

A PS5-style Control Center for Windows. Press the **PS button** on your
DualSense while gaming and an overlay appears — control Spotify, change the
volume, sleep/shut down/restart the PC, and switch between your games — all
with the controller, without touching your keyboard or mouse.

## What's in it

Pressing PS opens a tray of icons along the bottom (D-pad left/right to move
between them, Cross to open one, Circle to back out, PS again to close):

- **Music** — log in with Spotify once, then browse Liked Songs and your
  playlists, play a track, and control like/shuffle/previous/play-pause/
  next/repeat. Requires **Spotify Premium** for playback control (free
  accounts can browse but Spotify itself blocks remote control).
- **Sound** — output/input device names, master volume, mic mute, mic
  volume.
- **Task Switcher** — jump back into a game, or launch one from a list you
  configure (see below).
- **Power** — Sleep, Shut Down, Restart.
- A **Now Playing** card on the home screen (D-pad up from the tray) shows
  whatever's currently playing — prefers Spotify if you're logged in,
  otherwise falls back to whatever Windows itself is tracking (works with
  browsers, other players, etc).
- **Chats & Calls** and the other home cards are decorative, matching the
  original PS5 UI they're modeled on — no function behind them.

## Controls

| Button | While menu is open |
| --- | --- |
| **PS** | Open / close the menu |
| **D-pad left / right** | Move between tray icons, or adjust a slider (volume) |
| **D-pad up / down** | Move up/down within a panel's list, or between the tray and the home cards |
| **Cross (X)** | Activate the selected item (hold while adjusting a slider for finer 1% steps instead of 2%) |
| **Circle (O)** | Back out one level (closes the menu entirely from the top level) |

Keyboard fallback for testing without a controller: arrow keys, Enter, Esc
(there's no keyboard equivalent for the PS button itself — use the tray
icon's **Show menu**, or `--demo`, to open it).

## Setup

1. Install dependencies (one time):
   ```
   .venv\Scripts\python.exe -m pip install -r requirements.txt
   ```
   (If `.venv` doesn't exist yet: `py -m venv .venv` first.)
2. Plug in the DualSense with a **USB cable** (Bluetooth not supported yet).
3. Run it:
   ```
   .venv\Scripts\python.exe main.py
   ```
   The app lives in the system tray (blue "PS" icon). Right-click it for
   **Show menu** (useful without a controller) and **Quit**.

   `main.py --demo` opens the menu immediately on launch, for testing.

To run it with **no console window**, create a shortcut with this target:

```
C:\Projects\dualsense-quickmenu\.venv\Scripts\pythonw.exe C:\Projects\dualsense-quickmenu\main.py
```

Put that shortcut in `shell:startup` (Win+R, type `shell:startup`) to have it
start automatically when you log in.

## Spotify setup

The Music panel needs you to log in once. Open the Music panel and select
**Log in with Spotify** — your default browser opens to Spotify's own login
page; approve access and you're returned to a local page you can close.
Press the PS button again afterward to bring the overlay back (logging in
takes window focus away, same as any browser action would).

Your login token is cached at
`%APPDATA%\DualSenseQuickMenu\spotify_token.json` so you won't need to log in
again on future runs unless it expires or you revoke access from your
Spotify account settings.

Playback control (play/pause/skip/shuffle/repeat/liking songs) requires
**Spotify Premium** — free accounts can still browse Liked Songs and
playlists, but attempting to play something shows an inline message
explaining why instead of failing silently.

## Task Switcher setup

Windows has no equivalent of the PS5's "recently played games" list, so the
Switcher panel is backed by a list you maintain yourself in
`config/pinned_games.json`:

```json
[
  {"name": "Rocket League", "path": "C:\\Program Files\\Epic Games\\rocketleague\\Binaries\\Win64\\RocketLeague.exe"},
  {"name": "Another Game", "path": "C:\\Games\\AnotherGame\\Game.exe"}
]
```

Every game listed here always shows under **Pinned Games**. Whichever of
them are *currently running* also show under **Recent Games** (detected by
matching the running process name against each entry's `path` — no
Steam/Epic-specific integration needed). Pressing Cross on a row launches
that game's exe. If it's already running, what happens depends on the game
itself (most single-instance games just bring themselves to the front; this
app doesn't do anything special to force that).

Finding a game's exe path: right-click its shortcut (Desktop, Start Menu, or
inside its launcher) → **Properties** → the **Target** field.

## Important: game display mode

Overlays cannot draw on top of *exclusive fullscreen* games. Set your game
to **Borderless** (usually under Settings → Video → Display Mode). It looks
identical to fullscreen — this is standard practice for anyone using
overlays.

While the menu is open it takes window focus on purpose, so the game stops
reacting to the D-pad; closing the menu gives focus straight back.

## Troubleshooting

- **PS button does nothing** — make sure the controller is on USB and that no
  other tool (DS4Windows, Steam with "PlayStation Controller Support" enabled)
  is capturing the controller.
- **Menu doesn't appear over the game** — the game is in exclusive fullscreen;
  switch it to borderless windowed.
- **Music panel shows "Log in with Spotify" every time** — the cached token
  may be missing a permission the app needs (this happened once already when
  playlist access was added); logging in again fixes it.
- **"Open Spotify on this PC or phone to enable playback control"** — Spotify
  needs an *active* device to control; open the Spotify app anywhere (PC,
  phone) and start playback there once, then the overlay can take over.
- **Now Playing card shows the wrong app / stale info** — it only prefers
  Spotify data when you're logged in and something's actually playing there;
  otherwise it falls back to whatever Windows' own media tracker last saw.

## Not yet supported (planned)

Bluetooth, Steam-launched games in the Task Switcher, Discord controls,
real game box art (color-coded placeholders are used instead), switching
audio output/input device (vs. just showing the current one — needs an
undocumented Windows COM interface), the nested Shuffle/Repeat sub-menu from
the original PS5 UI (this app uses direct one-press toggle/cycle buttons
instead, which is functionally equivalent).
