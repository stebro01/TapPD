"""The generic form dialog (ui/form_dialog.py) on the anamnesis form."""

from clinical.schema import load_form


def _dialog(qapp, answers=None):
    from storage.database import Patient
    from ui.form_dialog import FormDialog
    p = Patient(patient_code="T001", first_name="Test", last_name="Person",
                birth_date="1960-01-01", gender="f")
    return FormDialog(None, load_form("pd_anamnese"), patient=p, session_label="Sitzung 1",
                      answers=answers)


def test_answers_round_trip_through_the_widgets(qapp):
    given = {"diagnosis_year": 2019, "onset_side": "left", "hoehn_yahr": "2.5",
             "updrs3_total": 28, "family_pd": "no", "falls_12m": 2, "nms": ["rbd", "pain"],
             "moca": 27, "nms_note": "Schlaf schlecht", "med_state": "off",
             "last_dose_minutes": 720, "dbs": True, "comment": "x",
             "medication": [{"substance": "levodopa", "dose_mg": 100.0, "per_day": 4,
                             "times": "8, 12, 16, 20"},
                            {"substance": "pramipexole", "dose_mg": 0.7, "per_day": 3}]}
    dlg = _dialog(qapp, given)
    assert dlg.answers() == given
    assert dlg._computed_lbls["ledd_mg"].text() == "610 mg"
    assert dlg._computed_lbls["disease_duration_y"].text().isdigit()

    # an empty dialog answers nothing — no phantom zeros
    empty = _dialog(qapp)
    assert empty.answers() == {"dbs": False}
    assert empty._computed_lbls["ledd_mg"].text() == "–"


def test_repeat_table_add_and_remove_updates_ledd(qapp):
    dlg = _dialog(qapp)
    table = dlg._repeat_tables["medication"]
    table.add_row({"substance": "levodopa", "dose_mg": 200, "per_day": 3})
    assert dlg.answers()["medication"] == [{"substance": "levodopa", "dose_mg": 200.0, "per_day": 3}]
    assert dlg._computed_lbls["ledd_mg"].text() == "600 mg"
    table.add_row({"substance": "entacapone", "dose_mg": 200, "per_day": 3})
    assert dlg._computed_lbls["ledd_mg"].text() == "798 mg"
    table.table.setCurrentCell(1, 0)
    table._remove_current()
    assert dlg._computed_lbls["ledd_mg"].text() == "600 mg"


def test_accept_is_blocked_by_validation_errors(qapp, monkeypatch):
    dlg = _dialog(qapp, {"updrs3_total": 20})
    closed = []
    monkeypatch.setattr(type(dlg).__mro__[1], "accept", lambda self: closed.append(True))
    # the widgets themselves keep values in range, so feed a bad answer set
    monkeypatch.setattr(dlg, "answers", lambda: {"updrs3_total": 500, "hoehn_yahr": "9"})
    dlg.accept()
    assert not closed and "zwischen 0 und 132" in dlg._issues_lbl.text()
    assert "unbekannte Auswahl" in dlg._issues_lbl.text()
