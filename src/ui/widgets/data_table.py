"""Sortable object table: a model, a badge delegate and a pre-configured view.

Pages describe their columns declaratively with :class:`Column` and hand the
table a list of objects; no page deals with model indexes.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QRectF,
    QSortFilterProxyModel,
    Qt,
    Signal,
)
from PySide6.QtGui import QBrush, QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QWidget,
)

from src.ui.theme import ThemeManager
from src.ui.widgets.status_badge import BadgeTone

SORT_ROLE = Qt.ItemDataRole.UserRole + 1
BADGE_ROLE = Qt.ItemDataRole.UserRole + 2
HIGHLIGHT_ROLE = Qt.ItemDataRole.UserRole + 3
ROW_HEIGHT = 40
_BADGE_HEIGHT = 20
_BADGE_PADDING = 9
_CELL_PADDING = 10

Index = QModelIndex | QPersistentModelIndex


@dataclass(frozen=True, slots=True)
class Column[T]:
    """One table column.

    Attributes:
        title: Header text.
        text: Returns the display text for a row object.
        sort_key: Returns the value to sort by (defaults to the display text).
        badge: If set, the cell is drawn as a coloured pill in the returned tone.
        width: Fixed initial width in pixels; ignored when ``stretch`` is set.
        stretch: Let this column absorb the remaining horizontal space.
    """

    title: str
    text: Callable[[T], str]
    sort_key: Callable[[T], Any] | None = None
    badge: Callable[[T], BadgeTone | None] | None = None
    width: int | None = None
    stretch: bool = False
    align_right: bool = False


class ObjectTableModel[T](QAbstractTableModel):
    """Read-only model exposing a list of objects through :class:`Column` getters."""

    def __init__(
        self,
        columns: Sequence[Column[T]],
        highlight: Callable[[T], bool] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._columns = tuple(columns)
        self._highlight = highlight
        self._rows: list[T] = []

    def set_rows(self, rows: Sequence[T]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def row_object(self, row: int) -> T:
        return self._rows[row]

    def rowCount(self, parent: Index = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: Index = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._columns)

    def data(self, index: Index, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        column = self._columns[index.column()]
        item = self._rows[index.row()]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return column.text(item)
        if role == SORT_ROLE:
            return column.sort_key(item) if column.sort_key else column.text(item)
        if role == BADGE_ROLE:
            tone = column.badge(item) if column.badge else None
            return tone.value if tone else None
        if role == HIGHLIGHT_ROLE:
            return bool(self._highlight and self._highlight(item))
        if role == Qt.ItemDataRole.FontRole and self._highlight and self._highlight(item):
            font = QFont()
            font.setBold(True)
            return font
        if role == Qt.ItemDataRole.TextAlignmentRole:
            horizontal = (
                Qt.AlignmentFlag.AlignRight if column.align_right else Qt.AlignmentFlag.AlignLeft
            )
            return int(horizontal | Qt.AlignmentFlag.AlignVCenter)
        return None

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self._columns[section].title
        return None


class _SortProxy(QSortFilterProxyModel):
    """Sorts by :data:`SORT_ROLE`; case-insensitive for text, ``None`` first."""

    def lessThan(self, left: Index, right: Index) -> bool:
        a, b = left.data(SORT_ROLE), right.data(SORT_ROLE)
        if a is None or b is None:
            return a is None and b is not None
        if isinstance(a, str) and isinstance(b, str):
            return a.casefold() < b.casefold()
        try:
            return bool(a < b)
        except TypeError:
            return str(a) < str(b)


class BadgeDelegate(QStyledItemDelegate):
    """Draws cells that carry a :data:`BADGE_ROLE` as rounded status pills."""

    def __init__(self, theme: ThemeManager, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._theme = theme

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: Index) -> None:
        tone = index.data(BADGE_ROLE)
        highlighted = bool(index.data(HIGHLIGHT_ROLE))
        widget = option.widget
        style = widget.style() if widget is not None else None
        if (tone is None and not highlighted) or style is None:
            super().paint(painter, option, index)
            return

        background = QStyleOptionViewItem(option)
        self.initStyleOption(background, index)
        if highlighted:  # emphasised rows (e.g. the target result): tinted and bold
            background.backgroundBrush = QBrush(QColor(self._theme.palette.success_bg))
            background.features &= ~QStyleOptionViewItem.ViewItemFeature.Alternate
        if tone is None:
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, background, painter, widget)
            return
        background.text = ""
        if style is not None:
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, background, painter, widget)

        text = str(index.data(Qt.ItemDataRole.DisplayRole))
        foreground, fill = self._theme.palette.tone(tone)
        font = painter.font()
        font.setPointSizeF(8.5)
        font.setBold(True)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(font)
        width = painter.fontMetrics().horizontalAdvance(text) + 2 * _BADGE_PADDING
        rect = QRectF(
            option.rect.left() + _CELL_PADDING,
            option.rect.center().y() - _BADGE_HEIGHT / 2 + 0.5,
            width,
            _BADGE_HEIGHT,
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(fill))
        painter.drawRoundedRect(rect, _BADGE_HEIGHT / 2, _BADGE_HEIGHT / 2)
        painter.setPen(QColor(foreground))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()


class DataTable[T](QTableView):
    """Table view with sorting, row selection and object-level signals."""

    object_activated = Signal(object)
    selection_changed = Signal()

    def __init__(
        self,
        theme: ThemeManager,
        columns: Sequence[Column[T]],
        parent: QWidget | None = None,
        highlight: Callable[[T], bool] | None = None,
    ) -> None:
        super().__init__(parent)
        self._model: ObjectTableModel[T] = ObjectTableModel(columns, highlight, self)
        self._proxy = _SortProxy(self)
        self._proxy.setSourceModel(self._model)
        self.setModel(self._proxy)
        self.setItemDelegate(BadgeDelegate(theme, self))

        self.setSortingEnabled(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)

        header = self.horizontalHeader()
        header.setHighlightSections(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSortIndicator(0, Qt.SortOrder.AscendingOrder)
        for position, column in enumerate(columns):
            if column.stretch:
                header.setSectionResizeMode(position, QHeaderView.ResizeMode.Stretch)
            elif column.width is not None:
                self.setColumnWidth(position, column.width)

        self.doubleClicked.connect(self._on_double_clicked)
        self.selectionModel().selectionChanged.connect(lambda *_: self.selection_changed.emit())
        theme.theme_changed.connect(lambda _palette: self.viewport().update())

    def set_rows(self, rows: Sequence[T]) -> None:
        """Replace the content, keeping the selection where objects share an ``id``."""
        selected_ids = {getattr(item, "id", None) for item in self.selected_objects()}
        self._model.set_rows(rows)
        selected_ids.discard(None)
        if selected_ids:
            for row in range(self._proxy.rowCount()):
                if getattr(self._object_at(row), "id", None) in selected_ids:
                    self.selectRow(row)
                    break
        self.selection_changed.emit()

    def keep_source_order(self) -> None:
        """Show rows in the order given (chronological data); header sorting is turned off."""
        self.setSortingEnabled(False)
        self._proxy.sort(-1)

    def row_count(self) -> int:
        return self._proxy.rowCount()

    def selected_objects(self) -> list[T]:
        rows = sorted({index.row() for index in self.selectionModel().selectedRows()})
        return [self._object_at(row) for row in rows]

    def object_at_position(self, row: int) -> T:
        """Return the object displayed in visual ``row`` (after sorting)."""
        return self._object_at(row)

    def cell_text(self, row: int, column: int) -> str:
        return str(self._proxy.index(row, column).data(Qt.ItemDataRole.DisplayRole))

    def _object_at(self, proxy_row: int) -> T:
        source = self._proxy.mapToSource(self._proxy.index(proxy_row, 0))
        return self._model.row_object(source.row())

    def _on_double_clicked(self, index: QModelIndex) -> None:
        self.object_activated.emit(self._object_at(index.row()))
