"""Test dashboard: all 5 tests as clean clickable cards with L/R indicators."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from storage.database import Patient
from ui.theme import (SZ, 
    PRIMARY, PRIMARY_LIGHT, ACCENT, BORDER, TEXT_SECONDARY)

TESTS = [
    ("finger_tapping", "Finger Tapping", "3.4", False, "Daumen-Zeigefinger"),
    ("hand_open_close", "Hand Öffnen/\nSchließen", "3.5", False, "Öffnen & Schließen"),
    ("pronation_supination", "Pronation/\nSupination", "3.6", False, "Unterarm drehen"),
    ("postural_tremor", "Posturaler\nTremor", "3.15", True, "Hände vorgestreckt"),
    ("rest_tremor", "Ruhetremor", "3.17", True, "Hände entspannt"),
    ("tower_of_hanoi", "Tuerme von\nHanoi", "Kogn.", False, "Scheiben verschieben"),
    ("spatial_srt", "Raeumliche\nReaktionszeit", "Kogn.", False, "Sequenz-Lernen"),
    ("trail_making_a", "Trail Making\nTeil A", "Kogn.", False, "Zahlen verbinden"),
    ("trail_making_b", "Trail Making\nTeil B", "Kogn.", False, "Zahlen & Buchstaben"),
]


class TestCard(QFrame):
    def __init__(self, test_key: str, label: str, updrs: str, bilateral: bool,
                 description: str, on_click) -> None:
        super().__init__()
        self.test_key = test_key
        self.bilateral = bilateral
        self._on_click = on_click
        self._completed = {"left": False, "right": False, "both": False}

        self.setFixedSize(SZ.CARD, SZ.CARD)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._apply_card_style()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 16, 14, 14)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        updrs_lbl = QLabel(updrs)
        updrs_lbl.setStyleSheet(f"font-size: 11px; color: {TEXT_SECONDARY}; font-weight: 600;")
        updrs_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(updrs_lbl)

        name_lbl = QLabel(label)
        name_lbl.setStyleSheet("font-size: 14px; font-weight: 700;")
        name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(name_lbl)

        desc_lbl = QLabel(description)
        desc_lbl.setStyleSheet(f"font-size: 11px; color: {TEXT_SECONDARY};")
        desc_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(desc_lbl)

        layout.addSpacing(6)

        # L / R indicators
        ind_row = QHBoxLayout()
        ind_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ind_row.setSpacing(6)

        if bilateral:
            self.both_ind = self._make_indicator("L + R")
            ind_row.addWidget(self.both_ind)
        else:
            self.left_ind = self._make_indicator("L")
            self.right_ind = self._make_indicator("R")
            ind_row.addWidget(self.left_ind)
            ind_row.addWidget(self.right_ind)

        layout.addLayout(ind_row)

    def _make_indicator(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setFixedHeight(SZ.INDICATOR_H)
        lbl.setMinimumWidth(40)
        self._style_indicator(lbl, False)
        return lbl

    @staticmethod
    def _style_indicator(lbl: QLabel, done: bool) -> None:
        if done:
            lbl.setStyleSheet(
                f"background-color: {ACCENT}; color: white; border-radius: 6px; "
                "padding: 4px 10px; font-size: 12px; font-weight: 700;"
            )
        else:
            lbl.setStyleSheet(
                f"background-color: #EEEEEE; color: #BDBDBD; border-radius: 6px; "
                "padding: 4px 10px; font-size: 12px; font-weight: 600;"
            )

    def _apply_card_style(self) -> None:
        all_done = (
            self._completed.get("both") if self.bilateral
            else self._completed.get("left") and self._completed.get("right")
        )
        any_done = any(self._completed.values())

        if all_done:
            border_c, bg = ACCENT, "#F1F8E9"
        elif any_done:
            border_c, bg = "#FFC107", "#FFFDE7"
        else:
            border_c, bg = BORDER, "#FFFFFF"

        self.setStyleSheet(
            f"TestCard {{ background: {bg}; border: 2px solid {border_c}; border-radius: 12px; }}"
            f"TestCard:hover {{ border-color: {PRIMARY}; }}"
        )

    def mark_completed(self, hand: str) -> None:
        self._completed[hand] = True
        if self.bilateral:
            self._style_indicator(self.both_ind, True)
        elif hand == "left":
            self._style_indicator(self.left_ind, True)
        elif hand == "right":
            self._style_indicator(self.right_ind, True)
        self._apply_card_style()

    def mousePressEvent(self, event) -> None:
        self._on_click(self.test_key, self.bilateral)


class TestDashboard(QWidget):
    def __init__(self, main_window) -> None:
        super().__init__()
        self.main_window = main_window
        self.cards: dict[str, TestCard] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 30, 40, 20)
        layout.setSpacing(16)

        # Patient bar
        self.patient_label = QLabel()
        self.patient_label.setProperty("cssClass", "patient-bar")
        self.patient_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.patient_label)

        # Duration
        dur_row = QHBoxLayout()
        dur_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dur_lbl = QLabel("Testdauer")
        dur_lbl.setProperty("cssClass", "section")
        dur_row.addWidget(dur_lbl)
        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(5, 60)
        self.duration_spin.setValue(10)
        self.duration_spin.setSuffix(" s")
        self.duration_spin.setFixedWidth(110)
        self.duration_spin.setFixedHeight(SZ.INPUT_H)
        dur_row.addWidget(self.duration_spin)
        layout.addLayout(dur_row)

        hint = QLabel("Test anklicken, dann Hand wählen")
        hint.setProperty("cssClass", "subtitle")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)

        # Cards grid
        grid = QGridLayout()
        grid.setSpacing(16)
        grid.setAlignment(Qt.AlignmentFlag.AlignCenter)

        for idx, (key, label, updrs, bilateral, desc) in enumerate(TESTS):
            card = TestCard(key, label, updrs, bilateral, desc, self._on_test_click)
            self.cards[key] = card
            grid.addWidget(card, idx // 3, idx % 3)

        layout.addLayout(grid)
        layout.addStretch()

        # Bottom
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        btn_row.addStretch()

        back_btn = QPushButton("← Zurueck")
        back_btn.setProperty("cssClass", "flat")
        back_btn.setFixedHeight(SZ.BTN_H)
        back_btn.clicked.connect(lambda: self.main_window.show_patient_detail())
        btn_row.addWidget(back_btn)

        close_btn = QPushButton("Session beenden")
        close_btn.setProperty("cssClass", "primary")
        close_btn.setFixedHeight(SZ.BTN_H)
        close_btn.clicked.connect(lambda: self.main_window.show_patient_detail())
        btn_row.addWidget(close_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

    def set_patient(self, patient) -> None:
        age_str = f"  |  {patient.age} J." if patient.age is not None else ""
        self.patient_label.setText(f"{patient.display_name}{age_str}")
        for card in self.cards.values():
            card._completed = {"left": False, "right": False, "both": False}
            if card.bilateral:
                card._style_indicator(card.both_ind, False)
            else:
                card._style_indicator(card.left_ind, False)
                card._style_indicator(card.right_ind, False)
            card._apply_card_style()

    def _on_test_click(self, test_key: str, bilateral: bool) -> None:
        if bilateral:
            self.main_window.start_test(test_key, "both", self.duration_spin.value())
        elif test_key in ("tower_of_hanoi", "spatial_srt", "trail_making_a", "trail_making_b"):
            # Hand is auto-detected during positioning phase
            self.main_window.start_test(test_key, "right", self.duration_spin.value())
        else:
            self._show_hand_picker(test_key)

    def _show_hand_picker(self, test_key: str) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Hand wählen")
        dlg.setFixedSize(360, 200)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        lbl = QLabel("Welche Hand?")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet("font-size: 16px; font-weight: 600;")
        layout.addWidget(lbl)

        row = QHBoxLayout()
        row.setSpacing(16)

        for hand, text in [("left", "Links"), ("right", "Rechts")]:
            btn = QPushButton(text)
            btn.setProperty("cssClass", "primary")
            btn.setFixedHeight(SZ.DIALOG_BTN_H)
            btn.clicked.connect(
                lambda _, h=hand: (
                    dlg.accept(),
                    self.main_window.start_test(test_key, h, self.duration_spin.value()),
                )
            )
            row.addWidget(btn)

        layout.addLayout(row)
        dlg.exec()
