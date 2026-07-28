# DualSense Quick Menu

A PS5-style Control Center for Windows. Press the **PS button** on your
DualSense while gaming and an overlay appears — browse and control Spotify,
change the volume, and sleep/shut down/restart the PC — all with the
controller, without touching your keyboard or mouse.

## What's in it

Pressing PS opens a tray of icons along the bottom (D-pad left/right to move
between them, Cross to open one, Circle to back out, PS again to close):

- **Music** — log in with Spotify once, then browse Liked Songs and your
  playlists, play a track, and control like/shuffle/previous/play-pause/
  next/repeat. The last control opens the current song in Spotify itself.
  Requires **Spotify Premium** for playback control (free accounts can browse
  but Spotify itself blocks remote control).
- **Sound** — output/input device names, master volume, mic mute, mic
  volume.
- **Power** — Sleep, Shut Down, Restart.
- A **Now Playing** card on the home screen (D-pad up from the tray) shows
  whatever's currently playing — prefers Spotify if you're logged in,
  otherwise falls back to whatever Windows itself is tracking (works with
  browsers, other players, etc).
- **Chats & Calls** isn't built yet — the icon is there, and opening it says so.
  The home cards other than Now Playing are decorative, matching the original
  PS5 UI they're modeled on.

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

### Option A — download the build (recommended)

1. Download `DualSenseQuickMenu-windows.zip` and extract it anywhere (about
   126 MB extracted).
2. Plug in the DualSense with a **USB cable** (Bluetooth not supported yet).
3. Run `DualSenseQuickMenu.exe`. No Python needed.

The first time you run it, a notification confirms it's running and the Settings
window opens so you can connect Spotify. After that it starts
silently into the tray — if you run it again while it's already running, it'll
just point you back at the tray icon.

The app lives in the system tray (blue "PS" icon) — right-click it for
**Show menu**, **Settings…** and **Quit**. Windows may show a
"Windows protected your PC" SmartScreen warning the first time, because the
build isn't code-signed; **More info → Run anyway**.

To start it automatically when you log in, tick **Start with Windows** in
Settings (right-click the tray icon → **Settings…**).

Then follow **Spotify setup** below.

### Option B — run from source

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
   **Show menu** (useful without a controller), **Settings…** (Spotify setup
   and troubleshooting) and **Quit**.

   `main.py --demo` opens the menu immediately on launch, for testing.

To run it with **no console window**, create a shortcut whose target is
`pythonw.exe` from the venv, followed by the full path to `main.py` — e.g. if
you cloned to `C:\Apps\dualsense-quickmenu`:

```
C:\Apps\dualsense-quickmenu\.venv\Scripts\pythonw.exe C:\Apps\dualsense-quickmenu\main.py
```

Put that shortcut in `shell:startup` (Win+R, type `shell:startup`) to have it
start automatically when you log in.

### Building the distributable yourself

```
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe tools\build.py
```

That runs PyInstaller, then runs the built exe's `--selftest`, and only zips it
into `dist\DualSenseQuickMenu-windows.zip` if every check passes. The selftest
step isn't skippable on purpose: the ways a packaged build of this app breaks
are all silent (blank icons, wrong font, dead PS button), so "it built" on its
own doesn't tell you much.

## Spotify setup

This takes two steps the first time: a one-off **client ID**, then logging in.

### 1. Your own Spotify client ID

Spotify only lets an app talk to its API on behalf of people the app's creator
has added by hand — up to 25 of them — until the app goes through Spotify's
review. Rather than everyone sharing one app and hitting that wall, you create
your own free one, which always works for the account that made it.

Right-click the tray icon → **Settings…** → **Spotify**, then:

1. Click **Open Spotify dashboard** and log in with your normal Spotify
   account.
2. Click **Create app**. Any name and description will do.
3. Copy the **Redirect URI** shown in the Settings window
   (`http://127.0.0.1:8888/callback`) into the app's **Redirect URIs** box —
   it has to match exactly.
4. Tick **Web API**, save, then copy the app's **Client ID** and paste it into
   the Settings window's **Your Client ID** box. Press **Save**.

You only ever do this once. Nothing is charged and no Spotify Premium is
needed for this part.

### 2. Logging in

Open the Music panel and select **Log in with Spotify** — your default browser
opens to Spotify's own login page; approve access and you're returned to a
local page you can close. Press the PS button again afterward to bring the
overlay back (logging in takes window focus away, same as any browser action
would).

Your login token is cached at
`%APPDATA%\DualSenseQuickMenu\spotify_token.json` so you won't need to log in
again on future runs unless it expires or you revoke access from your
Spotify account settings.

Playback control (play/pause/skip/shuffle/repeat/liking songs) requires
**Spotify Premium** — free accounts can still browse Liked Songs and
playlists, but attempting to play something shows an inline message
explaining why instead of failing silently.

## Important: game display mode

Overlays cannot draw on top of *exclusive fullscreen* games. Set your game
to **Borderless** (usually under Settings → Video → Display Mode). It looks
identical to fullscreen — this is standard practice for anyone using
overlays.

While the menu is open it takes window focus on purpose, so the game stops
reacting to the D-pad; closing the menu gives focus straight back.

## Troubleshooting

**Start here for anything not listed below.** Right-click the tray icon →
**Settings…** → **Copy diagnostics**, then paste the result into your message.
It's a short summary of your setup plus the last few errors, and it deliberately
contains no passwords, tokens, or account details. **Open log folder** next to
it gets you the full `log.txt` if more detail is needed. Nothing is uploaded
anywhere automatically.

- **PS button does nothing** — make sure the controller is on USB and that no
  other tool (DS4Windows, Steam with "PlayStation Controller Support" enabled)
  is capturing the controller.
- **Menu doesn't appear over the game** — the game is in exclusive fullscreen;
  switch it to borderless windowed.
- **Music panel says "Set up Spotify…"** — no client ID has been saved yet.
  Right-click the tray icon → **Settings…** and follow the Spotify steps above.
  (Pressing Cross on that row closes the overlay and opens Settings for you.)
- **Music panel shows "Log in with Spotify" every time** — the cached token
  may be missing a permission the app needs (this happened once already when
  playlist access was added); logging in again fixes it.
- **Login fails with an "invalid redirect URI" error** — the Redirect URI in
  your Spotify app doesn't match `http://127.0.0.1:8888/callback` exactly.
  Copy it from the Settings window rather than typing it.
- **"Open Spotify on this PC or phone to enable playback control"** — Spotify
  needs an *active* device to control; open the Spotify app anywhere (PC,
  phone) and start playback there once, then the overlay can take over.
- **Now Playing card shows the wrong app / stale info** — it only prefers
  Spotify data when you're logged in and something's actually playing there;
  otherwise it falls back to whatever Windows' own media tracker last saw.

## Not yet supported (planned)

Chats & Calls (the tray icon exists and says it's under construction),
Bluetooth, Discord controls, switching
audio output/input device (vs. just showing the current one — needs an
undocumented Windows COM interface), the nested Shuffle/Repeat sub-menu from
the original PS5 UI (this app uses direct one-press toggle/cycle buttons
instead, which is functionally equivalent).

## Licence

Copyright (C) 2026 shihaab453. All rights reserved except as granted in
[LICENSE](LICENSE) — the PolyForm Strict License 1.0.0 plus additional
permissions.

**You may:**

- read, study and learn from this source code
- download and run the app for your own personal, non-commercial use
- modify the code for your own personal use
- submit your changes back here as a contribution — see
  [CONTRIBUTING.md](CONTRIBUTING.md)

**You may not:**

- distribute, publish, share, upload or re-host this software, or any modified
  version of it
- remove or alter the author's name or the copyright notices, or present this
  work as your own

The software comes with **no warranty of any kind**. See [LICENSE](LICENSE) for
the terms that actually govern; the summary above is not the licence.

Bundled dependencies keep their own licences — notably PySide6 (Qt for Python),
which is LGPL v3.
