"""Main application window with the full flow:
   Patient Screen → Patient Detail → New Session → Test Dashboard → Test → Results
"""

import logging

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QWidget,
)

log = logging.getLogger(__name__)

from capture.base_capture import BaseCaptureDevice
from capture.mock_capture import MockCaptureDevice
from motor_tests.base_test import BaseMotorTest
from motor_tests.finger_tapping import FingerTappingTest
from motor_tests.hand_open_close import HandOpenCloseTest
from motor_tests.pronation_supination import PronationSupinationTest
from motor_tests.tremor import PosturalTremorTest
from motor_tests.rest_tremor import RestTremorTest
from motor_tests.tower_of_hanoi import TowerOfHanoiTest
from motor_tests.spatial_srt import SpatialSRTTest
from motor_tests.trail_making import TrailMakingTest
from storage.database import (
    Measurement, Patient, Session,
    create_session, get_db, save_measurement, update_raw_data_path,
)
from ui.patient_screen import PatientScreen
from ui.patient_detail_screen import PatientDetailScreen
from ui.test_dashboard import TestDashboard
from ui.test_screen import TestScreen
from ui.hanoi_screen import HanoiScreen
from ui.srt_screen import SRTScreen
from ui.tmt_screen import TMTScreen
from ui.results_screen import ResultsScreen, save_raw_data
from ui.log_viewer import LogViewerDialog
from ui import theme
from ui.theme import SZ


class _SensorCheckWorker(QThread):
    """Background thread for non-blocking sensor check."""
    finished = pyqtSignal(bool, str)  # (device_present, error_msg)

    def __init__(self, capture_device, parent=None):
        super().__init__(parent)
        self._device = capture_device

    def run(self):
        try:
            if hasattr(self._device, 'check_device_present'):
                present = self._device.check_device_present()
            else:
                present = self._device.is_connected()
            self.finished.emit(present, "")
        except Exception as e:
            self.finished.emit(False, str(e))


TEST_CLASSES = {
    "finger_tapping": FingerTappingTest,
    "hand_open_close": HandOpenCloseTest,
    "pronation_supination": PronationSupinationTest,
    "postural_tremor": PosturalTremorTest,
    "rest_tremor": RestTremorTest,
    "tower_of_hanoi": TowerOfHanoiTest,
    "spatial_srt": SpatialSRTTest,
    "trail_making_a": TrailMakingTest,
    "trail_making_b": TrailMakingTest,
}

MOCK_MODES = {
    "finger_tapping": "tapping",
    "hand_open_close": "open_close",
    "pronation_supination": "pronation_supination",
    "postural_tremor": "postural_tremor",
    "rest_tremor": "rest_tremor",
    "tower_of_hanoi": "tower_of_hanoi",
    "spatial_srt": "spatial_srt",
    "trail_making_a": "trail_making",
    "trail_making_b": "trail_making",
}


class TapPDMainWindow(QMainWindow):
    def __init__(self, capture_device: BaseCaptureDevice) -> None:
        super().__init__()
        self.capture_device = capture_device
        self.current_patient: Patient | None = None
        self.current_session: Session | None = None
        self.setWindowTitle("TapPD – Motorik-Analyse")
        self.setMinimumSize(950, 720)

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self._build_screens()

        # Status bar: [dot + sensor text (left, clickable)] ... [Log button (right)]
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)

        # Sensor indicator in left area (replaces showMessage)
        self._sensor_widget = QWidget()
        sensor_layout = QHBoxLayout(self._sensor_widget)
        sensor_layout.setContentsMargins(4, 0, 12, 0)
        sensor_layout.setSpacing(7)

        self._sensor_dot = QLabel()
        self._sensor_dot.setFixedSize(16, 16)
        sensor_layout.addWidget(self._sensor_dot)

        self._sensor_label = QLabel()
        self._sensor_label.setStyleSheet("font-size: 13px; color: #757575; border: none;")
        sensor_layout.addWidget(self._sensor_label)

        self._sensor_widget.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sensor_widget.setMinimumHeight(SZ.MIN)
        self._sensor_widget.mousePressEvent = lambda e: self._check_sensor_status()
        self._sensor_widget.setToolTip("Klicken um Sensor-Status zu prüfen")
        self._status_bar.addWidget(self._sensor_widget, 1)  # left side, stretch

        # Log button (right side)
        self._log_btn = QPushButton("  Log  ")
        self._log_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #757575; border: 1px solid #E0E0E0; "
            "border-radius: 4px; padding: 8px 16px; font-size: 13px; font-weight: 600; min-height: 36px; }"
            "QPushButton:hover { background: #F5F5F5; color: #1976D2; border-color: #1976D2; }"
        )
        self._log_btn.clicked.connect(self._show_log_viewer)
        self._status_bar.addPermanentWidget(self._log_btn)

        self._log_viewer: LogViewerDialog | None = None
        self._update_status_bar()

        self.stack.setCurrentWidget(self.patient_screen)
        self._check_sensor_on_start()

    def _update_status_bar(self) -> None:
        is_mock = isinstance(self.capture_device, MockCaptureDevice)
        if is_mock:
            issues = getattr(self.capture_device, "_sensor_issues", None)
            if issues:
                self._set_sensor_indicator(False, "Sensor nicht verbunden – Simulationsmodus")
            else:
                self._set_sensor_indicator(False, "Simulationsmodus (--mock)")
        else:
            connected = self.capture_device.is_connected()
            device_name = type(self.capture_device).__name__
            if connected:
                self._set_sensor_indicator(True, f"Leap Motion Controller verbunden")
            else:
                self._set_sensor_indicator(False, f"Leap Motion Controller getrennt")

    def _set_sensor_indicator(self, connected: bool, label: str) -> None:
        color = "#43A047" if connected else "#E53935"
        self._sensor_dot.setStyleSheet(
            f"background-color: {color}; border-radius: 8px; border: none;"
        )
        self._sensor_label.setText(label)

    def _check_sensor_status(self) -> None:
        """Re-check sensor connection and show detailed diagnostics on click."""
        log.info("Sensor-Status wird geprüft...")
        is_mock = isinstance(self.capture_device, MockCaptureDevice)

        if is_mock:
            # Try to connect a real Leap device
            try:
                from capture.leap_capture import LeapCaptureDevice
                test_device = LeapCaptureDevice()
                test_device.connect()
                self.capture_device = test_device
                self._update_status_bar()
                log.info("Leap Controller gefunden! Wechsel von Mock auf LeapCaptureDevice")
                QMessageBox.information(
                    self, "Sensor erkannt",
                    "Leap Motion Controller erfolgreich verbunden!\n"
                    "Der Simulationsmodus wurde deaktiviert."
                )
                return
            except Exception as e:
                log.warning("Sensor-Check: Leap nicht verfügbar (%s)", e)

            # Show detailed diagnostics
            from capture import diagnose_sensor
            issues = diagnose_sensor()
            self._show_sensor_diagnostics(issues, str(e))
            self._update_status_bar()
            return

        # Real device: run check in background thread to avoid UI freeze
        self._sensor_label.setText("Prüfe Sensor...")
        self._sensor_check_worker = _SensorCheckWorker(self.capture_device, self)
        self._sensor_check_worker.finished.connect(self._on_sensor_check_done)
        self._sensor_check_worker.start()

    def _on_sensor_check_done(self, device_present: bool, error: str) -> None:
        """Handle result from background sensor check."""
        if device_present and not self.capture_device.is_connected():
            # USB device is back but connection was lost — reconnect
            log.info("Sensor-Check: USB-Gerät vorhanden, reconnecte...")
            try:
                self.capture_device.disconnect()
                self.capture_device.connect()
                self._update_status_bar()
                log.info("Sensor-Check: Reconnect erfolgreich")
                QMessageBox.information(self, "Sensor-Status", "Verbindung wiederhergestellt!")
                return
            except Exception as e:
                log.error("Sensor-Check: Reconnect fehlgeschlagen: %s", e)
                from capture import diagnose_sensor
                issues = diagnose_sensor()
                self._show_sensor_diagnostics(issues, str(e))
                self._update_status_bar()
                return

        self._update_status_bar()
        if device_present:
            log.info("Sensor-Check: Verbunden und aktiv")
            QMessageBox.information(
                self, "Sensor-Status",
                "Leap Motion Controller ist verbunden und betriebsbereit."
            )
        else:
            log.warning("Sensor-Check: Gerät nicht erreichbar, versuche Reconnect...")
            from capture import diagnose_sensor
            issues = diagnose_sensor()
            self._show_sensor_diagnostics(issues, error)
            self._update_status_bar()

    def _show_sensor_diagnostics(self, issues: list[str], error: str) -> None:
        """Show a detailed sensor diagnostic dialog."""
        log.info("Zeige Sensor-Diagnose (%d Probleme gefunden)", len(issues))
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setWindowTitle("Sensor-Diagnose")
        msg.setText("Der Leap Motion Controller konnte nicht verbunden werden.")

        # Build checklist
        info_parts = [
            "Checkliste:",
            "",
            "1. Ultraleap Hand Tracking Software installiert?",
            "   -> Download: ultraleap.com/downloads/leap-controller/",
            "",
            "2. Tracking-Service (LeapSvc) gestartet?",
            "   -> Windows: Dienste-Manager prüfen",
            "   -> Oder Ultraleap Control Panel öffnen",
            "",
            "3. Controller per USB angeschlossen?",
            "   -> LED am Controller sollte grün leuchten",
            "   -> Anderes USB-Kabel / anderen Port versuchen",
            "",
            "4. Nur eine App-Instanz gleichzeitig?",
            "   -> LeapC erlaubt nur eine aktive Verbindung",
        ]
        msg.setInformativeText("\n".join(info_parts))

        # Detailed diagnostic results
        detail_parts = []
        if error:
            detail_parts.append(f"Fehler: {error}")
            detail_parts.append("")
        if issues:
            detail_parts.append("Automatische Diagnose:")
            for i, issue in enumerate(issues, 1):
                detail_parts.append(f"\n{i}. {issue}")
        else:
            detail_parts.append("Automatische Diagnose: Keine spezifischen Probleme erkannt.")
            detail_parts.append("Möglicherweise ist der Treiber installiert aber der Controller nicht angeschlossen.")

        msg.setDetailedText("\n".join(detail_parts))
        msg.exec()

    def _check_sensor_on_start(self) -> None:
        if not isinstance(self.capture_device, MockCaptureDevice):
            return
        issues = getattr(self.capture_device, "_sensor_issues", None)
        if not issues:
            return
        detail_text = "\n\n".join(f"• {issue}" for issue in issues)
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setWindowTitle("Sensor nicht erkannt")
        msg.setText(
            "Der Leap Motion Controller konnte nicht verbunden werden.\n"
            "Die App läuft im Simulationsmodus."
        )
        msg.setDetailedText(detail_text)
        msg.setInformativeText(
            "Checkliste:\n"
            "1. Ultraleap Hand Tracking Software installiert und gestartet?\n"
            "2. Controller per USB angeschlossen (LED grün)?\n"
            "3. USB-Kabel fest eingesteckt?\n\n"
            "Behebe die Probleme und starte die App neu."
        )
        msg.exec()

    def _show_log_viewer(self) -> None:
        """Open or bring to front the log viewer dialog."""
        if self._log_viewer is None or not self._log_viewer.isVisible():
            self._log_viewer = LogViewerDialog(self)
        self._log_viewer.show()
        self._log_viewer.raise_()
        self._log_viewer.activateWindow()

    # ── Screen management ──────────────────────────────────────────

    def _build_screens(self) -> None:
        """Create (or recreate) all screens and add to stack."""
        self.patient_screen = PatientScreen(self)
        self.patient_detail = PatientDetailScreen(self)
        self.dashboard = TestDashboard(self)
        self.test_screen = TestScreen(self)
        self.results_screen = ResultsScreen(self)
        self.hanoi_screen = HanoiScreen(self)
        self.srt_screen = SRTScreen(self)
        self.tmt_screen = TMTScreen(self)

        self.stack.addWidget(self.patient_screen)
        self.stack.addWidget(self.patient_detail)
        self.stack.addWidget(self.dashboard)
        self.stack.addWidget(self.test_screen)
        self.stack.addWidget(self.results_screen)
        self.stack.addWidget(self.hanoi_screen)
        self.stack.addWidget(self.srt_screen)
        self.stack.addWidget(self.tmt_screen)

    def toggle_ui_mode(self) -> None:
        """Switch between dense and touch UI mode, rebuild all screens."""
        from PyQt6.QtCore import QSettings
        from PyQt6.QtWidgets import QApplication
        new_mode = "dense" if theme.current_ui_mode() == "touch" else "touch"
        theme.set_ui_mode(new_mode)
        QApplication.instance().setStyleSheet(theme.APP_STYLESHEET)
        QSettings("TapPD", "TapPD").setValue("ui_mode", new_mode)

        # Remove old screens
        while self.stack.count():
            w = self.stack.widget(0)
            self.stack.removeWidget(w)
            w.deleteLater()

        # Rebuild screens + update status bar
        self._build_screens()
        self._apply_statusbar_sizes()
        self.stack.setCurrentWidget(self.patient_screen)
        self.patient_screen.refresh_list()
        log.info("UI-Modus gewechselt: %s", new_mode)

    def _apply_statusbar_sizes(self) -> None:
        """Update status bar widget sizes to match current UI mode."""
        self._sensor_widget.setMinimumHeight(SZ.MIN)
        font_sz = SZ.STATUS_FONT
        self._sensor_label.setStyleSheet(f"font-size: {font_sz}px; color: #757575; border: none;")
        self._log_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: #757575; border: 1px solid #E0E0E0; "
            f"border-radius: 4px; padding: {SZ.STATUS_PAD}; font-size: {font_sz}px; "
            f"font-weight: 600; min-height: 0px; }}"
            f"QPushButton:hover {{ background: #F5F5F5; color: #1976D2; border-color: #1976D2; }}"
        )

    # ── Navigation ──────────────────────────────────────────────────

    def select_patient(self, patient: Patient) -> None:
        """Show patient detail screen with session history."""
        log.info("Patient ausgewählt: %s (ID %s)", patient.patient_code, patient.id)
        self.current_patient = patient
        self.current_session = None
        self.patient_detail.set_patient(patient)
        self.stack.setCurrentWidget(self.patient_detail)

    def show_patient_screen(self) -> None:
        self.current_session = None
        self.patient_screen.refresh_list()
        self.stack.setCurrentWidget(self.patient_screen)

    def start_new_session(self) -> None:
        """Create a new session and open the test dashboard."""
        if not self.current_patient or not self.current_patient.id:
            return
        conn = get_db()
        self.current_session = create_session(conn, self.current_patient.id)
        conn.close()
        log.info("Neue Session gestartet: Session %d für %s",
                 self.current_session.id, self.current_patient.patient_code)
        self.dashboard.set_patient(self.current_patient)
        self.stack.setCurrentWidget(self.dashboard)

    def start_test(self, test_key: str, hand: str, duration: int) -> None:
        """Start a motor test from the dashboard."""
        log.info("Test gestartet: %s (Hand: %s, Dauer: %ds)", test_key, hand, duration)
        test_cls = TEST_CLASSES[test_key]

        # Set mock mode
        if isinstance(self.capture_device, MockCaptureDevice):
            self.capture_device.mode = MOCK_MODES.get(test_key, "idle")

        # Pass part parameter for TMT
        if test_key == "trail_making_a":
            test = test_cls(self.capture_device, duration=float(duration), hand=hand, part="A")
        elif test_key == "trail_making_b":
            test = test_cls(self.capture_device, duration=float(duration), hand=hand, part="B")
        else:
            test = test_cls(self.capture_device, duration=float(duration), hand=hand)

        if test_key == "tower_of_hanoi":
            self.hanoi_screen.start_test(test, self.current_patient.patient_code)
            self.stack.setCurrentWidget(self.hanoi_screen)
        elif test_key == "spatial_srt":
            self.srt_screen.start_test(test, self.current_patient.patient_code)
            self.stack.setCurrentWidget(self.srt_screen)
        elif test_key in ("trail_making_a", "trail_making_b"):
            self.tmt_screen.start_test(test, self.current_patient.patient_code)
            self.stack.setCurrentWidget(self.tmt_screen)
        else:
            self.test_screen.start_test(test, self.current_patient.patient_code)
            self.stack.setCurrentWidget(self.test_screen)

    def show_results_silent(self, test: BaseMotorTest, patient_code: str) -> None:
        """Save results + raw data to database without navigating to results screen."""
        features = test.compute_features()
        measurement_id = None
        if self.current_patient and self.current_patient.id:
            m = Measurement(
                patient_id=self.current_patient.id,
                session_id=self.current_session.id if self.current_session else None,
                test_type=test.test_type(),
                hand=test.hand,
                duration_s=test.duration,
            )
            m.features = features
            conn = get_db()
            try:
                save_measurement(conn, m)
                measurement_id = m.id
            finally:
                conn.close()
        # Save raw data
        filepath = save_raw_data(test, patient_code, features)
        if filepath and measurement_id:
            try:
                conn = get_db()
                try:
                    update_raw_data_path(conn, measurement_id, str(filepath))
                finally:
                    conn.close()
            except Exception:
                log.exception("Failed to update raw_data_path for measurement %d", measurement_id)
        card = self.dashboard.cards.get(test.test_type())
        if card:
            card.mark_completed(test.hand)

    def show_results(self, test: BaseMotorTest, patient_code: str) -> None:
        """Show results and auto-save to database."""
        log.info("Ergebnisse berechnen: %s %s für %s", test.test_type(), test.hand, patient_code)
        features = test.compute_features()

        # Auto-save to database
        measurement_id = None
        if self.current_patient and self.current_patient.id:
            m = Measurement(
                patient_id=self.current_patient.id,
                session_id=self.current_session.id if self.current_session else None,
                test_type=test.test_type(),
                hand=test.hand,
                duration_s=test.duration,
            )
            m.features = features
            conn = get_db()
            try:
                save_measurement(conn, m)
                measurement_id = m.id
            finally:
                conn.close()

        # Mark test as completed on dashboard
        card = self.dashboard.cards.get(test.test_type())
        if card:
            card.mark_completed(test.hand)

        self.results_screen.show_results(test, patient_code, measurement_id=measurement_id, features=features)
        self.stack.setCurrentWidget(self.results_screen)

    def show_start(self) -> None:
        """Back to dashboard (called after test recording)."""
        self.stack.setCurrentWidget(self.dashboard)

    def show_patient_detail(self) -> None:
        """End session and return to patient detail."""
        self.current_session = None
        if self.current_patient:
            self.patient_detail.set_patient(self.current_patient)
        self.stack.setCurrentWidget(self.patient_detail)

    def resume_session(self, session: Session) -> None:
        """Resume an existing session (e.g. to add a missing measurement)."""
        self.current_session = session

    def repeat_test(self, test_key: str, hand: str, duration: int) -> None:
        """Repeat the same test (called from results screen)."""
        self.start_test(test_key, hand, duration)

    def closeEvent(self, event) -> None:
        log.info("Anwendung wird geschlossen")
        if self.capture_device.is_connected():
            self.capture_device.disconnect()
        event.accept()
