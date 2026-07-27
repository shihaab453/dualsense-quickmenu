# Music control by simulating the media keys (⏮ ⏯ ⏭) found on keyboards.
# Windows routes these to whatever app is playing (Spotify, YouTube, ...), so
# we get universal music control without talking to any specific app's API.

import ctypes

_user32 = ctypes.windll.user32

_KEYEVENTF_KEYUP = 0x0002
_VK_MEDIA_NEXT_TRACK = 0xB0
_VK_MEDIA_PREV_TRACK = 0xB1
_VK_MEDIA_PLAY_PAUSE = 0xB3


def _tap(vk_code: int) -> None:
    # A key "tap" is a press event followed by a release event.
    _user32.keybd_event(vk_code, 0, 0, 0)
    _user32.keybd_event(vk_code, 0, _KEYEVENTF_KEYUP, 0)


def play_pause() -> None:
    _tap(_VK_MEDIA_PLAY_PAUSE)


def next_track() -> None:
    _tap(_VK_MEDIA_NEXT_TRACK)


def previous_track() -> None:
    _tap(_VK_MEDIA_PREV_TRACK)
