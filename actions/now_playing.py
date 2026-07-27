# Windows already tracks "what's playing" system-wide — it powers the little
# popup in the corner when you press a volume key. The winrt packages expose
# that tracker to Python. WinRT calls are asynchronous by design, hence the
# small asyncio wrapper.
#
# Deliberately optional: if winrt is missing or errors, get() returns None and
# the menu simply shows "Nothing playing" instead of crashing.

import asyncio

import logs

log = logs.get(__name__)

try:
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as _MediaManager,
    )
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False


async def _fetch():
    manager = await _MediaManager.request_async()
    session = manager.get_current_session()
    if session is None:
        return None
    props = await session.try_get_media_properties_async()
    return {"title": props.title or "", "artist": props.artist or ""}


def get() -> dict | None:
    """Current track as {'title': ..., 'artist': ...}, or None."""
    if not _AVAILABLE:
        return None
    try:
        return asyncio.run(_fetch())
    except Exception:
        log.warning("Windows media session lookup failed", exc_info=True)
        return None
