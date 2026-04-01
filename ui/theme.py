"""Central application theme and stylesheet with dense/touch mode toggle."""

from types import SimpleNamespace

# ── Color Palette ───────────────────────────────────────────────
PRIMARY = "#1976D2"
PRIMARY_DARK = "#1565C0"
PRIMARY_LIGHT = "#BBDEFB"
ACCENT = "#43A047"
ACCENT_DARK = "#388E3C"
WARN = "#FB8C00"
DANGER = "#E53935"
BG = "#FAFAFA"
CARD_BG = "#FFFFFF"
TEXT = "#212121"
TEXT_SECONDARY = "#757575"
BORDER = "#E0E0E0"
HOVER_BG = "#F5F5F5"

# ── UI Size Profiles ───────────────────────────────────────────
_PROFILES = {
    "dense": {
        "MIN": 30, "ROW_H": 30, "BTN_H": 32, "INPUT_H": 32,
        "CARD": 155, "INDICATOR_H": 22, "DIALOG_BTN_H": 36,
        "FONT": 13, "FONT_SUB": 14, "FONT_SEC": 12,
        "BTN_PAD": "8px 16px", "INPUT_PAD": "8px 12px",
        "INPUT_PAD_FOCUS": "7px 11px",
        "TABLE_PAD": "6px 10px", "HEADER_PAD": "8px 10px", "HEADER_FONT": 12,
        "SCROLLBAR": 8, "SCROLLBAR_R": 4, "SCROLLBAR_MIN": 30,
        "STATUS_FONT": 11, "STATUS_PAD": "4px 12px",
    },
    "touch": {
        "MIN": 48, "ROW_H": 52, "BTN_H": 48, "INPUT_H": 48,
        "CARD": 180, "INDICATOR_H": 28, "DIALOG_BTN_H": 52,
        "FONT": 15, "FONT_SUB": 15, "FONT_SEC": 13,
        "BTN_PAD": "12px 24px", "INPUT_PAD": "10px 14px",
        "INPUT_PAD_FOCUS": "9px 13px",
        "TABLE_PAD": "10px 12px", "HEADER_PAD": "10px 14px", "HEADER_FONT": 13,
        "SCROLLBAR": 14, "SCROLLBAR_R": 7, "SCROLLBAR_MIN": 48,
        "STATUS_FONT": 13, "STATUS_PAD": "6px 16px",
    },
}

# Shared mutable sizing — import SZ once, always up-to-date
SZ = SimpleNamespace()
_ui_mode = "touch"
APP_STYLESHEET = ""


def current_ui_mode() -> str:
    return _ui_mode


def set_ui_mode(mode: str) -> None:
    """Switch UI profile and regenerate stylesheet."""
    global _ui_mode, APP_STYLESHEET
    if mode not in _PROFILES:
        return
    _ui_mode = mode
    for k, v in _PROFILES[mode].items():
        setattr(SZ, k, v)
    APP_STYLESHEET = _build_stylesheet()


def _build_stylesheet() -> str:
    return f"""
/* ── Global ──────────────────────────────────────────── */

QMainWindow, QWidget {{
    background-color: {BG};
    color: {TEXT};
    font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
    font-size: {SZ.FONT}px;
}}

/* ── Buttons ─────────────────────────────────────────── */

QPushButton {{
    background-color: {CARD_BG};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: {SZ.BTN_PAD};
    font-size: {SZ.FONT}px;
    font-weight: 500;
    min-height: {SZ.BTN_H}px;
}}
QPushButton:hover {{
    background-color: {HOVER_BG};
    border-color: #BDBDBD;
}}
QPushButton:pressed {{
    background-color: #E0E0E0;
}}
QPushButton:disabled {{
    color: #BDBDBD;
    background-color: #F5F5F5;
}}

QPushButton[cssClass="primary"] {{
    background-color: {PRIMARY};
    color: white;
    border: none;
    font-weight: 600;
}}
QPushButton[cssClass="primary"]:hover {{
    background-color: {PRIMARY_DARK};
}}

QPushButton[cssClass="accent"] {{
    background-color: {ACCENT};
    color: white;
    border: none;
    font-weight: 600;
}}
QPushButton[cssClass="accent"]:hover {{
    background-color: {ACCENT_DARK};
}}

QPushButton[cssClass="danger"] {{
    background-color: {DANGER};
    color: white;
    border: none;
}}

QPushButton[cssClass="flat"] {{
    background-color: transparent;
    border: none;
    color: {PRIMARY};
    font-weight: 600;
}}
QPushButton[cssClass="flat"]:hover {{
    background-color: {PRIMARY_LIGHT};
    border-radius: 8px;
}}

/* ── Inputs ──────────────────────────────────────────── */

QLineEdit, QSpinBox, QComboBox, QDateEdit {{
    background-color: {CARD_BG};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: {SZ.INPUT_PAD};
    font-size: {SZ.FONT}px;
    min-height: {SZ.INPUT_H}px;
    selection-background-color: {PRIMARY_LIGHT};
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QDateEdit:focus {{
    border: 2px solid {PRIMARY};
    padding: {SZ.INPUT_PAD_FOCUS};
}}

QComboBox::drop-down {{
    border: none;
    padding-right: 8px;
}}

/* ── Tables ──────────────────────────────────────────── */

QTableWidget {{
    background-color: {CARD_BG};
    border: 1px solid {BORDER};
    border-radius: 8px;
    gridline-color: #F0F0F0;
    selection-background-color: {PRIMARY_LIGHT};
}}
QTableWidget::item {{
    padding: {SZ.TABLE_PAD};
}}
QHeaderView::section {{
    background-color: #F5F5F5;
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: {SZ.HEADER_PAD};
    font-weight: 600;
    font-size: {SZ.HEADER_FONT}px;
    color: {TEXT_SECONDARY};
}}

/* ── List Widget ─────────────────────────────────────── */

QListWidget {{
    background-color: {CARD_BG};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 4px;
    outline: none;
}}
QListWidget::item {{
    padding: 10px 14px;
    border-radius: 6px;
    margin: 2px 4px;
}}
QListWidget::item:selected {{
    background-color: {PRIMARY_LIGHT};
    color: {TEXT};
}}
QListWidget::item:hover {{
    background-color: {HOVER_BG};
}}

/* ── Progress Bar ────────────────────────────────────── */

QProgressBar {{
    background-color: #E8E8E8;
    border: none;
    border-radius: 6px;
    height: 10px;
    text-align: center;
    font-size: 1px;
}}
QProgressBar::chunk {{
    background-color: {PRIMARY};
    border-radius: 6px;
}}

/* ── Status Bar ──────────────────────────────────────── */

QStatusBar {{
    background-color: #F5F5F5;
    color: {TEXT_SECONDARY};
    font-size: {SZ.STATUS_FONT}px;
    border-top: 1px solid {BORDER};
    padding: {SZ.STATUS_PAD};
}}

/* ── Labels ──────────────────────────────────────────── */

QLabel[cssClass="title"] {{
    font-size: 30px;
    font-weight: 700;
    color: {TEXT};
    letter-spacing: -0.5px;
}}
QLabel[cssClass="subtitle"] {{
    font-size: {SZ.FONT_SUB}px;
    color: {TEXT_SECONDARY};
    font-weight: 400;
}}
QLabel[cssClass="section"] {{
    font-size: {SZ.FONT_SEC}px;
    color: {TEXT_SECONDARY};
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}}
QLabel[cssClass="patient-bar"] {{
    font-size: 16px;
    font-weight: 600;
    color: {TEXT};
    background-color: {PRIMARY_LIGHT};
    border-radius: 8px;
    padding: 10px 20px;
}}
QLabel[cssClass="countdown"] {{
    font-size: 64px;
    font-weight: 700;
    color: {PRIMARY};
}}
QLabel[cssClass="recording"] {{
    font-size: 20px;
    font-weight: 600;
    color: {ACCENT};
}}
QLabel[cssClass="done"] {{
    font-size: 20px;
    font-weight: 600;
    color: {PRIMARY};
}}

/* ── Scroll Area ─────────────────────────────────────── */

QScrollBar:vertical {{
    background: transparent;
    width: {SZ.SCROLLBAR}px;
}}
QScrollBar::handle:vertical {{
    background: #C0C0C0;
    border-radius: {SZ.SCROLLBAR_R}px;
    min-height: {SZ.SCROLLBAR_MIN}px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: {SZ.SCROLLBAR}px;
}}
QScrollBar::handle:horizontal {{
    background: #C0C0C0;
    border-radius: {SZ.SCROLLBAR_R}px;
    min-width: {SZ.SCROLLBAR_MIN}px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
}}
"""


# ── Initialize default mode ─────────────────────────────────────
set_ui_mode("touch")
