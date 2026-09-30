"""YAML clinical forms: loader, validation, computed values (LEDD), summary."""

from datetime import date

import pytest

from clinical import schema as cs
from clinical.schema import FormError, compute, describe, ledd, load_form, load_form_file, \
    summary_line, validate


@pytest.fixture(scope="module")
def form():
    return load_form("pd_anamnese")


def _answers(**over):
    a = {"diagnosis_year": 2019, "hoehn_yahr": "2", "updrs3_total": 28, "med_state": "off",
         "nms": ["hyposmia", "rbd"], "dbs": False,
         "medication": [{"substance": "levodopa", "dose_mg": 100, "per_day": 4},
                        {"substance": "entacapone", "dose_mg": 200, "per_day": 4},
                        {"substance": "pramipexole", "dose_mg": 0.7, "per_day": 3},
                        {"substance": "rasagiline", "dose_mg": 1, "per_day": 1}]}
    a.update(over)
    return a


def test_the_anamnesis_form_loads_with_its_parts(form):
    assert form.id == "pd_anamnese" and form.scope == "visit" and form.carry_forward
    assert [s.id for s in form.sections] == ["diagnosis", "family", "falls", "nonmotor",
                                             "medication", "misc"]
    assert form.item("hoehn_yahr").is_numeric                # choice stored as number
    assert form.repeat("medication").items[0].catalog == "substances"
    assert {c.key for c in form.computed} == {"disease_duration_y", "ledd_mg"}
    assert form.concept_cd == "TAPPD:FORM_PD_ANAMNESE"
    assert form.item("moca").concept == "LOINC:72172-0"


def test_validation_catches_ranges_choices_and_types(form):
    assert validate(form, _answers()) == []
    issues = {i.key: i.message for i in validate(form, _answers(
        updrs3_total=140, hoehn_yahr="9", falls_12m="viele", dbs="ja",
        medication=[{"substance": "levodopa", "dose_mg": -5, "per_day": 4}], bogus=1))}
    assert "zwischen 0 und 132" in issues["updrs3_total"]
    assert "unbekannte Auswahl" in issues["hoehn_yahr"]
    assert "keine Zahl" in issues["falls_12m"]
    assert "ja/nein" in issues["dbs"]
    assert "zwischen 0 und 5000" in issues["medication[1].dose_mg"]
    assert "ignoriert" in issues["bogus"]


def test_ledd_follows_tomlinson_factors(form):
    rows = _answers()["medication"]
    # 400 levodopa + 0.33·400 entacapone + 2.1·100 pramipexole + 1·100 rasagiline
    assert ledd(form, rows) == 842.0
    assert ledd(form, []) is None
    assert ledd(form, [{"substance": "entacapone", "dose_mg": 200, "per_day": 3}]) == 0.0
    assert ledd(form, [{"substance": "levodopa_cr", "dose_mg": 200, "per_day": 2},
                       {"substance": "opicapone", "dose_mg": 50, "per_day": 1}]) == 300.0 + 200.0
    assert ledd(form, [{"substance": "rotigotine", "dose_mg": 6, "per_day": 1}]) == 180.0
    assert ledd(form, [{"substance": "other", "dose_mg": 10, "per_day": 1}]) == 0.0


def test_computed_and_summary(form):
    c = compute(form, _answers(), today=date(2026, 9, 10))
    assert c == {"disease_duration_y": 7, "ledd_mg": 842.0}
    assert compute(form, {}, today=date(2026, 9, 10)) == {"disease_duration_y": None,
                                                        "ledd_mg": None}
    assert summary_line(form, _answers(), c) == "H&Y 2  ·  UPDRS III 28  ·  LEDD 842 mg  ·  OFF"
    rows = dict(describe(form, _answers(), c))
    assert rows["Nicht-motorische Symptome"[:0] or "Vorhanden"] == "Riechstörung, REM-Schlaf-Verhaltensstörung"
    assert rows["Präparat 1"].startswith("Levodopa") and rows["LEDD (mg/Tag)"] == "842 mg"
    assert rows["Tiefe Hirnstimulation"] == "nein"


def _write(tmp_path, text):
    p = tmp_path / "f.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_loader_rejects_broken_forms(tmp_path):
    base = "id: t\nname: T\nsections:\n  - id: a\n    items:\n"
    with pytest.raises(FormError, match="unbekannter Typ"):
        load_form_file(_write(tmp_path, base + "      - {key: x, type: blob}\n"))
    with pytest.raises(FormError, match="doppelter"):
        load_form_file(_write(tmp_path, base + "      - {key: x, type: text}\n      - {key: x, type: text}\n"))
    with pytest.raises(FormError, match="ohne choices"):
        load_form_file(_write(tmp_path, base + "      - {key: x, type: choice}\n"))
    with pytest.raises(FormError, match="range min > max"):
        load_form_file(_write(tmp_path, base + "      - {key: x, type: integer, range: [5, 1]}\n"))
    with pytest.raises(FormError, match="Wiederholgruppe"):
        load_form_file(_write(tmp_path, base + "      - {key: x, type: text}\ncomputed:\n"
                                              "  - {key: l, expr: 'ledd(meds)'}\n"))
    with pytest.raises(FormError, match="unbekannter Ausdruck"):
        load_form_file(_write(tmp_path, base + "      - {key: x, type: text}\ncomputed:\n"
                                              "  - {key: l, expr: 'eval(x)'}\n"))
    with pytest.raises(FormError, match="unbekannter Katalog"):
        load_form_file(_write(tmp_path, base + "      - {key: x, type: choice, catalog: nope}\n"))
    ok = load_form_file(_write(tmp_path, base + "      - {key: x, type: scale, range: [0, 4]}\n"
                                               "      - {key: y, type: scale, range: [0, 4]}\n"
                                               "computed:\n  - {key: s, expr: 'sum(x*)'}\n"))
    assert compute(ok, {"x": 3, "y": 1}) == {"s": 3.0}


def test_list_forms_includes_the_anamnesis():
    assert "pd_anamnese" in [f.id for f in cs.list_forms()]
