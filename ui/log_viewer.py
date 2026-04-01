"""Elegant log viewer dialog for TapPD."""

import logging
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from logging_config import LOG_DIR, QtLogHandler
from ui.theme import (
    SZ,
    ACCENT,
    BG,
    BORDER,
    CARD_BG,
    DANGER,
    PRIMARY,
    PRIMARY_DARK,
    TEXT,
    TEXT_SECONDARY,
    WARN,
)

log = logging.getLogger(__name__)

# Level colors
LEVEL_COLORS = {
    "DEBUG": "#9E9E9E",
    "INFO": PRIMARY,
    "WARNING": WARN,
    "ERROR": DANGER,
    "CRITICAL": "#B71C1C",
}


class LogViewerDialog(QDialog):
    """Floating log viewer with live updates, filtering, and color-coded levels."""

    MAX_LINES = 2000

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("TapPD – Log Viewer")
        self.setMinimumSize(820, 520)
        self.resize(920, 580)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowMinMaxButtonsHint
        )
        self._line_count = 0
        self._auto_scroll = True
        self._filter_level = "DEBUG"

        self._build_ui()
        self._connect_signals()
        self._load_existing_logs()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Toolbar ────────────────────────────────────────────────
        toolbar = QWidget()
        toolbar.setStyleSheet(f"""
            QWidget {{
                background-color: {CARD_BG};
                border-bottom: 1px solid {BORDER};
            }}
        """)
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(16, 10, 16, 10)
        tb_layout.setSpacing(12)

        title = QLabel("Log Viewer")
        title.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {TEXT}; border: none;")
        tb_layout.addWidget(title)

        tb_layout.addStretch()

        # Filter
        filter_label = QLabel("Level:")
        filter_label.setStyleSheet(f"font-size: 12px; color: {TEXT_SECONDARY}; border: none;")
        tb_layout.addWidget(filter_label)

        self.level_combo = QComboBox()
        self.level_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        self.level_combo.setCurrentText("DEBUG")
        self.level_combo.setFixedWidth(130)
        self.level_combo.setFixedHeight(SZ.INPUT_H)
        self.level_combo.setStyleSheet("border: 1px solid #E0E0E0; border-radius: 6px;")
        tb_layout.addWidget(self.level_combo)

        # Line count
        self.count_label = QLabel("0 Einträge")
        self.count_label.setStyleSheet(f"font-size: 11px; color: {TEXT_SECONDARY}; border: none;")
        tb_layout.addWidget(self.count_label)

        # Auto-scroll toggle
        self.scroll_btn = QPushButton("Auto-Scroll: An")
        self.scroll_btn.setFixedWidth(150)
        self.scroll_btn.setFixedHeight(SZ.BTN_H)
        self.scroll_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {ACCENT};
                color: white;
                border: none;
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 12px;
                font-weight: 600;
                min-height: 0px;
            }}
            QPushButton:hover {{ background-color: #388E3C; }}
        """)
        tb_layout.addWidget(self.scroll_btn)

        # Clear
        clear_btn = QPushButton("Leeren")
        clear_btn.setFixedWidth(100)
        clear_btn.setFixedHeight(SZ.BTN_H)
        clear_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {DANGER};
                border: 1px solid {DANGER};
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 12px;
                font-weight: 600;
                min-height: 0px;
            }}
            QPushButton:hover {{ background-color: #FFEBEE; }}
        """)
        clear_btn.clicked.connect(self._on_clear)
        tb_layout.addWidget(clear_btn)

        layout.addWidget(toolbar)

        # ── Log Text ───────────────────────────────────────────────
        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setFont(QFont("Consolas", 10) if __import__("sys").platform == "win32"
                               else QFont("Menlo", 11))
        self.text_edit.setStyleSheet(f"""
            QTextEdit {{
                background-color: #1E1E1E;
                color: #D4D4D4;
                border: none;
                padding: 12px;
                selection-background-color: #264F78;
            }}
        """)
        layout.addWidget(self.text_edit)

        # ── Status Bar ─────────────────────────────────────────────
        status = QWidget()
        status.setStyleSheet(f"""
            QWidget {{
                background-color: #F5F5F5;
                border-top: 1px solid {BORDER};
            }}
        """)
        status_layout = QHBoxLayout(status)
        status_layout.setContentsMargins(16, 4, 16, 4)

        self.log_path_label = QLabel(f"Log-Datei: {LOG_DIR}")
        self.log_path_label.setStyleSheet(f"font-size: 10px; color: {TEXT_SECONDARY}; border: none;")
        status_layout.addWidget(self.log_path_label)

        status_layout.addStretch()

        layout.addWidget(status)

    def _connect_signals(self) -> None:
        QtLogHandler.get_signal().connect(self._on_log_message)
        self.level_combo.currentTextChanged.connect(self._on_filter_changed)
        self.scroll_btn.clicked.connect(self._toggle_auto_scroll)

    def _load_existing_logs(self) -> None:
        """Load last 200 lines from current log file."""
        log_file = LOG_DIR / "tappd.log"
        if not log_file.exists():
            return
        try:
            lines = log_file.read_text(encoding="utf-8").splitlines()
            for line in lines[-200:]:
                self._append_line(line)
        except Exception:
            pass

    def _on_log_message(self, message: str) -> None:
        self._append_line(message)

    def _append_line(self, line: str) -> None:
        # Check filter level
        level = self._extract_level(line)
        level_num = getattr(logging, level, 0)
        filter_num = getattr(logging, self._filter_level, 0)
        if level_num < filter_num:
            return

        # Trim old lines
        if self._line_count >= self.MAX_LINES:
            cursor = self.text_edit.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            cursor.movePosition(QTextCursor.MoveOperation.Down, QTextCursor.MoveMode.KeepAnchor, 100)
            cursor.removeSelectedText()
            self._line_count -= 100

        # Color-code the line
        color = LEVEL_COLORS.get(level, "#D4D4D4")
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))

        cursor = self.text_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(line + "\n", fmt)

        self._line_count += 1
        self.count_label.setText(f"{self._line_count} Einträge")

        if self._auto_scroll:
            scrollbar = self.text_edit.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    @staticmethod
    def _extract_level(line: str) -> str:
        """Extract log level from formatted line."""
        for level in ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"):
            if f"| {level}" in line:
                return level
        return "DEBUG"

    def _on_filter_changed(self, level: str) -> None:
        self._filter_level = level

    def _toggle_auto_scroll(self) -> None:
        self._auto_scroll = not self._auto_scroll
        if self._auto_scroll:
            self.scroll_btn.setText("Auto-Scroll: An")
            self.scroll_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {ACCENT};
                    color: white;
                    border: none;
                    border-radius: 6px;
                    padding: 6px 12px;
                    font-size: 11px;
                    font-weight: 600;
                }}
                QPushButton:hover {{ background-color: #388E3C; }}
            """)
            scrollbar = self.text_edit.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
        else:
            self.scroll_btn.setText("Auto-Scroll: Aus")
            self.scroll_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {TEXT_SECONDARY};
                    color: white;
                    border: none;
                    border-radius: 6px;
                    padding: 6px 12px;
                    font-size: 11px;
                    font-weight: 600;
                }}
                QPushButton:hover {{ background-color: #616161; }}
            """)

    def _on_clear(self) -> None:
        self.text_edit.clear()
        self._line_count = 0
        self.count_label.setText("0 Einträge")
