"""Central application theme and stylesheet."""

# Color palette
PRIMARY = "#1976D2"       # Blue
PRIMARY_DARK = "#1565C0"
PRIMARY_LIGHT = "#BBDEFB"
ACCENT = "#43A047"        # Green
ACCENT_DARK = "#388E3C"
WARN = "#FB8C00"          # Orange
DANGER = "#E53935"        # Red
BG = "#FAFAFA"
CARD_BG = "#FFFFFF"
TEXT = "#212121"
TEXT_SECONDARY = "#757575"
BORDER = "#E0E0E0"
HOVER_BG = "#F5F5F5"

APP_STYLESHEET = f"""
/* ── Global ──────────────────────────────────────────── */

QMainWindow, QWidget {{
    background-color: {BG};
    color: {TEXT};
    font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
    font-size: 13px;
}}

/* ── Buttons ─────────────────────────────────────────── */

QPushButton {{
    background-color: {CARD_BG};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 10px 20px;
    font-size: 13px;
    font-weight: 500;
}}
QPushButton:hover {{
    background-color: {HOVER_BG};
    border-color: #BDBDBD;
}}
QPushButton:pressed {{
    background-color: #EEEEEE;
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
    padding: 8px 12px;
    font-size: 13px;
    selection-background-color: {PRIMARY_LIGHT};
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QDateEdit:focus {{
    border: 2px solid {PRIMARY};
    padding: 7px 11px;
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
    padding: 6px 10px;
}}
QHeaderView::section {{
    background-color: #F5F5F5;
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 8px 10px;
    font-weight: 600;
    font-size: 12px;
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
    font-size: 11px;
    border-top: 1px solid {BORDER};
    padding: 4px 12px;
}}

/* ── Labels ──────────────────────────────────────────── */

QLabel[cssClass="title"] {{
    font-size: 30px;
    font-weight: 700;
    color: {TEXT};
    letter-spacing: -0.5px;
}}
QLabel[cssClass="subtitle"] {{
    font-size: 14px;
    color: {TEXT_SECONDARY};
    font-weight: 400;
}}
QLabel[cssClass="section"] {{
    font-size: 12px;
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
    width: 8px;
}}
QScrollBar::handle:vertical {{
    background: #C0C0C0;
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
"""
