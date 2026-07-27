# A placeholder for the Chats & Calls tray icon.
#
# The icon is in the mockup and in this app's tray, but the feature behind it
# hasn't been started. It previously did nothing at all when pressed — and an
# icon that produces no response is indistinguishable from a broken one, which
# is exactly the kind of thing a tester reports as a bug. Saying "not yet" is a
# response; silence isn't.
#
# Deliberately has no navigable rows: build_nav() returns None, so
# OverlayWindow pushes an empty RowList and Circle pops straight back out.
# Nothing here needs to be reworked when the real feature arrives — this file
# just gets replaced.

from PySide6.QtWidgets import QLabel

from panels.base import Panel


class ChatsPanel(Panel):
    def __init__(self):
        super().__init__("Chats & Calls")

        message = QLabel("This feature is still under construction.")
        message.setWordWrap(True)
        message.setStyleSheet("font-size: 17px; color: rgba(255,255,255,0.75);")
        self.body.addWidget(message)

        # There are no rows to move between here, so without this a user can
        # reasonably wonder whether they're stuck.
        hint = QLabel("Press Circle to go back.")
        hint.setWordWrap(True)
        hint.setStyleSheet("font-size: 14px; color: rgba(255,255,255,0.45);")
        self.body.addWidget(hint)

    def build_nav(self):
        # None means "no navigable rows" (see panels/base.py) — OverlayWindow
        # substitutes an empty RowList, which every RowList method guards for.
        return None
