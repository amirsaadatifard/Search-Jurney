"""Small built-in icon set.

Icons are simple 24x24 line drawings stored as SVG fragments and rendered in
the requested colour, so they follow the theme and need no image files.
"""

from __future__ import annotations

from typing import Final

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_SVG_TEMPLATE: Final = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
    'stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
)

_ICONS: Final[dict[str, str]] = {
    "dashboard": '<rect x="3.5" y="3.5" width="7" height="8" rx="1.5"/><rect x="13.5" y="3.5" width="7" height="5" rx="1.5"/>'
    '<rect x="13.5" y="11.5" width="7" height="9" rx="1.5"/><rect x="3.5" y="14.5" width="7" height="6" rx="1.5"/>',
    "scenarios": '<path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r="1.2"/><circle cx="4.5" cy="12" r="1.2"/>'
    '<circle cx="4.5" cy="18" r="1.2"/>',
    "keywords": '<path d="M9 4 7 20M17 4l-2 16M4 9h17M3 15h17"/>',
    "targets": '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r="0.8"/>',
    "proxies": '<circle cx="5" cy="12" r="2.3"/><circle cx="19" cy="6" r="2.3"/><circle cx="19" cy="18" r="2.3"/>'
    '<path d="M7.1 11 16.9 7M7.1 13l9.8 4"/>',
    "browser": '<rect x="3" y="4.5" width="18" height="15" rx="2"/><path d="M3 9h18M6.5 6.8h.01M9.5 6.8h.01"/>',
    "runner": '<circle cx="12" cy="12" r="9"/><path d="M10 8.5v7l5.5-3.5z"/>',
    "live": '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8.5 20h7M12 16v4"/>',
    "history": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.2 2"/>',
    "logs": '<path d="M6 3.5h8.5L19 8v12.5H6z"/><path d="M14 3.5V8h5M9 12.5h7M9 16h7"/>',
    "reports": '<path d="M4 20V4M4 20h16"/><path d="M8.5 16v-5M12.5 16V8M16.5 16v-3"/>',
    "settings": '<path d="M4 7h9M17 7h3M4 12h3M11 12h9M4 17h11M19 17h1"/><circle cx="15" cy="7" r="2"/>'
    '<circle cx="9" cy="12" r="2"/><circle cx="17" cy="17" r="2"/>',
    "about": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5M12 7.6h.01"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2.5M12 19v2.5M2.5 12H5M19 12h2.5M5.3 5.3l1.8 1.8M16.9 16.9l1.8 1.8'
    'M5.3 18.7l1.8-1.8M16.9 7.1l1.8-1.8"/>',
    "moon": '<path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z"/>',
    "system": '<rect x="3" y="4.5" width="18" height="12" rx="2"/><path d="M9 20h6"/>',
    "chevron_down": '<path d="m6 9 6 6 6-6"/>',
    "chevron_up": '<path d="m6 15 6-6 6 6"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4.5 4.5"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "edit": '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13.5 6.5 4 4"/>',
    "copy": '<rect x="8.5" y="8.5" width="11" height="11" rx="2"/><path d="M5 15.5V6.5a2 2 0 0 1 2-2h9"/>',
    "trash": '<path d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l1 12.5h9l1-12.5M10 11v5M14 11v5"/>',
    "play": '<path d="M7 4.5v15l12-7.5z"/>',
    "pause": '<path d="M8 5v14M16 5v14"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="1.5"/>',
    "refresh": '<path d="M20 5v5h-5"/><path d="M19.5 10A8 8 0 1 0 20 13.5"/>',
    "download": '<path d="M12 4v11M7.5 11l4.5 4.5 4.5-4.5M5 19.5h14"/>',
    "upload": '<path d="M12 15.5V4.5M7.5 8.5 12 4l4.5 4.5M5 19.5h14"/>',
    "check": '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    "alert": '<path d="M12 4 2.8 19.5h18.4z"/><path d="M12 10v4.5M12 17h.01"/>',
    "close": '<path d="M6 6l12 12M18 6 6 18"/>',
    "shield": '<path d="M12 3 4.5 6v5.5c0 4.6 3.1 8.1 7.5 9.5 4.4-1.4 7.5-4.9 7.5-9.5V6z"/><path d="m9 12 2.2 2.2L15 10.4"/>',
    "inbox": '<path d="M3.5 13 6 5h12l2.5 8v6h-17z"/><path d="M3.5 13H8l1 2.5h6l1-2.5h4.5"/>',
    "image": '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="m5 18 5-5 3.5 3.5 2.5-2.5 3.5 4"/>',
    "sparkles": '<path d="M10 4l1.6 4.4L16 10l-4.4 1.6L10 16l-1.6-4.4L4 10l4.4-1.6zM18 14l.8 2.2L21 17l-2.2.8L18 20l-.8-2.2L15 17l2.2-.8z"/>',
}


def icon_names() -> tuple[str, ...]:
    """Return the names of all available icons."""
    return tuple(_ICONS)


def make_pixmap(name: str, color: str, size: int = 18, device_pixel_ratio: float = 2.0) -> QPixmap:
    """Render icon ``name`` in ``color`` as a high-DPI pixmap of logical ``size``."""
    body = _ICONS.get(name, _ICONS["about"])
    svg = _SVG_TEMPLATE.format(color=color, body=body)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    physical = round(size * device_pixel_ratio)
    pixmap = QPixmap(QSize(physical, physical))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, physical, physical))
    painter.end()
    pixmap.setDevicePixelRatio(device_pixel_ratio)
    return pixmap


def make_icon(name: str, color: str, active_color: str | None = None, size: int = 18) -> QIcon:
    """Build a ``QIcon``; ``active_color`` is used for the checked/selected state."""
    icon = QIcon()
    icon.addPixmap(make_pixmap(name, color, size), QIcon.Mode.Normal, QIcon.State.Off)
    if active_color is not None:
        icon.addPixmap(make_pixmap(name, active_color, size), QIcon.Mode.Normal, QIcon.State.On)
    return icon


APP_ICON_COLOR: Final = "#2F62E0"
_APP_ICON_SIZES: Final = (16, 24, 32, 48, 64, 128, 256)


def make_app_image(size: int) -> QImage:
    """Render the application logo: a white target mark on a rounded accent tile."""
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(APP_ICON_COLOR))
    radius = size * 0.22
    painter.drawRoundedRect(QRectF(0, 0, size, size), radius, radius)
    svg = _SVG_TEMPLATE.format(color="#FFFFFF", body=_ICONS["targets"])
    inset = size * 0.16
    QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(
        painter, QRectF(inset, inset, size - 2 * inset, size - 2 * inset)
    )
    painter.end()
    return image


def make_app_icon() -> QIcon:
    """Window / taskbar icon in every common size."""
    icon = QIcon()
    for size in _APP_ICON_SIZES:
        icon.addPixmap(QPixmap.fromImage(make_app_image(size)))
    return icon
