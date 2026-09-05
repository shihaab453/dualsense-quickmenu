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
.venv\Scripts\python.exe -m pytest
```

That takes about half a minute and should end in `passed`. It needs the dev
dependencies (`pip install -r requirements-dev.txt`).

Some of the checks use real Windows state - they write to the registry under a
test-only name, briefly take foreground focus, and register a real global
hotkey - so they want a normal desktop session. If that's inconvenient right
now, run the hermetic ones on their own:

```bash
.venv\Scripts\python.exe -m pytest -m unit
```

A `skipped` result is not a failure. The hotkey checks skip themselves when
something else on your machine already holds Ctrl+Alt+P, which is usually a
copy of this app already running.

Each group of checks is also a standalone script you can run directly, which
is the quickest way to work on one:

```bash
.venv\Scripts\python.exe tests\verify_settings.py
```

If you changed anything that ends up in a build, also run:

```bash
.venv\Scripts\python.exe tools\build.py
```

which builds, runs the packaged app's `--selftest`, and refuses to package a
build that fails it.

**Read [HANDOFF.md](HANDOFF.md) first if you're touching layout code.** It
documents the specific Qt and Spotify API behaviours that have each cost real
debugging time here — several of them fail silently rather than raising, so
"it looks fine" isn't much evidence. The one that bites most often: a row of
widgets needing its own sub-layout must be wrapped in a real `QWidget` and added
with `addWidget()`, never `addLayout()`.

## Reporting a bug instead

You don't need to touch the code. Right-click the tray icon → **Settings…** →
**Copy diagnostics**, then paste that into the issue. It includes the version,
your Windows build, and the last few errors. Tokens and account details are
reported as a yes or a no rather than as values, and the error lines are
stripped of paths and credential-shaped text, but you get the whole report in
an editable box before anything is copied, so have a read of it first.
