# Generic D-pad navigation, shared by every panel instead of each one
# hand-rolling its own "which row is selected" bookkeeping.
#
#   RowList  — one selectable list (the tray, a panel's rows, a submenu's
#              options). Owns the selected index and applies the visual
#              "selected" state to whichever row widget currently has it.
#   NavStack — the single active navigation context, owned by OverlayWindow.
#              Opening a panel/submenu pushes a new RowList on top; Circle
#              pops one level back (closing just that submenu) rather than
#              the whole overlay, until the stack empties.


class RowList:
    """A vertical or horizontal list of selectable rows.

    `rows` is a list of widgets, each of which must have a `set_selected(bool)`
    method (see overlay.py's `_Tile` / panel row widgets). `on_activate(index,
    row)` fires when Cross is pressed on the current selection.
    """

    def __init__(self, rows, on_activate=None, on_select=None, wrap=False,
                 orientation="vertical", name=None, on_enter=None):
        self.rows = rows
        self.on_activate = on_activate
        self.on_select = on_select
        self.wrap = wrap
        # "vertical" lists respond to up/down (most panels); "horizontal"
        # ones respond to left/right (the tray, and Phase A's media tiles).
        self.orientation = orientation
        # Lets OverlayWindow figure out what a stack level *is* after a pop
        # (e.g. "tray" or "cards") instead of tracking that separately and
        # risking it drifting out of sync with the actual stack.
        self.name = name
        # Fires whenever this level becomes the active one — on the initial
        # push AND every time Circle pops back up to it — so a panel with
        # multiple internal views (e.g. Music's Library/Songs/Detail) can
        # show the right widgets without needing its own separate tracking.
        self.on_enter = on_enter
        self.index = 0
        # Row widgets are often reused across multiple RowLists (e.g. the
        # tray tiles persist for the app's whole lifetime) — reset every row
        # first so whichever one was left selected from a previous RowList
        # doesn't stay stuck showing as selected.
        for row in self.rows:
            row.set_selected(False)
        if self.rows:
            self.rows[0].set_selected(True)
            if self.on_select:
                self.on_select(0, self.rows[0])

    def move(self, delta: int) -> None:
        if not self.rows:
            return
        new_index = self.index + delta
        if self.wrap:
            new_index %= len(self.rows)
        else:
            new_index = max(0, min(len(self.rows) - 1, new_index))
        if new_index == self.index:
            return
        self.rows[self.index].set_selected(False)
        self.index = new_index
        self.rows[self.index].set_selected(True)
        if self.on_select:
            self.on_select(self.index, self.rows[self.index])

    def reselect(self, index: int) -> None:
        """Point the selection at an absolute row index, assuming whatever was
        selected before is *gone* — the paged lists in Music append a page to
        the RowList they're already showing and delete the "Load more" row
        that was selected, so there's no previous row left to deselect (and
        touching a deleted widget would raise). Rebuilding the RowList instead
        would restyle every row, which is what made paging a long list get
        slower with every page."""
        if not self.rows:
            return
        self.index = max(0, min(len(self.rows) - 1, index))
        row = self.rows[self.index]
        row.set_selected(True)
        if self.on_select:
            self.on_select(self.index, row)

    def activate(self) -> None:
        if self.rows and self.on_activate:
            self.on_activate(self.index, self.rows[self.index])

    def adjust(self, delta: int) -> None:
        """Forward a left/right press to the selected row's own `adjust`
        method, if it has one (e.g. a volume slider row). No-op otherwise."""
        row = self.selected_row()
        if row is not None and hasattr(row, "adjust"):
            row.adjust(delta)

    def selected_row(self):
        return self.rows[self.index] if self.rows else None


class NavStack:
    """The single stack of active RowLists. Top of stack = what D-pad drives."""

    def __init__(self):
        self._stack = []

    def push(self, row_list: RowList) -> None:
        self._stack.append(row_list)
        if row_list.on_enter:
            row_list.on_enter()

    def pop(self) -> bool:
        """Pop one level. Returns True if a context is still left underneath."""
        if self._stack:
            self._stack.pop()
        return bool(self._stack)

    def clear(self) -> None:
        self._stack.clear()

    def current(self) -> RowList | None:
        return self._stack[-1] if self._stack else None

    def depth(self) -> int:
        return len(self._stack)
