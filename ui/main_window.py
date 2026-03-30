"""Main application window with the full flow:
   Patient Screen → Patient Detail → New Session → Test Dashboard → Test → Results
"""

from PyQt6.QtWidgets import (
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QStatusBar,
)

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
    create_session, get_db, save_measurement,
)
from ui.patient_screen import PatientScreen
from ui.patient_detail_screen import PatientDetailScreen
from ui.test_dashboard import TestDashboard
from ui.test_screen import TestScreen
from ui.hanoi_screen import HanoiScreen
from ui.srt_screen import SRTScreen
from ui.tmt_screen import TMTScreen
from ui.results_screen import ResultsScreen, save_raw_data


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

        # Screens
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

        # Status bar
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._update_status_bar()

        self.stack.setCurrentWidget(self.patient_screen)
        self._check_sensor_on_start()

    def _update_status_bar(self) -> None:
        if isinstance(self.capture_device, MockCaptureDevice):
            issues = getattr(self.capture_device, "_sensor_issues", None)
            if issues:
                self._status_bar.showMessage("Sensor: Nicht verbunden (Simulationsmodus)")
            else:
                self._status_bar.showMessage("Sensor: Simulationsmodus (--mock)")
        else:
            device_name = type(self.capture_device).__name__
            connected = "Verbunden" if self.capture_device.is_connected() else "Bereit"
            self._status_bar.showMessage(f"Sensor: {device_name} | Status: {connected}")

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
            "Die App laeuft im Simulationsmodus."
        )
        msg.setDetailedText(detail_text)
        msg.setInformativeText(
            "Checkliste:\n"
            "1. Ultraleap Hand Tracking Software installiert und gestartet?\n"
            "2. Controller per USB angeschlossen (LED gruen)?\n"
            "3. USB-Kabel fest eingesteckt?\n\n"
            "Behebe die Probleme und starte die App neu."
        )
        msg.exec()

    # ── Navigation ──────────────────────────────────────────────────

    def select_patient(self, patient: Patient) -> None:
        """Show patient detail screen with session history."""
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
        self.dashboard.set_patient(self.current_patient)
        self.stack.setCurrentWidget(self.dashboard)

    def start_test(self, test_key: str, hand: str, duration: int) -> None:
        """Start a motor test from the dashboard."""
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
            save_measurement(conn, m)
            measurement_id = m.id
            conn.close()
        # Save raw data
        filepath = save_raw_data(test, patient_code, features)
        if filepath and measurement_id:
            try:
                conn = get_db()
                conn.execute(
                    "UPDATE measurements SET raw_data_path=? WHERE id=?",
                    (str(filepath), measurement_id),
                )
                conn.commit()
                conn.close()
            except Exception:
                pass
        card = self.dashboard.cards.get(test.test_type())
        if card:
            card.mark_completed(test.hand)

    def show_results(self, test: BaseMotorTest, patient_code: str) -> None:
        """Show results and auto-save to database."""
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
            save_measurement(conn, m)
            measurement_id = m.id
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
        if self.capture_device.is_connected():
            self.capture_device.disconnect()
        event.accept()
