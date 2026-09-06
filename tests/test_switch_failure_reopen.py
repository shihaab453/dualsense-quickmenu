"""A switch failure must still be readable when the user reopens the panel."""
import pytest
from PySide6.QtWidgets import QApplication, QLabel

pytestmark = pytest.mark.unit


def test_switch_error_survives_reopen_and_list_refresh(monkeypatch):
    from panels import appswitcher

    app = QApplication.instance() or QApplication([])
    jobs = []
    monkeypatch.setattr(appswitcher.SYSTEM, 'submit', jobs.append)
    monkeypatch.setattr(appswitcher.window_switcher, 'switch_to', lambda hwnd: False)
    monkeypatch.setattr(appswitcher.window_switcher, 'list_switchable_windows',
                        lambda: [{'hwnd': 2, 'title': 'Another window'}])
    panel = appswitcher.AppSwitcherPanel()
    panel._windows = [{'hwnd': 1, 'title': 'Closed window'}]
    panel.build_nav()
    panel.hide()
    panel._on_activate(0, panel._rows[0])

    def error_is_present():
        return any("Couldn't switch to Closed window" in label.text()
                   for label in panel._scroll.findChildren(QLabel))

    panel.build_nav()
    assert error_is_present(), 'The failure disappeared before reopening'
    while jobs:
        jobs.pop(0)()
    assert error_is_present(), 'The refresh erased the failure before it could be read'
    assert [row.window['hwnd'] for row in panel._rows] == [2]
    panel.build_nav()
    assert not error_is_present(), 'The next visit should start without the old failure'
    panel.close()
