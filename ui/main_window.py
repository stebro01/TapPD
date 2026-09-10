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
from capture.mock_capture import SimulationSource
from capture.mediapipe_capture import WebcamSource
from paradigms.base_test import BaseParadigm
from storage.database import (
    Measurement, Patient, Session,
    create_session, get_db, save_measurement, update_raw_data_path,
)
from ui.patient_screen import PatientScreen
from ui.test_dashboard import TestDashboard
from ui.test_screen import TestScreen
from ui.hanoi_screen import HanoiScreen
from ui.srt_screen import SRTScreen
from ui.tmt_screen import TMTScreen
from ui.results_screen import ResultsScreen, save_raw_data
from ui.gesture_lab_screen import GestureLabScreen
from ui.tracking_screen import TrackingScreen
from ui.patient_workbench import PatientWorkbench
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




class MotryxMainWindow(QMainWindow):
    def __init__(self, capture_device: BaseCaptureDevice) -> None:
        super().__init__()
        self.capture_device = capture_device
        self._return_session = None        # set when a live test starts from the workbench
        self.current_patient: Patient | None = None
        self.current_session: Session | None = None
        from app_settings import APP_TITLE
        self.setWindowTitle(APP_TITLE)
        self.setMinimumSize(950, 720)
        self.resize(1280, 820)

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
        self._sensor_label.setStyleSheet(f"font-size: 13px; color: {theme.TEXT_SECONDARY}; border: none;")
        sensor_layout.addWidget(self._sensor_label)

        self._sensor_widget.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sensor_widget.setMinimumHeight(SZ.MIN)
        self._sensor_widget.mousePressEvent = lambda e: self._check_sensor_status()
        self._sensor_widget.setToolTip("Klicken um Sensor-Status zu prüfen")
        self._status_bar.addWidget(self._sensor_widget, 1)  # left side, stretch

        # Input-source button (right side) — opens the Tracking screen.
        # "◉" reads as a sensor/lens, i.e. the input source (Leap or webcam).
        self._tracking_btn = QPushButton("◉  Eingabequelle")
        self._tracking_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {theme.TEXT_SECONDARY}; border: 1px solid {theme.BORDER}; "
            "border-radius: 4px; padding: 8px 12px; font-size: 12px; min-height: 36px; }"
            f"QPushButton:hover {{ background: #E3F2FD; color: {theme.PRIMARY}; border-color: {theme.PRIMARY}; }}"
        )
        self._tracking_btn.setToolTip("Eingabequelle wählen (Leap Motion / Webcam) + Vorschau")
        self._tracking_btn.clicked.connect(self.show_tracking_screen)
        self._status_bar.addPermanentWidget(self._tracking_btn)
        # The "Eingabequelle" footer button belongs to the start page only.
        self.stack.currentChanged.connect(self._update_tracking_btn_visibility)
        self._update_tracking_btn_visibility()

        # Reset Leap button (right side, before Log)
        self._reset_btn = QPushButton("Reset Leap")
        self._reset_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {theme.TEXT_SECONDARY}; border: 1px solid {theme.BORDER}; "
            "border-radius: 4px; padding: 8px 12px; font-size: 12px; min-height: 36px; }"
            f"QPushButton:hover {{ background: #FFF3E0; color: {theme.WARN_DARK}; border-color: {theme.WARN_DARK}; }}"
        )
        self._reset_btn.setToolTip("Leap Motion Controller zurücksetzen und neu verbinden")
        self._reset_btn.clicked.connect(self._reset_leap)
        self._status_bar.addPermanentWidget(self._reset_btn)

        # Log button (right side)
        self._log_btn = QPushButton("  Log  ")
        self._log_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {theme.TEXT_SECONDARY}; border: 1px solid {theme.BORDER}; "
            "border-radius: 4px; padding: 8px 16px; font-size: 13px; font-weight: 600; min-height: 36px; }"
            f"QPushButton:hover {{ background: {theme.HOVER_BG}; color: {theme.PRIMARY}; border-color: {theme.PRIMARY}; }}"
        )
        self._log_btn.clicked.connect(self._show_log_viewer)
        self._status_bar.addPermanentWidget(self._log_btn)

        self._log_viewer: LogViewerDialog | None = None
        self._update_status_bar()

        self.stack.setCurrentWidget(self.patient_screen)
        self._check_sensor_on_start()

    def _update_status_bar(self) -> None:
        from capture.source import source_kind
        kind = source_kind(self.capture_device)
        # "Reset Leap" only makes sense when the Leap is the active source.
        self._reset_btn.setVisible(kind == "leap")

        if kind == "mock":
            issues = getattr(self.capture_device, "_sensor_issues", None)
            if issues:
                self._set_sensor_indicator(False, "Sensor nicht verbunden – Simulationsmodus")
            else:
                self._set_sensor_indicator(False, "Simulationsmodus (--mock)")
            return

        connected = self.capture_device.is_connected()
        if kind == "webcam":
            name = self._webcam_name(self.capture_device)
            source = f"Webcam-Tracking ({name})" if name else "Webcam-Tracking"
        else:
            source = "Leap Motion Controller"
        suffix = "verbunden" if connected else "getrennt"
        self._set_sensor_indicator(connected, f"{source} {suffix}")

    @staticmethod
    def _webcam_name(device: "WebcamSource") -> str:
        """Best-effort friendly name of the active webcam."""
        for idx, nm in getattr(device, "_cameras", []) or []:
            if idx == device.camera_index:
                return nm.strip()
        return ""

    def _set_sensor_indicator(self, connected: bool, label: str) -> None:
        color = f"{theme.ACCENT}" if connected else f"{theme.DANGER}"
        self._sensor_dot.setStyleSheet(
            f"background-color: {color}; border-radius: 8px; border: none;"
        )
        self._sensor_label.setText(label)

    def _check_sensor_status(self) -> None:
        """Re-check sensor connection and show detailed diagnostics on click."""
        log.info("Sensor-Status wird geprüft...")

        # Webcam tracking: don't run Leap-specific diagnostics/reconnect.
        if isinstance(self.capture_device, WebcamSource):
            connected = self.capture_device.is_connected()
            name = self._webcam_name(self.capture_device)
            QMessageBox.information(
                self, "Tracking-Status",
                ("Webcam-Tracking aktiv" + (f" ({name})" if name else "") + ".")
                if connected else
                "Webcam-Tracking ist nicht verbunden.\n"
                "Quelle über die Schaltfläche „Tracking“ neu wählen.",
            )
            self._update_status_bar()
            return

        is_mock = isinstance(self.capture_device, SimulationSource)

        if is_mock:
            # Try to connect a real Leap device
            try:
                from capture.leap_capture import LeapSource
                test_device = LeapSource()
                test_device.connect()
                self.capture_device = test_device
                self._update_status_bar()
                log.info("Leap Controller gefunden! Wechsel von Mock auf LeapSource")
                QMessageBox.information(
                    self, "Sensor erkannt",
                    "Leap Motion Controller erfolgreich verbunden!\n"
                    "Der Simulationsmodus wurde deaktiviert."
                )
                return
            except Exception as e:
                # NB: ``e`` is unbound after the except block in Python 3 —
                # capture the message now for use in the diagnostics dialog.
                err_msg = str(e)
                log.warning("Sensor-Check: Leap nicht verfügbar (%s)", err_msg)

            # Show detailed diagnostics
            from capture import diagnose_sensor
            issues = diagnose_sensor()
            self._show_sensor_diagnostics(issues, err_msg)
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

    def _reset_leap(self) -> None:
        """Disconnect and reconnect the active capture device."""
        log.info("Reset angefordert")

        # Webcam tracking: reconnect the sidecar generically, no Leap dialogs.
        if isinstance(self.capture_device, WebcamSource):
            self._set_sensor_indicator(False, "Webcam-Tracking wird neu gestartet...")
            from PyQt6.QtWidgets import QApplication
            QApplication.processEvents()
            try:
                self.capture_device.disconnect()
                self.capture_device.connect()
                self._update_status_bar()
                QMessageBox.information(self, "Neu gestartet",
                                        "Webcam-Tracking wurde neu verbunden.")
            except Exception as e:
                log.error("Webcam-Reset fehlgeschlagen: %s", e)
                self._update_status_bar()
                QMessageBox.warning(self, "Reset fehlgeschlagen",
                                    f"Webcam-Tracking konnte nicht verbunden werden.\n\n{e}")
            return

        self._set_sensor_indicator(False, "Resette Leap Motion...")
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()

        # Step 1: Disconnect
        try:
            self.capture_device.disconnect()
            log.info("Leap Motion getrennt")
        except Exception as e:
            log.warning("Disconnect fehlgeschlagen: %s", e)

        # Step 2: Reconnect
        try:
            self.capture_device.connect()
            self._update_status_bar()
            log.info("Leap Motion Reset erfolgreich")
            QMessageBox.information(
                self, "Reset erfolgreich",
                "Leap Motion Controller wurde zurückgesetzt und neu verbunden."
            )
        except Exception as e1:
            log.warning("Reconnect fehlgeschlagen (%s), erstelle neues Device...", e1)
            # Step 3: Fresh device as fallback
            try:
                from capture.leap_capture import LeapSource
                new_device = LeapSource()
                new_device.connect()
                self.capture_device = new_device
                self._update_status_bar()
                log.info("Neues LeapSource erstellt und verbunden")
                QMessageBox.information(
                    self, "Reset erfolgreich",
                    "Leap Motion Controller wurde neu initialisiert."
                )
            except Exception as e2:
                log.error("Leap Motion Reset komplett fehlgeschlagen: %s", e2)
                self._set_sensor_indicator(False, "Reset fehlgeschlagen")
                self._update_status_bar()
                QMessageBox.warning(
                    self, "Reset fehlgeschlagen",
                    f"Leap Motion Controller konnte nicht verbunden werden.\n\n{e2}"
                )

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
        if not isinstance(self.capture_device, SimulationSource):
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
        # One screen for everything about a patient: sessions, recording,
        # playback, results. (Replaces the old detail list + session screen.)
        self.patient_detail = PatientWorkbench(self)
        self.dashboard = TestDashboard(self)
        self.test_screen = TestScreen(self)
        self.results_screen = ResultsScreen(self)
        self.hanoi_screen = HanoiScreen(self)
        self.srt_screen = SRTScreen(self)
        self.tmt_screen = TMTScreen(self)
        from ui.saccade_screen import SaccadeScreen
        self.saccade_screen = SaccadeScreen(self)
        self.gesture_lab_screen = GestureLabScreen(self)
        self.tracking_screen = TrackingScreen(self)

        self.stack.addWidget(self.patient_screen)
        self.stack.addWidget(self.patient_detail)
        self.stack.addWidget(self.dashboard)
        self.stack.addWidget(self.test_screen)
        self.stack.addWidget(self.results_screen)
        self.stack.addWidget(self.hanoi_screen)
        self.stack.addWidget(self.srt_screen)
        self.stack.addWidget(self.tmt_screen)
        self.stack.addWidget(self.saccade_screen)
        self.stack.addWidget(self.gesture_lab_screen)
        self.stack.addWidget(self.tracking_screen)

    def _update_tracking_btn_visibility(self, *_args) -> None:
        self._tracking_btn.setVisible(self.stack.currentWidget() is self.patient_screen)

    def toggle_ui_mode(self) -> None:
        """Switch between dense and touch UI mode, rebuild all screens."""
        from PyQt6.QtCore import QSettings
        from PyQt6.QtWidgets import QApplication
        new_mode = "dense" if theme.current_ui_mode() == "touch" else "touch"
        theme.set_ui_mode(new_mode)
        QApplication.instance().setStyleSheet(theme.APP_STYLESHEET)
        from app_settings import app_settings
        app_settings().setValue("ui_mode", new_mode)

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
        self._sensor_label.setStyleSheet(f"font-size: {font_sz}px; color: {theme.TEXT_SECONDARY}; border: none;")
        self._log_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {theme.TEXT_SECONDARY}; border: 1px solid {theme.BORDER}; "
            f"border-radius: 4px; padding: {SZ.STATUS_PAD}; font-size: {font_sz}px; "
            f"font-weight: 600; min-height: 0px; }}"
            f"QPushButton:hover {{ background: {theme.HOVER_BG}; color: {theme.PRIMARY}; border-color: {theme.PRIMARY}; }}"
        )

    # ── Navigation ──────────────────────────────────────────────────

    def select_patient(self, patient: Patient) -> None:
        """Show patient detail screen with session history."""
        log.info("Proband ausgewählt: %s (ID %s)", patient.patient_code, patient.id)
        self.current_patient = patient
        self.current_session = None
        self.patient_detail.set_patient(patient)
        self.stack.setCurrentWidget(self.patient_detail)

    def show_patient_screen(self) -> None:
        self.current_session = None
        if self.stack.currentWidget() is self.patient_detail:
            self.patient_detail.leave()     # release the camera, save sessions
        self.patient_screen.refresh_list()
        self.stack.setCurrentWidget(self.patient_screen)

    def show_gesture_lab(self, return_screen: str = "patients") -> None:
        self.gesture_lab_screen.return_screen = return_screen
        self.stack.setCurrentWidget(self.gesture_lab_screen)

    def show_session(self, session, step_id: str = "") -> None:
        """Show a session (optionally a step) in the patient workbench."""
        if self.current_patient is None:
            return
        self.current_session = session
        if self.stack.currentWidget() is not self.patient_detail:
            self.patient_detail.set_patient(self.current_patient)
            self.stack.setCurrentWidget(self.patient_detail)
        self.patient_detail.open_session(session, step_id)

    def close_session(self) -> None:
        """Kept for callers that still 'leave' a session: it is the same screen."""
        self.patient_detail.refresh()
        self.stack.setCurrentWidget(self.patient_detail)

    def show_tracking_screen(self) -> None:
        self._return_after_tracking = self.stack.currentWidget()
        self.tracking_screen.on_enter()
        self.stack.setCurrentWidget(self.tracking_screen)

    def close_tracking_screen(self) -> None:
        target = getattr(self, "_return_after_tracking", None) or self.patient_screen
        self.stack.setCurrentWidget(target)

    def switch_capture_device(self, mode: str, camera_index: int = 0,
                              flip_handedness: bool = False,
                              device: BaseCaptureDevice | None = None) -> bool:
        """Swap the active capture device at runtime.

        ``device`` may be an already-connected device (e.g. the Tracking
        screen's live preview device) to adopt directly instead of building a
        fresh one.  Persists the choice to QSettings.  Returns True on success.
        """
        from PyQt6.QtCore import QSettings
        from capture import create_source

        old = self.capture_device
        if old is not None and old is not device:
            try:
                old.stop_recording()
            except Exception:
                pass
            try:
                old.disconnect()
            except Exception:
                pass

        try:
            if device is not None:
                new = device
                # An adopted device must actually be live; reconnect if it died
                # between preview and adoption.
                if not new.is_connected():
                    new.connect()
            else:
                new = create_source(mode, camera_index=camera_index,
                                            flip_handedness=flip_handedness)
                new.connect()
        except Exception as e:
            log.error("Wechsel auf %s fehlgeschlagen: %s", mode, e)
            QMessageBox.warning(
                self, "Tracking-Wechsel fehlgeschlagen",
                f"Konnte nicht auf '{mode}' wechseln:\n{e}\n\nSimulationsmodus aktiv.",
            )
            self.capture_device = SimulationSource()
            self._update_status_bar()
            return False

        self.capture_device = new
        self._update_status_bar()

        from app_settings import app_settings
        s = app_settings()
        s.setValue("capture_mode", mode)
        s.setValue("camera_index", camera_index)
        s.setValue("flip_handedness", flip_handedness)
        log.info("Capture-Device gewechselt auf %s (%s)", mode, type(new).__name__)
        return True

    def start_new_session(self) -> None:
        """Create a new session and open the test dashboard."""
        if not self.current_patient or not self.current_patient.id:
            return
        conn = get_db()
        self.current_session = create_session(conn, self.current_patient.id)
        conn.close()
        log.info("Neue Session gestartet: Session %d für %s",
                 self.current_session.id, self.current_patient.patient_code)
        # The workbench is already showing this patient; the new session
        # appears as a group in its list, selected, with the empty-state cards.
        if self.stack.currentWidget() is not self.patient_detail:
            self.patient_detail.set_patient(self.current_patient)
            self.stack.setCurrentWidget(self.patient_detail)
        self.patient_detail.refresh()
        self.patient_detail.open_session(self.current_session)

    def start_test(self, test_key: str, hand: str, duration: int) -> None:
        """Start a paradigm from the dashboard (everything via the registry)."""
        from paradigms import registry
        log.info("Test gestartet: %s (Hand: %s, Dauer: %ds)", test_key, hand, duration)
        spec = registry.get(test_key)

        # Set the simulation scenario when running on the simulation source.
        if isinstance(self.capture_device, SimulationSource):
            self.capture_device.mode = spec.sim_scenario

        test = spec.load_class()(self.capture_device, duration=float(duration),
                                 hand=hand, **spec.cls_kwargs)

        screen = {
            registry.SCREEN_HANOI: self.hanoi_screen,
            registry.SCREEN_SRT: self.srt_screen,
            registry.SCREEN_TMT: self.tmt_screen,
            registry.SCREEN_SACCADE: self.saccade_screen,
            registry.SCREEN_METRIC: self.test_screen,
        }[spec.screen]
        screen.start_test(test, self.current_patient.patient_code)
        self.stack.setCurrentWidget(screen)

    def show_results_silent(self, test: BaseParadigm, patient_code: str) -> None:
        """Save results + raw data to database without navigating to results screen."""
        features = test.compute_features()
        measurement_id = None
        if self.current_patient and self.current_patient.id:
            from capture.source import source_kind as _source_kind
            m = Measurement(
                patient_id=self.current_patient.id,
                session_id=self.current_session.id if self.current_session else None,
                test_type=test.test_type(),
                hand=test.hand,
                duration_s=test.duration,
                source_kind=_source_kind(test.capture),
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

    def show_results(self, test: BaseParadigm, patient_code: str) -> None:
        """Show results and auto-save to database."""
        log.info("Ergebnisse berechnen: %s %s für %s", test.test_type(), test.hand, patient_code)
        features = test.compute_features()

        # Auto-save to database
        measurement_id = None
        if self.current_patient and self.current_patient.id:
            from capture.source import source_kind as _source_kind
            m = Measurement(
                patient_id=self.current_patient.id,
                session_id=self.current_session.id if self.current_session else None,
                test_type=test.test_type(),
                hand=test.hand,
                duration_s=test.duration,
                source_kind=_source_kind(test.capture),
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
        """Back to where the test was started: the session in the workbench
        when it came from there, else the dashboard."""
        s, self._return_session = self._return_session, None
        if s is not None and self.current_patient is not None:
            self.show_session(s)
            return
        self.stack.setCurrentWidget(self.dashboard)

    def start_test_from_session(self, session: Session, test_key: str, hand: str,
                                duration: int) -> None:
        """Live test out of the workbench: result lands in ``session``, cancel
        and „Fortfahren“ lead back to it."""
        self.resume_session(session)
        self._return_session = session
        self.start_test(test_key, hand, duration)

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
        # The Tracking screen may own preview/face sidecars (not the active
        # device) — tear them down so no subprocess is orphaned on hard close.
        try:
            self.tracking_screen._teardown_candidate()
        except Exception:
            pass
        if self.capture_device.is_connected():
            self.capture_device.disconnect()
        event.accept()
