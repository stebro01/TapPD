"""Pick what to film: a stored protocol, or a single paradigm.

Both answers come back as a ``video.protocol.Protocol`` — a single paradigm is
simply one of length 1 — so the caller has no second case to handle.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
)

from ui import theme


class ProtocolChooser(QDialog):
    def __init__(self, parent=None, *, single_only: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle("Einzelnes Paradigma" if single_only else "Protokoll aufnehmen")
        self.setMinimumWidth(480)

        from paradigms import registry
        from video.protocol import list_protocols

        self._protocols = [] if single_only else list_protocols()

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        self._rb_protocol = QRadioButton("Protokoll")
        self._proto_combo = QComboBox()
        for p in self._protocols:
            self._proto_combo.addItem(
                f"{p.name}  ({len(p.steps)} Schritte, {p.total_duration_s:g}s)", p.id)
        if not single_only:
            layout.addWidget(self._rb_protocol)
            layout.addWidget(self._proto_combo)
            if not self._protocols:
                self._proto_combo.addItem("Kein Protokoll gefunden", "")
                self._proto_combo.setEnabled(False)
                self._rb_protocol.setEnabled(False)

        self._rb_single = QRadioButton("Einzelnes Paradigma")
        if not single_only:
            layout.addWidget(self._rb_single)
        use_single = single_only or not self._protocols
        self._rb_single.setChecked(use_single)
        self._rb_protocol.setChecked(not use_single)

        row = QHBoxLayout()
        self._para_combo = QComboBox()
        for key in registry.all_keys():
            spec = registry.get(key)
            label = (spec.label or key).replace("\n", " ")
            # Interactive paradigms are tasks on screen, not something to film:
            # they run live. Say so in the list rather than in a later error.
            if spec.screen != registry.SCREEN_METRIC:
                label += "   (live am Bildschirm)"
            self._para_combo.addItem(label, key)
        row.addWidget(self._para_combo, 1)
        self._hand_combo = QComboBox()
        for label, value in (("rechts", "right"), ("links", "left"), ("beide", "both")):
            self._hand_combo.addItem(label, value)
        row.addWidget(self._hand_combo)
        self._dur = QSpinBox()
        self._dur.setRange(5, 120)
        self._dur.setValue(20)
        self._dur.setSuffix(" s")
        row.addWidget(self._dur)
        layout.addLayout(row)

        self._hint = QLabel()
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px;")
        layout.addWidget(self._hint)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        for w in (self._rb_protocol, self._rb_single):
            w.toggled.connect(self._sync)
        for w in (self._proto_combo, self._para_combo, self._hand_combo):
            w.currentIndexChanged.connect(self._sync)
        self._sync()

    def _sync(self, *_) -> None:
        use_protocol = self._rb_protocol.isChecked() and bool(self._protocols)
        self._proto_combo.setEnabled(use_protocol)
        for w in (self._para_combo, self._hand_combo, self._dur):
            w.setEnabled(not use_protocol)
        if not use_protocol and self.is_interactive:
            self._dur.setEnabled(False)          # the task decides its own duration
            self._hint.setText("Hinweis: läuft live am Bildschirm — wird nicht als "
                               "Video-Schritt aufgenommen.")
            return
        # Surface the loader's notes here rather than mid-session: "needs face
        # tracking" is something to know before the patient is sitting down.
        try:
            notes = [n.message for n in self.protocol().notes]
        except Exception as e:
            notes = [str(e)]
        self._hint.setText("Hinweis: " + "  ".join(notes) if notes else "")

    @property
    def uses_protocol(self) -> bool:
        return self._rb_protocol.isChecked() and bool(self._protocols)

    @property
    def is_interactive(self) -> bool:
        """The chosen single paradigm is a screen task (Hanoi, SRT, TMT, …)."""
        from paradigms import registry
        if self.uses_protocol:
            return False
        key = self._para_combo.currentData()
        return bool(key) and registry.get(key).screen != registry.SCREEN_METRIC

    def single_choice(self) -> tuple[str, str, float]:
        """(paradigm key, hand, duration_s) of the single-paradigm section."""
        return (self._para_combo.currentData(), self._hand_combo.currentData(),
                float(self._dur.value()))

    def protocol(self):
        """The chosen protocol (raises if it cannot be built)."""
        from video.protocol import load_protocol, protocol_for_paradigm

        if self._rb_protocol.isChecked() and self._protocols:
            return load_protocol(self._proto_combo.currentData())
        return protocol_for_paradigm(self._para_combo.currentData(),
                                     hand=self._hand_combo.currentData(),
                                     duration_s=float(self._dur.value()))
