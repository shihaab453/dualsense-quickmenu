# DualSense Quick Menu

A PS5-style Control Center for Windows. Press the **PS button** on your
DualSense while gaming and an overlay pops up. From there you can browse and
control Spotify, change the volume, switch to another open app, and
sleep/shut down/restart the PC, all with the controller and without touching
your keyboard or mouse.

## What's in it

Pressing PS opens a tray of icons along the bottom (D-pad left/right to move
between them, Cross to open one, Circle to back out, PS again to close):

- **Music**: log in with Spotify once, then browse Liked Songs and your
  playlists, play a track, and control like/shuffle/previous/play-pause/
  next/repeat. The last one opens the current song in Spotify itself.
  Playback control needs **Spotify Premium** (free accounts can still browse,
  Spotify just blocks remote control for them).
- **Sound**: output/input device names, master volume, mic mute, mic volume.
- **Power**: Sleep, Shut Down, Restart.
- A **Now Playing** card on the home screen (D-pad up from the tray) shows
  what's currently playing on Spotify: real album art, title, and (once
  selected) the artist and what playlist or album it's from. It only shows
  Spotify, so if nothing's playing there, the card just says so instead of
  showing some other app. Opening it with Cross goes to the full Now Playing
  panel, which *does* also show whatever Windows itself is tracking
  (browsers, other players, etc.) when Spotify has nothing playing.
- A **Switch App** card next to it works like a controller-driven Alt-Tab. It
  opens a live list of every open, switchable window (real icon and title, no
  setup needed); D-pad up/down picks one, and Cross switches to it and closes
  the overlay. It's built fresh from Windows itself every time it's opened,
  so it always reflects what's actually running.

## Controls

| Button | While menu is open |
| --- | --- |
| **PS** | Open / close the menu |
| **D-pad left / right** | Move between tray icons, or adjust a slider (volume) |
| **D-pad up / down** | Move up/down within a panel's list, or between the tray and the home cards |
| **Cross (X)** | Activate the selected item (hold while adjusting a slider for finer 1% steps instead of 2%) |
| **Circle (O)** | Back out one level (closes the menu entirely from the top level) |

## Using it without a controller

Press **Ctrl+Alt+P** anywhere, even while a game has focus, to open or close
the overlay, exactly like the PS button. If another app has claimed it, the
default Automatic setting tries **Ctrl+Alt+Shift+P** instead and shows a tray
notification. This isn't just for testing: if you don't always have the
controller plugged in, it's a full second way to use the app day to day.

Once it's open, arrow keys move the selection, **Enter** activates it, and
**Esc** backs out, the same as D-pad/Cross/Circle. The one thing a keyboard
can't do on its own is *open* the menu in the first place, which is exactly
what the global shortcut is for. The tray icon's right-click **Show menu** works too.

Settings lets you choose Automatic, Ctrl+Alt+P, or Ctrl+Alt+Shift+P for the
next launch, and shows whether the active shortcut registered successfully.
**Copy diagnostics** reports that state too.

## Setup

### Option A: prebuilt Windows build

This project is in closed alpha testing right now, so the build isn't posted
here as a public download yet. If you're one of the testers, you'll have
gotten `DualSenseQuickMenu-windows.zip` directly. (If you're reading this on
GitHub and want it, just ask.)

1. Extract the zip anywhere (about 126 MB extracted).
2. Plug in the DualSense with a **USB cable** (Bluetooth not supported yet).
3. Run `DualSenseQuickMenu.exe`. No Python needed.

The first time you run it, a notification confirms it's running and the
Settings window opens so you can connect Spotify. After that it starts
silently into the tray. Run it again while it's already running and it'll
just point you back at the tray icon.

The app lives in the system tray (blue "PS" icon). Right-click it for
**Show menu**, **Settings…**, and **Quit**. Windows may show a "Windows
protected your PC" SmartScreen warning the first time, because the build
isn't code-signed yet. Click **More info → Run anyway**.

To start it automatically when you log in, tick **Start with Windows** in
Settings (right-click the tray icon → **Settings…**).

Then follow **Spotify setup** below.

### Option B: run from source

1. Install dependencies (one time):
   ```
   .venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock.txt
   ```
   (If `.venv` doesn't exist yet: `py -m venv .venv` first.) `requirements.lock.txt`
   pins every dependency, direct and transitive, by hash — see its own header
   for why and how to regenerate it after changing `requirements.txt`.
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
`pythonw.exe` from the venv, followed by the full path to `main.py`. For
example, if you cloned to `C:\Apps\dualsense-quickmenu`:

```
C:\Apps\dualsense-quickmenu\.venv\Scripts\pythonw.exe C:\Apps\dualsense-quickmenu\main.py
```

Put that shortcut in `shell:startup` (Win+R, type `shell:startup`) to have it
start automatically when you log in.

### Building the distributable yourself

```
.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-dev.lock.txt
.venv\Scripts\python.exe tools\build.py
```

That runs the test suite, then PyInstaller, then the built exe's `--selftest`
— and only then zips it, as `dist\DualSenseQuickMenu-<version>-<commit>.zip`
(`-dirty` appended if your working tree has uncommitted changes). Neither
verification step is skippable on purpose: the ways a packaged build of this
app breaks are all silent (blank icons, wrong font, dead PS button), so "it
built" on its own doesn't tell you much. It then unzips its own output
somewhere else and runs `--selftest` again against *that* copy — a packaging
bug that only shows up once the build is no longer sitting next to the
source tree it came from won't pass just because the first check did.

Alongside the zip you'll find a `.manifest.json` (source commit, dependency
lock hash, a SHA-256 of every file in the build, and the known limitations
of what was checked) and, in `dist\`, `test-evidence.xml` and
`smoke-test-record.json` — the same evidence CI uploads for every run. None
of this makes the build *reproducible*; see the manifest's own
`reproducibility_caveat` field for why not.

## Spotify setup

This takes two steps the first time: a one-off **client ID**, then logging in.

### 1. Your own Spotify client ID

Spotify only lets an app talk to its API on behalf of people the app's
creator has added by hand, up to 25 of them, until the app goes through
Spotify's review. Rather than everyone sharing one app and hitting that wall,
you create your own free one, which always works for the account that made
it.

Right-click the tray icon → **Settings…** → **Spotify**, then:

1. Click **Open Spotify dashboard** and log in with your normal Spotify
   account.
2. Click **Create app**. Any name and description will do.
3. Copy the **Redirect URI** shown in the Settings window
   (`http://127.0.0.1:8888/callback`) into the app's **Redirect URIs** box.
   It has to match exactly.
4. Tick **Web API**, save, then copy the app's **Client ID** and paste it into
   the Settings window's **Your Client ID** box. Press **Save**.

You only ever do this once. Nothing is charged and no Spotify Premium is
needed for this part.

### 2. Logging in

Open the Music panel and select **Log in with Spotify**. Your default
browser opens to Spotify's own login page. Approve access and you're
returned to a local page you can close. Press the PS button again afterward
to bring the overlay back (logging in takes window focus away, same as any
browser action would).

Your login token is cached at
`%APPDATA%\DualSenseQuickMenu\spotify_token.json` so you won't need to log in
again on future runs unless it expires or you revoke access from your
Spotify account settings.

Playback control (play/pause/skip/shuffle/repeat/liking songs) needs
**Spotify Premium**. Free accounts can still browse Liked Songs and
playlists, but trying to play something shows an inline message explaining
why instead of just failing silently.

## Important: game display mode

Overlays cannot draw on top of *exclusive fullscreen* games. Set your game
to **Borderless** (usually under Settings → Video → Display Mode). It looks
identical to fullscreen anyway, and it's standard practice for anyone using
overlays.

While the menu is open it takes window focus on purpose, so the game stops
reacting to the D-pad; closing the menu gives focus straight back.

## Troubleshooting

**Start here for anything not listed below.** Right-click the tray icon →
**Settings…** → **Copy diagnostics**, then paste the result into your
message. It's a short summary of your setup plus the last few errors.

The report is shown to you first, in a box you can edit, and nothing is copied
until you press the button in it. Your Spotify client ID and login token are
never in it, only whether they exist. The error lines have your home folder,
web addresses, e-mail addresses and anything credential-shaped stripped out of
them, which catches the shapes we know about rather than every shape there is,
so the box is there for you to check. **Open log folder** next to it gets you
the full `log.txt` if more detail is needed. That file has had none of the
above done to it, so read it before you send it. Nothing is uploaded anywhere
automatically.

- **PS button does nothing**: make sure the controller is on USB and that no
  other tool (DS4Windows, Steam with "PlayStation Controller Support"
  enabled) is capturing the controller. Ctrl+Alt+P works either way in the
  meantime.
- **The keyboard shortcut does nothing**: Settings shows whether it registered.
  Automatic tries Ctrl+Alt+Shift+P if Ctrl+Alt+P was already claimed; choose a
  specific option there and restart the app if you prefer one permanently.
- **Menu doesn't appear over the game**: the game is in exclusive fullscreen.
  Switch it to borderless windowed.
- **Music panel says "Set up Spotify…"**: no client ID has been saved yet.
  Right-click the tray icon → **Settings…** and follow the Spotify steps
  above. (Pressing Cross on that row closes the overlay and opens Settings
  for you.)
- **Music panel shows "Log in with Spotify" every time**: the cached token
  may be missing a permission the app needs (this happened once already when
  playlist access was added). Logging in again fixes it.
- **Login fails with an "invalid redirect URI" error**: the Redirect URI in
  your Spotify app doesn't match `http://127.0.0.1:8888/callback` exactly.
  Copy it from the Settings window instead of typing it out.
- **"Open Spotify on this PC or phone to enable playback control"**: Spotify
  needs an *active* device to control. Open the Spotify app anywhere (PC,
  phone) and start playback there once, then the overlay can take over.
- **Now Playing card says "Nothing playing" even though something is**: the
  card only ever shows Spotify's own data, not other players. If Spotify
  itself has nothing active (paused too long, no active device, logged out),
  the card just says so. Open the full panel with Cross to also see whatever
  Windows' own media tracker last saw for other apps.
- **Now Playing panel shows the wrong app or stale info**: the panel (not the
  home card) falls back to Windows' media tracker when Spotify has nothing
  playing, so it can show a browser or another player. That's expected, not
  a bug.
- **Switch App shows a generic icon for some system apps** (e.g. Windows'
  own Settings app): a handful of modern Windows apps don't expose their
  real per-window icon the normal way, so those fall back to a generic file
  icon instead of a blank row. The window itself still switches correctly.
- **Switching doesn't bring the game to the front**: the same
  anti-focus-stealing behavior that occasionally needs a couple of tries to
  open the overlay itself can also affect switching to another app. It
  retries automatically, so it should sort itself out within a moment.

## Not yet supported (planned)

Bluetooth, Discord controls, switching audio output/input device (right now it
just shows the current one; actually switching needs an undocumented Windows
COM interface), and the nested Shuffle/Repeat sub-menu from the original PS5
UI (this app uses direct one-press toggle/cycle buttons instead, which does
the same job).

## Licence

Copyright (C) 2026 shihaab453. All rights reserved except as granted in
[LICENSE](LICENSE): the PolyForm Strict License 1.0.0 plus additional
permissions.

**You may:**

- read, study and learn from this source code
- download and run the app for your own personal, non-commercial use
- modify the code for your own personal use
- submit your changes back here as a contribution (see
  [CONTRIBUTING.md](CONTRIBUTING.md))

**You may not:**

- distribute, publish, share, upload or re-host this software, or any modified
  version of it
- remove or alter the author's name or the copyright notices, or present this
  work as your own

The software comes with **no warranty of any kind**. See [LICENSE](LICENSE) for
the terms that actually govern; the summary above is not the licence.

Bundled dependencies keep their own licences, notably PySide6 (Qt for
Python), which is LGPL v3.
