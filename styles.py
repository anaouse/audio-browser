"""Botanical colour palette, procedural icon, and QSS stylesheets for Audio Browser."""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPalette, QPixmap

# ── Colour palette ──────────────────────────────────────────────────────────
MOSS_DARK = "#1C2416"  # deepest forest floor
MOSS_MID = "#2D3B22"   # window / panel background
MOSS_LEAF = "#3E5430"  # header / sidebar
FERN_GREEN = "#5A7A3A"  # accent / hover
LICHEN = "#8FAF6A"      # bright accent, highlighted text
CREAM = "#EDE8D5"       # primary text
PARCHMENT = "#C8BFA0"   # secondary text
GOLD_SPORE = "#B89A4A"  # folder colour
BARK = "#6B5B3E"        # subtle separator
DEWDROP = "#A8C8A0"     # currently playing tint


# ── Procedural leaf icon ────────────────────────────────────────────────────
def make_leaf_icon(size: int = 64) -> QIcon:
    """
    Draw a botanical leaf icon entirely in QPainter — no external assets needed.
    The leaf is a filled bezier shape with a centre vein and two side veins,
    sitting on a rounded dark-green background.
    """
    px = QPixmap(size, size)
    px.fill(Qt.GlobalColor.transparent)

    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    s = size
    # Rounded background pill
    bg_path = QPainterPath()
    bg_path.addRoundedRect(0, 0, s, s, s * 0.22, s * 0.22)
    p.fillPath(bg_path, QColor(MOSS_LEAF))

    # Leaf body
    # Coordinate system: origin top-left, leaf pointing upward-right
    cx = s * 0.50  # horizontal centre
    tip_y = s * 0.12  # leaf tip (top)
    base_y = s * 0.85  # leaf base (bottom)
    mid_y = (tip_y + base_y) / 2

    leaf = QPainterPath()
    leaf.moveTo(cx, tip_y)
    # right curve
    leaf.cubicTo(
        cx + s * 0.40,
        tip_y + s * 0.15,
        cx + s * 0.40,
        base_y - s * 0.15,
        cx,
        base_y,
    )
    # left curve
    leaf.cubicTo(
        cx - s * 0.40,
        base_y - s * 0.15,
        cx - s * 0.40,
        tip_y + s * 0.15,
        cx,
        tip_y,
    )

    p.fillPath(leaf, QColor(FERN_GREEN))

    # Subtle inner highlight (lighter left lobe)
    highlight = QPainterPath()
    highlight.moveTo(cx, tip_y)
    highlight.cubicTo(
        cx - s * 0.40,
        tip_y + s * 0.15,
        cx - s * 0.30,
        mid_y - s * 0.05,
        cx,
        mid_y,
    )
    highlight.cubicTo(
        cx - s * 0.10,
        mid_y - s * 0.10,
        cx - s * 0.10,
        tip_y + s * 0.10,
        cx,
        tip_y,
    )
    p.fillPath(highlight, QColor(LICHEN + "55"))  # semi-transparent lichen

    # Centre vein
    pen = p.pen()
    pen.setColor(QColor(MOSS_DARK))
    pen.setWidthF(s * 0.04)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    vein = QPainterPath()
    vein.moveTo(cx, tip_y + s * 0.04)
    vein.lineTo(cx, base_y - s * 0.04)
    p.drawPath(vein)

    # Side veins (3 pairs)
    pen.setWidthF(s * 0.025)
    p.setPen(pen)
    offsets = [0.28, 0.48, 0.65]  # fractional positions along the centre vein
    spread = s * 0.26
    for frac in offsets:
        vy = tip_y + (base_y - tip_y) * frac
        # right vein
        rv = QPainterPath()
        rv.moveTo(cx, vy)
        rv.quadTo(cx + spread * 0.6, vy - s * 0.04, cx + spread, vy - s * 0.06)
        p.drawPath(rv)
        # left vein
        lv = QPainterPath()
        lv.moveTo(cx, vy)
        lv.quadTo(cx - spread * 0.6, vy - s * 0.04, cx - spread, vy - s * 0.06)
        p.drawPath(lv)

    p.end()
    return QIcon(px)


# ── QSS stylesheets ─────────────────────────────────────────────────────────
def tree_stylesheet() -> str:
    """QSS for the main audio file tree widget (scrollbars, items, branches)."""
    return f"""
        QTreeWidget {{
            background-color: {MOSS_MID};
            color: {CREAM};
            border: none;
            outline: none;
            padding: 6px 4px;
            font-size: 13px;
        }}

        QTreeWidget::item {{
            padding: 5px 8px;
            border-radius: 6px;
            margin: 1px 4px;
            color: {PARCHMENT};
        }}

        QTreeWidget::item:hover {{
            background-color: {MOSS_LEAF};
            color: {CREAM};
        }}

        QTreeWidget::item:selected {{
            background-color: {FERN_GREEN};
            color: {CREAM};
        }}

        QTreeWidget::branch,
        QTreeWidget::branch:hover,
        QTreeWidget::branch:selected,
        QTreeWidget::branch:has-siblings,
        QTreeWidget::branch:!has-siblings,
        QTreeWidget::branch:has-siblings:adjoins-item,
        QTreeWidget::branch:has-siblings:!adjoins-item,
        QTreeWidget::branch:!has-siblings:adjoins-item,
        QTreeWidget::branch:!has-siblings:!adjoins-item,
        QTreeWidget::branch:open:has-children,
        QTreeWidget::branch:closed:has-children,
        QTreeWidget::branch:open:has-children:has-siblings,
        QTreeWidget::branch:closed:has-children:has-siblings {{
            background-color: {MOSS_MID};
            border-image: none;
            image: none;
        }}

        QScrollBar:vertical {{
            background: {MOSS_DARK};
            width: 8px;
            border-radius: 4px;
        }}
        QScrollBar::handle:vertical {{
            background: {FERN_GREEN};
            border-radius: 4px;
            min-height: 24px;
        }}
        QScrollBar::add-line:vertical,
        QScrollBar::sub-line:vertical {{
            height: 0px;
        }}
        QScrollBar:horizontal {{
            background: {MOSS_DARK};
            height: 8px;
            border-radius: 4px;
        }}
        QScrollBar::handle:horizontal {{
            background: {FERN_GREEN};
            border-radius: 4px;
        }}
    """


def status_stylesheet() -> str:
    """QSS for the status bar footer."""
    return f"""
        QFrame {{
            background-color: {MOSS_DARK};
            border-top: 1px solid {BARK};
        }}
        QLabel {{
            color: {LICHEN};
            font-size: 11px;
            padding: 0 12px;
        }}
    """


def window_stylesheet() -> str:
    """Global QSS for the main window and all descendant widgets."""
    return f"""
        QMainWindow {{
            background-color: {MOSS_MID};
        }}
        QWidget {{
            background-color: {MOSS_MID};
        }}
    """


def title_label_stylesheet() -> str:
    """QSS for the header title label."""
    return f"""
        QLabel {{
            color: {CREAM};
            font-size: 18px;
            font-weight: bold;
            letter-spacing: 2px;
            background: transparent;
        }}
    """


def subtitle_label_stylesheet() -> str:
    """QSS for the header subtitle label."""
    return f"""
        QLabel {{
            color: {LICHEN};
            font-size: 10px;
            letter-spacing: 3px;
            background: transparent;
        }}
    """


def tree_frame_stylesheet() -> str:
    """QSS for the inset frame surrounding the tree widget."""
    return f"""
        QFrame {{
            background-color: {MOSS_MID};
            border-left: 3px solid {MOSS_LEAF};
            margin: 8px 10px 4px 10px;
            border-radius: 4px;
        }}
    """


def dark_palette() -> QPalette:
    """Dark application palette so native widgets don't bleed light colours."""
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(MOSS_MID))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(CREAM))
    palette.setColor(QPalette.ColorRole.Base, QColor(MOSS_DARK))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(MOSS_LEAF))
    palette.setColor(QPalette.ColorRole.Text, QColor(CREAM))
    palette.setColor(QPalette.ColorRole.Button, QColor(MOSS_LEAF))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(CREAM))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(FERN_GREEN))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(CREAM))
    return palette
