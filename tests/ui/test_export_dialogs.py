"""Export dialogs (ui/export_dialog.py) and their entry points in the UI."""

import zipfile


def test_bundle_dialog_exports_with_the_chosen_options(app, workbench, tmp_path):
    from tests.ui.test_screens_smoke import _measurement
    from ui.export_dialog import BundleExportDialog
    from storage.database import get_db
    _measurement(app)
    dlg = BundleExportDialog(workbench, app.patient, get_db)
    dlg.cb_pseudo.setChecked(True)
    dlg.cb_pdf.setChecked(False)
    dest = tmp_path / "paket.zip"
    dlg.dest.setText(str(dest))
    dlg._run()
    assert dlg.result() == 1 and dest.is_file()
    with zipfile.ZipFile(dest) as zf:
        names = zf.namelist()
        assert "report.html" in names and "report.json" in names and "report.pdf" not in names
        assert b"P-0001" in zf.read("report.html")
    assert "paket.zip" in dlg._status.text()


def test_research_dialog_writes_the_tables(app, workbench, tmp_path):
    from tests.ui.test_screens_smoke import _measurement
    from ui.export_dialog import ResearchExportDialog
    from storage.database import get_db
    _measurement(app)
    dlg = ResearchExportDialog(workbench, get_db)
    dest = tmp_path / "forschung"
    dlg.dest.setText(str(dest))
    dlg._run()
    assert dlg.result() == 1
    assert (dest / "measurements.csv").is_file() and (dest / "codebook.md").is_file()
    assert "1 Patienten" in dlg._status.text()


def test_export_entries_exist_in_menus(app, workbench):
    labels = [a.text() for a in workbench._patient_btn.menu().actions()]
    assert "📦 Export-Paket (Bericht, Videos)…" in labels
    from PyQt6.QtWidgets import QPushButton
    texts = [b.text() for b in app.win.patient_screen.findChildren(QPushButton)]
    assert "🔬 Forschungsexport" in texts
