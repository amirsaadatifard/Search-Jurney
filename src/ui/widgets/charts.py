"""Small, theme-aware charts drawn with QPainter.

Design rules (from the data-visualisation guidance used for this project):
one measure per chart and one axis; thin marks; hairline gridlines and recessive
axes; text in text colours, never in the series colour; a hover tooltip on every
mark; direct labels for the outcome bars; and a table view of the same data for
readers who cannot rely on colour or on the picture.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from src.ui.theme import Palette, ThemeManager

MIN_HEIGHT: Final = 190
BAR_MAX_WIDTH: Final = 28
BAR_RADIUS: Final = 4
LINE_WIDTH: Final = 2.0
MARKER_RADIUS: Final = 4.0  # 8 px marker
RING_WIDTH: Final = 2.0
GRID_TICKS: Final = 4
MAX_X_LABELS: Final = 7


@dataclass(frozen=True, slots=True)
class ChartColors:
    """Chart ink for one theme. Series colour is categorical slot 1 of the reference palette."""

    series: QColor
    surface: QColor
    grid: QColor
    axis: QColor
    muted: QColor
    text: QColor


def chart_colors(palette: Palette) -> ChartColors:
    if palette.is_dark:
        return ChartColors(
            *(
                QColor(c)
                for c in ("#3987e5", palette.surface, "#2c2c2a", "#383835", "#898781", "#c3c2b7")
            )
        )
    return ChartColors(
        *(
            QColor(c)
            for c in ("#2a78d6", palette.surface, "#e1e0d9", "#c3c2b7", "#898781", "#52514e")
        )
    )


def nice_ticks(maximum: float, count: int = GRID_TICKS, integer: bool = False) -> list[float]:
    """Round tick values from 0 up to a "nice" maximum that covers ``maximum``."""
    if maximum <= 0:
        return [0.0, 1.0]
    raw_step = maximum / count
    magnitude = 10 ** math.floor(math.log10(raw_step))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw_step)
    if integer:
        step = max(1.0, math.ceil(step))
    ticks, value = [0.0], 0.0
    while value < maximum - 1e-9:
        value += step
        ticks.append(round(value, 10))
    return ticks


def format_value(value: float, unit: str = "") -> str:
    text = f"{value:.0f}" if abs(value - round(value)) < 1e-9 else f"{value:.1f}"
    return f"{text}{unit}"


class _AxisChart(QWidget):
    """Shared plumbing: margins, axes, hover tracking, theme."""

    left, right, top, bottom = 40, 12, 12, 26

    def __init__(self, theme: ThemeManager, unit: str = "", integer: bool = False) -> None:
        super().__init__()
        self._theme = theme
        self.unit = unit
        self.integer = integer
        self.labels: list[str] = []
        self.values: list[float | None] = []
        self.hover_index: int | None = None
        self.empty_text = "No data yet"
        self.setMinimumHeight(MIN_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        theme.theme_changed.connect(lambda _palette: self.update())

    # -- data ------------------------------------------------------------------ #
    def set_data(self, labels: Sequence[str], values: Sequence[float | None]) -> None:
        self.labels, self.values = list(labels), list(values)
        self.hover_index = None
        self.update()

    def has_data(self) -> bool:
        return any(v is not None and v > 0 for v in self.values)

    def rows(self) -> list[tuple[str, str]]:
        """The same data as text rows (the accessible table view)."""
        return [
            (label, "—" if value is None else format_value(value, self.unit))
            for label, value in zip(self.labels, self.values, strict=True)
        ]

    def tooltip_text(self, index: int) -> str:
        value = self.values[index]
        shown = "no data" if value is None else format_value(value, self.unit)
        return f"{self.labels[index]}\n{shown}"

    # -- geometry ---------------------------------------------------------------- #
    def plot_rect(self) -> QRectF:
        return QRectF(
            self.left,
            self.top,
            self.width() - self.left - self.right,
            self.height() - self.top - self.bottom,
        )

    def _scale(self) -> tuple[list[float], float]:
        present = [v for v in self.values if v is not None]
        ticks = nice_ticks(max(present) if present else 0, integer=self.integer)
        return ticks, ticks[-1]

    def x_of(self, index: int) -> float:
        rect = self.plot_rect()
        step = rect.width() / max(len(self.values), 1)
        return rect.left() + (index + 0.5) * step

    def index_at(self, x: float) -> int | None:
        rect = self.plot_rect()
        if not self.values or not (rect.left() <= x <= rect.right()):
            return None
        step = rect.width() / len(self.values)
        return min(int((x - rect.left()) / step), len(self.values) - 1)

    # -- painting ------------------------------------------------------------------ #
    def paintEvent(self, event: QPaintEvent) -> None:
        colors = chart_colors(self._theme.palette)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont(self.font())
        font.setPointSizeF(8.5)
        painter.setFont(font)
        if not self.has_data():
            painter.setPen(colors.muted)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.empty_text)
            return
        ticks, top_value = self._scale()
        self._draw_grid(painter, colors, ticks, top_value)
        self.draw_marks(painter, colors, top_value)
        self._draw_x_labels(painter, colors)

    def _y_of(self, value: float, top_value: float) -> float:
        rect = self.plot_rect()
        return rect.bottom() - (value / top_value) * rect.height()

    def _draw_grid(
        self, painter: QPainter, colors: ChartColors, ticks: list[float], top_value: float
    ) -> None:
        rect = self.plot_rect()
        for tick in ticks:
            y = self._y_of(tick, top_value)
            painter.setPen(QPen(colors.axis if tick == 0 else colors.grid, 1))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.setPen(colors.muted)
            painter.drawText(
                QRectF(0, y - 8, self.left - 6, 16),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                format_value(tick),
            )

    def _draw_x_labels(self, painter: QPainter, colors: ChartColors) -> None:
        rect = self.plot_rect()
        stride = max(1, math.ceil(len(self.labels) / MAX_X_LABELS))
        painter.setPen(colors.muted)
        for index in range(0, len(self.labels), stride):
            painter.drawText(
                QRectF(self.x_of(index) - 30, rect.bottom() + 4, 60, 18),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                self.labels[index],
            )

    def draw_marks(self, painter: QPainter, colors: ChartColors, top_value: float) -> None:
        raise NotImplementedError

    # -- interaction ------------------------------------------------------------------- #
    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        index = self.index_at(event.position().x())
        if index != self.hover_index:
            self.hover_index = index
            self.update()
        if index is None:
            QToolTip.hideText()
        else:
            QToolTip.showText(event.globalPosition().toPoint(), self.tooltip_text(index), self)

    def leaveEvent(self, event: object) -> None:
        self.hover_index = None
        QToolTip.hideText()
        self.update()


class BarChart(_AxisChart):
    """One bar per period: thin, rounded at the data end, anchored to the baseline."""

    def draw_marks(self, painter: QPainter, colors: ChartColors, top_value: float) -> None:
        rect = self.plot_rect()
        step = rect.width() / len(self.values)
        width = min(step * 0.6, BAR_MAX_WIDTH)
        for index, value in enumerate(self.values):
            if self.hover_index == index:
                wash = QColor(colors.grid)
                wash.setAlpha(110)
                painter.fillRect(
                    QRectF(rect.left() + index * step, rect.top(), step, rect.height()), wash
                )
            if not value:
                continue
            height = rect.bottom() - self._y_of(value, top_value)
            bar = QRectF(self.x_of(index) - width / 2, rect.bottom() - height, width, height)
            painter.save()
            painter.setClipRect(
                QRectF(bar.left(), rect.top(), bar.width(), rect.bottom() - rect.top())
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colors.series)
            painter.drawRoundedRect(bar.adjusted(0, 0, 0, BAR_RADIUS), BAR_RADIUS, BAR_RADIUS)
            painter.restore()


class LineChart(_AxisChart):
    """A 2 px line with 8 px markers ringed in the surface colour; gaps where there is no data."""

    def has_data(self) -> bool:
        return any(v is not None for v in self.values)

    def draw_marks(self, painter: QPainter, colors: ChartColors, top_value: float) -> None:
        rect = self.plot_rect()
        pen = QPen(colors.series, LINE_WIDTH)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        previous: QPointF | None = None
        points: list[tuple[int, QPointF]] = []
        for index, value in enumerate(self.values):
            if value is None:
                previous = None
                continue
            point = QPointF(self.x_of(index), self._y_of(value, top_value))
            points.append((index, point))
            if previous is not None:
                painter.drawLine(previous, point)
            previous = point

        if self.hover_index is not None:
            painter.setPen(QPen(colors.axis, 1, Qt.PenStyle.DashLine))
            x = self.x_of(self.hover_index)
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        for index, point in points:
            radius = MARKER_RADIUS + (1.5 if index == self.hover_index else 0)
            painter.setPen(QPen(colors.surface, RING_WIDTH))
            painter.setBrush(colors.series)
            painter.drawEllipse(point, radius, radius)


@dataclass(frozen=True, slots=True)
class OutcomeBar:
    label: str
    count: int
    color: QColor


class OutcomeChart(QWidget):
    """Horizontal bars, one per outcome, each with a direct label, count and share.

    Status colours are fixed roles; the label and number repeat the meaning, so
    nothing depends on colour alone.
    """

    ROW_HEIGHT: Final = 30
    LABEL_WIDTH: Final = 112

    def __init__(self, theme: ThemeManager) -> None:
        super().__init__()
        self._theme = theme
        self.bars: list[OutcomeBar] = []
        self.setMinimumHeight(MIN_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        theme.theme_changed.connect(lambda _palette: self.update())

    def set_bars(self, bars: Sequence[OutcomeBar]) -> None:
        self.bars = list(bars)
        self.update()

    def total(self) -> int:
        return sum(bar.count for bar in self.bars)

    def rows(self) -> list[tuple[str, str]]:
        total = self.total() or 1
        return [(bar.label, f"{bar.count}  ({bar.count / total:.0%})") for bar in self.bars]

    def paintEvent(self, event: QPaintEvent) -> None:
        colors = chart_colors(self._theme.palette)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont(self.font())
        font.setPointSizeF(9.0)
        painter.setFont(font)
        if self.total() == 0:
            painter.setPen(colors.muted)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No finished sessions yet")
            return
        peak = max(bar.count for bar in self.bars) or 1
        value_width = 86
        track = self.width() - self.LABEL_WIDTH - value_width - 8
        top = max((self.height() - self.ROW_HEIGHT * len(self.bars)) / 2, 6)
        for row, bar in enumerate(self.bars):
            y = top + row * self.ROW_HEIGHT
            painter.setPen(colors.text)
            painter.drawText(
                QRectF(0, y, self.LABEL_WIDTH, self.ROW_HEIGHT),
                Qt.AlignmentFlag.AlignVCenter,
                bar.label,
            )
            length = track * bar.count / peak
            if bar.count:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(bar.color)
                painter.drawRoundedRect(
                    QRectF(self.LABEL_WIDTH, y + 8, max(length, 2), 14), BAR_RADIUS, BAR_RADIUS
                )
            share = bar.count / self.total()
            painter.setPen(colors.text)
            painter.drawText(
                QRectF(self.LABEL_WIDTH + length + 8, y, value_width, self.ROW_HEIGHT),
                Qt.AlignmentFlag.AlignVCenter,
                f"{bar.count}  ({share:.0%})",
            )
