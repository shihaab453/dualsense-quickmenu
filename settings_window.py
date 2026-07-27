# A normal desktop window for the things the controller overlay fundamentally
# can't do.
#
# Why this exists at all: the overlay is driven entirely by a D-pad, and two
# pieces of first-run setup can't be done that way. A Spotify client ID is 32
# hex characters — there is no sane way to type that with a D-pad — and adding
# a game means browsing the filesystem for an .exe. Both are one-time, at-the-
# desktop tasks, so they live here instead: opened from the tray icon, or by
# pressing Cross on the Music panel's "Set up Spotify" row.
#
# Nothing here pushes changes into the overlay. Every panel re-reads its data
# in build_nav(), which runs each time that panel is opened (see HANDOFF.md's
# panel lifecycle notes), so closing this window and reopening the panel picks
# up new settings on its own.

import os

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
)

import logs
import settings
import version
from actions import spotify_client as sp

log = logs.get(__name__)

_DASHBOARD_URL = "https://developer.spotify.com/dashboard"

# Spotify client IDs are 32 lowercase hex characters. Checking the shape
# catches the two mistakes people actually make — pasting the dashboard URL,
# or pasting the app's name — before they turn into a confusing OAuth failure
# several steps later.
_CLIENT_ID_LENGTH = 32

_STYLE = """
#root { background: #15151c; }
QLabel { color: white; font-family: 'Manrope', 'Segoe UI', sans-serif;
         font-size: 14px; }
QLabel#sectionTitle { font-size: 20px; font-weight: 700; }
QLabel#hint { color: rgba(255,255,255,0.55); font-size: 13px; }
QLabel#status { color: rgba(255,255,255,0.75); font-size: 13px; }
QFrame#card { background: rgba(255,255,255,0.04); border-radius: 12px;
              border: 1px solid rgba(255,255,255,0.08); }
QFrame#divider { background: rgba(255,255,255,0.08); }
QLineEdit { background: rgba(0,0,0,0.35); color: white; border-radius: 8px;
            border: 1px solid rgba(255,255,255,0.15); padding: 8px 10px;
            font-family: 'Consolas', monospace; font-size: 13px; }
QLineEdit:focus { border: 1px solid #3ddc97; }
QLineEdit[readOnly="true"] { color: rgba(255,255,255,0.7); }
QPushButton { background: rgba(255,255,255,0.10); color: white;
              border-radius: 8px; padding: 8px 16px; font-size: 13px;
              font-weight: 600; border: none; }
QPushButton:hover { background: rgba(255,255,255,0.16); }
QPushButton#primary { background: #3ddc97; color: #10231c; }
QPushButton#primary:hover { background: #55e6a8; }
QListWidget { background: rgba(0,0,0,0.30); color: white; border-radius: 8px;
              border: 1px solid rgba(255,255,255,0.12); font-size: 13px;
              padding: 4px; }
QListWidget::item { padding: 6px 8px; border-radius: 6px; }
QListWidget::item:selected { background: rgba(61,220,151,0.25); }
"""


def _divider() -> QFrame:
    line = QFrame()
    line.setObjectName("divider")
    line.setFixedHeight(1)
    return line


class SettingsWindow(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DualSense Quick Menu — Settings")
        # An #id selector, not `QDialog {...}` — QFileDialog and QInputDialog
        # are parented to this window, and a plain QDialog rule would cascade
        # into them and half-restyle their standard chrome.
        self.setObjectName("settingsDialog")
        self.setStyleSheet("#settingsDialog { background: #15151c; }")
        self.setMinimumWidth(640)

        # The content sits in a scroll area because the full explanation plus
        # the games list is taller than the usable height of a 1080p screen,
        # and this ships to machines whose resolution we don't know. Static
        # content built once in __init__, so none of the deferred-measurement
        # trouble in panels/base.py's fit_scroll_to_content applies here.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("background: transparent;")
        scroll.viewport().setStyleSheet("background: transparent;")
        outer.addWidget(scroll)

        # The stylesheet lives on this inner frame rather than on the dialog
        # itself, for the same cascade reason as above.
        self._root = QFrame()
        self._root.setObjectName("root")
        self._root.setStyleSheet(_STYLE)
        scroll.setWidget(self._root)

        lay = QVBoxLayout(self._root)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(14)

        self._build_spotify_section(lay)
        lay.addWidget(_divider())
        self._build_games_section(lay)
        lay.addWidget(_divider())
        self._build_troubleshooting_section(lay)

        close_row = QHBoxLayout()
        # Shown here so a tester can read the build off the screen when
        # reporting something, without digging through the log.
        version_label = QLabel(f"{version.APP_NAME} {version.VERSION}")
        version_label.setObjectName("hint")
        close_row.addWidget(version_label)
        close_row.addStretch(1)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        close_row.addWidget(close_button)
        lay.addLayout(close_row)

        # Open at the content's natural height, but never taller than the
        # screen it's opening on — the scroll area covers any shortfall.
        available = QGuiApplication.primaryScreen().availableGeometry()
        self.resize(680, min(self._root.sizeHint().height(), int(available.height() * 0.9)))

    # ---- Spotify ----

    def _build_spotify_section(self, lay: QVBoxLayout) -> None:
        title = QLabel("Spotify")
        title.setObjectName("sectionTitle")
        lay.addWidget(title)

        explainer = QLabel(
            "Browsing your songs needs a Spotify client ID of your own. "
            "Spotify only lets an app's own creator use it until the app is "
            "reviewed, so instead of sharing one ID, you make a free one that "
            "belongs to you. It takes about two minutes:"
        )
        explainer.setObjectName("hint")
        explainer.setWordWrap(True)
        lay.addWidget(explainer)

        steps = QLabel(
            "1. Open the Spotify developer dashboard and log in with your "
            "normal Spotify account.<br>"
            "2. Click <b>Create app</b>. Any name and description will do.<br>"
            "3. Paste the redirect URI below into the app's "
            "<b>Redirect URIs</b> box.<br>"
            "4. Tick <b>Web API</b>, save, then copy the app's "
            "<b>Client ID</b> and paste it below."
        )
        steps.setObjectName("hint")
        steps.setWordWrap(True)
        steps.setTextFormat(Qt.RichText)
        lay.addWidget(steps)

        dashboard_button = QPushButton("Open Spotify dashboard")
        dashboard_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(_DASHBOARD_URL))
        )
        dashboard_row = QHBoxLayout()
        dashboard_row.addWidget(dashboard_button)
        dashboard_row.addStretch(1)
        lay.addLayout(dashboard_row)

        card = QFrame()
        card.setObjectName("card")
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(14, 12, 14, 12)
        card_lay.setSpacing(8)

        card_lay.addWidget(QLabel("Redirect URI — copy this exactly"))
        redirect_field = QLineEdit(sp.REDIRECT_URI)
        redirect_field.setReadOnly(True)
        redirect_field.setCursorPosition(0)
        card_lay.addWidget(redirect_field)

        card_lay.addWidget(QLabel("Your Client ID"))
        self._client_id_field = QLineEdit()
        self._client_id_field.setPlaceholderText(
            "32 characters, e.g. 0a1b2c3d4e5f60718293a4b5c6d7e8f9"
        )
        self._client_id_field.returnPressed.connect(self._save_client_id)
        card_lay.addWidget(self._client_id_field)

        buttons = QHBoxLayout()
        save_button = QPushButton("Save")
        save_button.setObjectName("primary")
        save_button.clicked.connect(self._save_client_id)
        buttons.addWidget(save_button)
        self._logout_button = QPushButton("Log out of Spotify")
        self._logout_button.clicked.connect(self._log_out)
        buttons.addWidget(self._logout_button)
        buttons.addStretch(1)
        card_lay.addLayout(buttons)

        self._spotify_status = QLabel()
        self._spotify_status.setObjectName("status")
        self._spotify_status.setWordWrap(True)
        card_lay.addWidget(self._spotify_status)

        lay.addWidget(card)

    def _save_client_id(self) -> None:
        entered = self._client_id_field.text().strip()
        if entered and len(entered) != _CLIENT_ID_LENGTH:
            self._spotify_status.setText(
                f"That doesn't look like a client ID — it should be "
                f"{_CLIENT_ID_LENGTH} characters, but that was {len(entered)}. "
                "Make sure you copied the Client ID and not the app name or URL."
            )
            return

        if entered != settings.get_spotify_client_id():
            # A token issued by the old app is useless to the new one, so the
            # cached login has to go with it.
            sp.forget_login()
        settings.set_spotify_client_id(entered)
        self._refresh_spotify_status(
            saved_message="Saved. Open the Music panel to log in."
            if entered
            else "Client ID cleared."
        )

    def _log_out(self) -> None:
        sp.forget_login()
        self._refresh_spotify_status(saved_message="Logged out of Spotify.")

    def _refresh_spotify_status(self, saved_message: str = "") -> None:
        client_id = settings.get_spotify_client_id()
        self._client_id_field.setText(client_id)
        try:
            logged_in = sp.is_logged_in()
        except Exception:
            # A malformed token cache or a network hiccup shouldn't leave this
            # window blank — treat it as "not logged in" and say so.
            log.exception("Couldn't check Spotify login state for the settings window")
            logged_in = False

        if not client_id:
            state = "No client ID saved yet — Spotify browsing is off."
        elif logged_in:
            state = "Connected to Spotify."
        else:
            state = "Client ID saved, but not logged in yet. Open the Music panel to log in."

        self._logout_button.setEnabled(bool(client_id) and logged_in)
        self._spotify_status.setText(
            f"{saved_message} {state}".strip() if saved_message else state
        )

    # ---- Games ----

    def _build_games_section(self, lay: QVBoxLayout) -> None:
        title = QLabel("Task Switcher games")
        title.setObjectName("sectionTitle")
        lay.addWidget(title)

        hint = QLabel(
            "Games you add here show up in the Task Switcher panel. Point each "
            "one at the game's own .exe rather than a shortcut — that's what "
            "lets the overlay notice when a game is already running and list "
            "it under Recent."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        self._games_list = QListWidget()
        # Capped rather than free-growing: it's the only expanding widget here,
        # so without a maximum it swallows all the window's spare height and
        # leaves a mostly-empty box.
        self._games_list.setMinimumHeight(110)
        self._games_list.setMaximumHeight(150)
        self._games_list.currentRowChanged.connect(self._update_games_buttons)
        lay.addWidget(self._games_list)

        buttons = QHBoxLayout()
        add_button = QPushButton("Add game…")
        add_button.setObjectName("primary")
        add_button.clicked.connect(self._add_game)
        buttons.addWidget(add_button)
        self._remove_button = QPushButton("Remove selected")
        self._remove_button.clicked.connect(self._remove_game)
        buttons.addWidget(self._remove_button)
        buttons.addStretch(1)
        lay.addLayout(buttons)

    def _add_game(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose the game's .exe",
            "",
            "Programs (*.exe);;All files (*)",
        )
        if not path:
            return
        default_name = os.path.splitext(os.path.basename(path))[0]
        name, ok = QInputDialog.getText(
            self,
            "Game name",
            "Name to show in the Task Switcher:",
            QLineEdit.Normal,
            default_name,
        )
        if not ok or not name.strip():
            return
        games = settings.get_pinned_games()
        games.append({"name": name.strip(), "path": os.path.normpath(path)})
        settings.set_pinned_games(games)
        self._reload_games()

    def _remove_game(self) -> None:
        row = self._games_list.currentRow()
        games = settings.get_pinned_games()
        if 0 <= row < len(games):
            del games[row]
            settings.set_pinned_games(games)
            self._reload_games()

    def _reload_games(self) -> None:
        self._games_list.clear()
        for game in settings.get_pinned_games():
            item = QListWidgetItem(game.get("name") or "(unnamed)")
            item.setToolTip(game.get("path", ""))
            self._games_list.addItem(item)
        self._update_games_buttons()

    def _update_games_buttons(self, *_) -> None:
        # Takes *_ because it's connected to currentRowChanged, which emits the
        # new row index, as well as being called directly with no arguments.
        self._remove_button.setEnabled(self._games_list.currentRow() >= 0)

    # ---- troubleshooting ----

    def _build_troubleshooting_section(self, lay: QVBoxLayout) -> None:
        title = QLabel("Troubleshooting")
        title.setObjectName("sectionTitle")
        lay.addWidget(title)

        hint = QLabel(
            "When something doesn't work, the app records what went wrong in a "
            "log file. Nothing is sent anywhere automatically — but opening this "
            "folder and sending <b>log.txt</b> is the fastest way to get a "
            "problem diagnosed."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        hint.setTextFormat(Qt.RichText)
        lay.addWidget(hint)

        row = QHBoxLayout()
        open_button = QPushButton("Open log folder")
        open_button.clicked.connect(self._open_log_folder)
        row.addWidget(open_button)
        row.addStretch(1)
        lay.addLayout(row)

    def _open_log_folder(self) -> None:
        folder = os.path.dirname(logs.log_path())
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError:
            log.exception("Couldn't create the log folder %s", folder)
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    # ---- opening ----

    def reload(self) -> None:
        """Re-reads everything from disk. Called each time the window is
        opened, so it never shows state left over from a previous visit."""
        self._refresh_spotify_status()
        self._reload_games()


_window = None


def open_settings() -> SettingsWindow:
    """Shows the settings window, creating it on first use. Kept as a single
    reusable instance so repeated opens don't stack up windows — and so the
    module holds a reference, which a locally-created QDialog would lose to
    garbage collection the moment the caller returned."""
    global _window
    if _window is None:
        _window = SettingsWindow()
    _window.reload()
    _window.show()
    _window.raise_()
    _window.activateWindow()
    return _window
