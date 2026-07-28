# Contributing

Pull requests are welcome. This project isn't open source in the usual sense —
see [LICENSE](LICENSE) — so the terms around contributions are worth stating
plainly rather than leaving to be assumed.

## The short version

You may modify this code for your own use and send those changes here. You may
not publish or redistribute the software, modified or not.

## What submitting a pull request means

By opening a pull request (or otherwise sending a change to this project), you
grant the project's author a perpetual, worldwide, irrevocable, royalty-free
licence to use, modify and license your contribution as part of this software.
That's permission 2 in [LICENSE](LICENSE).

Why it's spelled out: GitHub's terms say a contribution is licensed under "the
repository's licence", which works cleanly for a normal open-source project.
This project's licence doesn't grant redistribution rights, so relying on that
clause alone would leave it unclear whether a merged patch could actually be
shipped. The explicit grant removes the ambiguity.

You keep the copyright in your own contribution. You're confirming you wrote it
(or otherwise have the right to submit it), not signing it away.

## Before you open a PR

There's no CI, so please run the checks yourself:

```bash
.venv\Scripts\python.exe tests\verify_settings.py
.venv\Scripts\python.exe tests\verify_logging.py
.venv\Scripts\python.exe tests\verify_startup.py
.venv\Scripts\python.exe tests\verify_diagnostics.py
.venv\Scripts\python.exe tests\verify_startup_registry.py
.venv\Scripts\python.exe tests\verify_spotify_links.py
```

All four should exit 0. If you changed anything that ends up in a build, also
run:

```bash
.venv\Scripts\python.exe tools\build.py
```

which builds, runs the packaged app's `--selftest`, and refuses to package a
build that fails it.

**Read [HANDOFF.md](HANDOFF.md) first if you're touching layout code.** It
documents eight specific Qt and Spotify API behaviours that have each cost real
debugging time here — several of them fail silently rather than raising, so
"it looks fine" isn't much evidence. The one that bites most often: a row of
widgets needing its own sub-layout must be wrapped in a real `QWidget` and added
with `addWidget()`, never `addLayout()`.

## Reporting a bug instead

You don't need to touch the code. Right-click the tray icon → **Settings…** →
**Copy diagnostics**, then paste that into the issue. It includes the version,
your Windows build, and the last few errors, and it deliberately contains no
tokens or account details.
