"""
styles.py  — shared light theme (white + blue ONLY) + reusable widgets
"""
import os
import sys
from PyQt6.QtWidgets import QWidget, QLabel, QGraphicsDropShadowEffect
from PyQt6.QtCore import Qt, QPropertyAnimation, pyqtProperty, pyqtSignal, QTimer, QEasingCurve
from PyQt6.QtGui import QPainter, QColor


def resource_path(*parts) -> str:
    """
    Resolve a path to a bundled resource (e.g. assets/logo.png) that works
    BOTH when running from source AND when running from a PyInstaller-built
    .exe (onefile or onedir).

    - Dev mode: resolves relative to this file's directory (the project root).
    - Frozen .exe: PyInstaller extracts/unpacks bundled data files (anything
      passed via --add-data) into sys._MEIPASS at runtime — that's the base
      directory to use, NOT the .exe's own folder and NOT __file__ (which
      points into a temp dir that isn't guaranteed to hold our data files
      unless we resolve it via _MEIPASS explicitly).
    """
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, *parts)

# ── Palette (blue + white only — different shades/tints carry meaning) ──────
BG_PAGE       = "#eef5fb"      # page background, very light blue
BG_WHITE      = "#ffffff"
BG_SIDEBAR    = "#0099DC"
BG_SIDEBAR_HV = "#007DB8"
BG_ROW_ALT    = "#f3f8fd"
ACCENT_BLUE   = "#0099DC"      # primary blue
ACCENT_BLUE2  = "#007DB8"      # darker blue (hover)
ACCENT_BLUE_DARK  = "#005a82"  # deepest blue (pressed / strong text)
ACCENT_BLUE_LIGHT = "#7fd1f5"  # light blue (soft accents)
ACCENT_BLUE_PALE  = "#dff1fb"  # pale blue tint (chip backgrounds)
# Back-compat aliases so existing "green/red/orange" call-sites still work —
# all resolve to a shade of blue, never a different hue.
ACCENT_GREEN  = ACCENT_BLUE_DARK
ACCENT_RED    = ACCENT_BLUE2
ACCENT_ORANGE = ACCENT_BLUE2
# Real red — reserved ONLY for destructive actions (Delete, Clear History).
DANGER_RED      = "#dc2626"
DANGER_RED_HOVER = "#b91c1c"
# Real green — reserved ONLY for the Approve action.
SUCCESS_GREEN       = "#16a34a"
SUCCESS_GREEN_HOVER = "#15803d"
TEXT_PRIMARY  = "#0b2231"
TEXT_MUTED    = "#5b7892"
TEXT_SIDEBAR  = "#ffffff"
TEXT_SIDEBAR_MUTED = "#cdeefb"
BORDER        = "#d7e8f5"
HEADER_BG     = "#eaf4fc"
TOGGLE_OFF    = "#c7dded"
TOGGLE_ON     = "#0099DC"


def apply_shadow(widget, blur=22, x=0, y=4, alpha=35, color="#0099DC"):
    """Attach a soft drop shadow to any widget (cards, tables, buttons)."""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(x, y)
    c = QColor(color)
    c.setAlpha(alpha)
    effect.setColor(c)
    widget.setGraphicsEffect(effect)
    return effect

APP_STYLESHEET = f"""
/* ── Base reset ─────────────────────────────────────────────────────── */
QWidget {{
    background-color: {BG_PAGE};
    color: {TEXT_PRIMARY};
    font-family: 'Segoe UI', 'Inter', sans-serif;
    font-size: 13px;
    font-weight: 400;
}}
QMainWindow, QDialog {{
    background-color: {BG_PAGE};
}}
QScrollArea, QScrollArea > QWidget > QWidget {{
    background-color: transparent;
    border: none;
}}

/* ── Scrollbars — thin, pill-shaped ─────────────────────────────────── */
QScrollBar:vertical {{
    background: transparent;
    width: 7px;
    margin: 2px 1px 2px 1px;
    border-radius: 3px;
}}
QScrollBar::handle:vertical {{
    background: {ACCENT_BLUE_LIGHT};
    border-radius: 3px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{
    background: {ACCENT_BLUE};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
QScrollBar:horizontal {{
    background: transparent;
    height: 7px;
    margin: 1px 2px 1px 2px;
    border-radius: 3px;
}}
QScrollBar::handle:horizontal {{
    background: {ACCENT_BLUE_LIGHT};
    border-radius: 3px;
    min-width: 28px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {ACCENT_BLUE};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: none; }}

/* ── Inputs ──────────────────────────────────────────────────────────── */
QLineEdit {{
    background-color: {BG_WHITE};
    border: 1.5px solid {BORDER};
    border-radius: 7px;
    padding: 8px 12px;
    color: {TEXT_PRIMARY};
    font-size: 13px;
    font-weight: 400;
    selection-background-color: {ACCENT_BLUE_PALE};
}}
QLineEdit:focus {{
    border-color: {ACCENT_BLUE};
    background-color: {BG_WHITE};
}}
QLineEdit:disabled {{
    background-color: {BG_ROW_ALT};
    color: {TEXT_MUTED};
    border-color: {BORDER};
}}
QLineEdit::placeholder {{
    color: {TEXT_MUTED};
}}

/* ── Buttons ─────────────────────────────────────────────────────────── */
QPushButton {{
    background-color: {ACCENT_BLUE};
    color: white;
    border: none;
    border-radius: 7px;
    padding: 8px 18px;
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 0.2px;
}}
QPushButton:hover {{ background-color: {ACCENT_BLUE2}; }}
QPushButton:pressed {{ background-color: #006FA3; }}
QPushButton:disabled {{
    background-color: {BORDER};
    color: {TEXT_MUTED};
}}
QPushButton#btnSecondary {{
    background-color: {BG_WHITE};
    color: {TEXT_PRIMARY};
    border: 1.5px solid {BORDER};
}}
QPushButton#btnSecondary:hover {{
    background-color: {BG_ROW_ALT};
    border-color: {ACCENT_BLUE};
    color: {ACCENT_BLUE};
}}
QPushButton#btnSecondary:disabled {{
    background-color: {BG_ROW_ALT};
    color: {TEXT_MUTED};
    border-color: {BORDER};
}}
QPushButton#btnDanger {{
    background-color: {BG_WHITE};
    color: {ACCENT_BLUE2};
    border: 1.5px solid {ACCENT_BLUE2};
}}
QPushButton#btnDanger:hover {{ background-color: {ACCENT_BLUE_PALE}; }}
QPushButton#btnSuccess {{
    background-color: {ACCENT_BLUE_DARK};
    color: white;
}}
QPushButton#btnSuccess:hover {{ background-color: #00465f; }}

/* ── Table ───────────────────────────────────────────────────────────── */
QTableWidget {{
    background-color: {BG_WHITE};
    border: 1.5px solid {BORDER};
    border-radius: 12px;
    gridline-color: {BORDER};
    selection-background-color: {ACCENT_BLUE_PALE};
    selection-color: {TEXT_PRIMARY};
    outline: none;
}}
QTableWidget::item {{
    padding: 7px 10px;
    border: none;
    color: {TEXT_PRIMARY};
}}
QTableWidget::item:alternate {{
    background-color: {BG_ROW_ALT};
}}
QTableWidget::item:selected {{
    background-color: {ACCENT_BLUE_PALE};
    color: {TEXT_PRIMARY};
}}
QHeaderView::section {{
    background-color: {HEADER_BG};
    color: {ACCENT_BLUE_DARK};
    font-size: 10.5px;
    font-weight: 700;
    letter-spacing: 0.7px;
    text-transform: uppercase;
    padding: 9px 10px;
    border: none;
    border-bottom: 2px solid {BORDER};
}}
QHeaderView::section:first {{
    border-top-left-radius: 10px;
}}
QHeaderView::section:last {{
    border-top-right-radius: 10px;
}}

/* ── Cards ───────────────────────────────────────────────────────────── */
QFrame#card {{
    background-color: {BG_WHITE};
    border: 1px solid {BORDER};
    border-radius: 14px;
}}
QFrame#sidebar {{
    background-color: {BG_SIDEBAR};
    border: none;
}}

/* ── Tabs ────────────────────────────────────────────────────────────── */
QTabWidget::pane {{
    border: 1.5px solid {BORDER};
    border-radius: 10px;
    background-color: {BG_WHITE};
    top: -1px;
}}
QTabBar::tab {{
    background-color: transparent;
    color: {TEXT_MUTED};
    padding: 10px 24px;
    border-bottom: 3px solid transparent;
    font-size: 13px;
    font-weight: 500;
}}
QTabBar::tab:selected {{
    color: {ACCENT_BLUE_DARK};
    border-bottom: 3px solid {ACCENT_BLUE};
    font-weight: 700;
}}
QTabBar::tab:hover {{ color: {ACCENT_BLUE2}; }}

/* ── ComboBox ────────────────────────────────────────────────────────── */
QComboBox {{
    background-color: {BG_WHITE};
    border: 1.5px solid {BORDER};
    border-radius: 7px;
    padding: 6px 10px;
    color: {TEXT_PRIMARY};
    font-size: 13px;
}}
QComboBox:focus {{ border-color: {ACCENT_BLUE}; }}
QComboBox:disabled {{
    background-color: {BG_ROW_ALT};
    color: {TEXT_MUTED};
}}
QComboBox QAbstractItemView {{
    background-color: {BG_WHITE};
    border: 1.5px solid {BORDER};
    border-radius: 7px;
    selection-background-color: {ACCENT_BLUE_PALE};
    color: {TEXT_PRIMARY};
    outline: none;
}}

/* ── TextEdit ────────────────────────────────────────────────────────── */
QTextEdit {{
    background-color: {BG_WHITE};
    border: 1.5px solid {BORDER};
    border-radius: 7px;
    padding: 8px;
    color: {TEXT_PRIMARY};
    font-size: 13px;
}}
QTextEdit:focus {{ border-color: {ACCENT_BLUE}; }}
QTextEdit:disabled {{
    background-color: {BG_ROW_ALT};
    color: {TEXT_MUTED};
}}

/* ── Misc ────────────────────────────────────────────────────────────── */
QMessageBox {{ background-color: {BG_WHITE}; }}
QToolTip {{
    background-color: {TEXT_PRIMARY};
    color: #ffffff;
    border: 1px solid {ACCENT_BLUE};
    border-radius: 6px;
    padding: 5px 9px;
    font-size: 12px;
    font-weight: 500;
}}
QCheckBox {{
    font-size: 13px;
    color: {TEXT_MUTED};
    font-weight: 500;
    spacing: 8px;
}}
QCheckBox:disabled {{ color: {BORDER}; }}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1.5px solid {BORDER};
    border-radius: 4px;
    background-color: {BG_WHITE};
}}
QCheckBox::indicator:checked {{
    background-color: {ACCENT_BLUE};
    border-color: {ACCENT_BLUE};
}}
"""

# ── Topbar gradient strip ─────────────────────────────────────────────────────
HEADER_BAR_STYLE = f"""
QFrame {{
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 {BG_SIDEBAR},
        stop:1 {ACCENT_BLUE2}
    );
    border: none;
    border-bottom: 1px solid {ACCENT_BLUE_DARK};
}}
"""


class ToggleSwitch(QWidget):
    """Animated iOS-style toggle switch."""
    toggled = pyqtSignal(bool)

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setFixedSize(46, 24)
        self._checked = checked
        self._thumb_x = 22 if checked else 2
        self._anim = QPropertyAnimation(self, b"thumb_x", self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

    def get_thumb_x(self): return self._thumb_x
    def set_thumb_x(self, v):
        self._thumb_x = v
        self.update()
    thumb_x = pyqtProperty(int, get_thumb_x, set_thumb_x)

    def isChecked(self): return self._checked

    def setChecked(self, val: bool):
        self._checked = val
        target = 22 if val else 2
        self._anim.setStartValue(self._thumb_x)
        self._anim.setEndValue(target)
        self._anim.start()
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self._checked)
            self.toggled.emit(self._checked)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        track_color = QColor(TOGGLE_ON) if self._checked else QColor(TOGGLE_OFF)
        p.setBrush(track_color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(0, 2, 46, 20, 10, 10)
        p.setBrush(QColor("white"))
        p.drawEllipse(self._thumb_x, 4, 18, 16)
        p.end()


def status_badge(text: str) -> QLabel:
    """Blue/white-only status pill.
    ON/APPROVED/ENABLED/ALLOWED  → solid ACCENT_BLUE_DARK bg, white text.
    PENDING                      → pale blue chip with ACCENT_BLUE2 text.
    OFF/REJECTED/DISABLED/BLOCKED/DENIED → white bg, TEXT_MUTED text, BORDER outline.
    """
    lbl = QLabel()
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    t = text.upper()

    if t in ("ON", "APPROVED", "ENABLED", "ALLOWED"):
        # Solid deep-blue pill — active/good state
        color, bg, border = "#ffffff", ACCENT_BLUE_DARK, ACCENT_BLUE_DARK
        display = text.capitalize()
    elif t == "PENDING":
        # Pale blue chip — waiting state
        color, bg, border = ACCENT_BLUE2, ACCENT_BLUE_PALE, ACCENT_BLUE_LIGHT
        display = "Pending"
    elif t in ("OFF", "REJECTED", "DISABLED", "EJECTED", "EJECT", "BLOCKED", "DENIED"):
        # White outlined pill — inactive/blocked state
        color, bg, border = TEXT_MUTED, BG_WHITE, BORDER
        display = text.capitalize()
    else:
        color, bg, border = TEXT_MUTED, BG_WHITE, BORDER
        display = text.capitalize()

    lbl.setText(display)
    lbl.setStyleSheet(f"""
        background-color: {bg};
        color: {color};
        border: 1.5px solid {border};
        border-radius: 10px;
        padding: 3px 12px;
        font-size: 11px;
        font-weight: 700;
        min-width: 68px;
    """)
    lbl.adjustSize()
    return lbl