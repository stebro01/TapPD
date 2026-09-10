"""Clinical report from a record dict: self-contained HTML, optionally PDF.

The HTML embeds the curve images (base64) so it travels as one file; the PDF
is rendered from the same HTML with Qt's text engine (no extra dependency).
"""

from __future__ import annotations

import base64
import html
import logging

log = logging.getLogger(__name__)

_CSS = """
body { font-family: Arial, Helvetica, sans-serif; font-size: 10.5pt; color: #212121; }
h1 { font-size: 18pt; color: #1565C0; margin-bottom: 2px; }
h2 { font-size: 13pt; color: #1565C0; border-bottom: 1px solid #BBDEFB; margin-top: 18px; }
h3 { font-size: 11pt; margin: 12px 0 4px 0; }
.meta { color: #757575; font-size: 9pt; }
table { border-collapse: collapse; margin: 4px 0 8px 0; }
td, th { border: 1px solid #E0E0E0; padding: 3px 8px; text-align: left; vertical-align: top; }
th { background: #F5F5F5; }
.k { color: #757575; width: 200px; }
.warn { color: #E65100; }
.note { background: #FFF8E1; padding: 4px 8px; margin: 4px 0; }
.small { font-size: 9pt; color: #757575; }
"""


def _esc(x) -> str:
    return html.escape("" if x is None else str(x))


def _dt(s: str) -> str:
    return (s or "")[:16].replace("T", " ")


def _feature_rows(features: dict, source_kind: str) -> str:
    from ui.feature_meta import FEATURE_META, unit_label
    rows = []
    for k, v in features.items():
        label, _unit = FEATURE_META.get(k, (k, ""))
        unit = unit_label(k, source_kind)
        val = f"{v:.3f}" if isinstance(v, float) else _esc(v)
        rows.append(f"<tr><td class='k'>{_esc(label)}</td><td>{val}</td><td>{_esc(unit)}</td></tr>")
    return "".join(rows)


def _paradigm_label(key: str) -> str:
    try:
        from paradigms import registry
        return (registry.get(key).label or key).replace("\n", " ")
    except Exception:
        return key


_HAND = {"left": "links", "right": "rechts", "both": "beidseits"}


def render_html(record: dict, curves: dict[int, bytes] | None = None) -> str:
    """The report as one HTML document. ``curves`` maps measurement id → PNG."""
    curves = curves or {}
    p = record["patient"]
    parts = [f"<html><head><meta charset='utf-8'><style>{_CSS}</style></head><body>"]
    title = p["id"] if p.get("pseudonymised") else f"{p['id']} – {p.get('last_name', '')}, {p.get('first_name', '')}".strip(" –,")
    parts.append(f"<h1>Motryx-Bericht: {_esc(title)}</h1>")
    sex = {"m": "männlich", "f": "weiblich", "d": "divers"}.get(p.get("sex", ""), "–")
    born = p.get("birth_date") or p.get("birth_year") or "–"
    parts.append(f"<div class='meta'>Geboren {_esc(born)} · {sex} · Alter {p.get('age') if p.get('age') is not None else '–'}"
                 f" · erstellt {_dt(record['exported_at'])} · Motryx {_esc(record['app_version'])}"
                 + (" · pseudonymisiert" if p.get("pseudonymised") else "") + "</div>")
    if p.get("notes"):
        parts.append(f"<div class='note'>{_esc(p['notes'])}</div>")

    sessions = record.get("sessions", [])
    parts.append(f"<h2>Übersicht</h2><table><tr><th>Sitzung</th><th>Datum</th><th>Klinik</th>"
                 f"<th>Messungen</th></tr>")
    for i, s in enumerate(sessions, start=1):
        clin = "; ".join(f["summary"] or f["name"] for f in s["forms"]) or "–"
        ms = ", ".join(f"{_paradigm_label(m['test_type'])} ({_HAND.get(m['hand'], m['hand'])})"
                       for m in s["measurements"]) or "–"
        parts.append(f"<tr><td>{i}</td><td>{_dt(s['date'])}</td><td>{_esc(clin)}</td><td>{_esc(ms)}</td></tr>")
    parts.append("</table>")

    for i, s in enumerate(sessions, start=1):
        parts.append(f"<h2>Sitzung {i} · {_dt(s['date'])}</h2>")
        if s.get("notes"):
            parts.append(f"<div class='note'>{_esc(s['notes'])}</div>")
        if s.get("note"):
            parts.append(_note_html(s["note"]))
        for f in s["forms"]:
            parts.append(f"<h3>{_esc(f['name'])} <span class='small'>({_dt(f['recorded_at'])})</span></h3><table>")
            for label, value in f["rows"]:
                parts.append(f"<tr><td class='k'>{_esc(label)}</td><td>{_esc(value)}</td></tr>")
            parts.append("</table>")
        for m in s["measurements"]:
            parts.append(_measurement_html(m, curves.get(m["id"])))
    orphans = record.get("unassigned_measurements", [])
    if orphans:
        parts.append("<h2>Messungen ohne Sitzung</h2>")
        for m in orphans:
            parts.append(_measurement_html(m, curves.get(m["id"])))
    parts.append("<p class='small'>Motryx ist ein Forschungsprototyp und kein zugelassenes "
                 "Medizinprodukt. ≈mm-Werte aus Kamera-Quellen sind Modellschätzungen.</p>")
    parts.append("</body></html>")
    return "\n".join(parts)


def _note_html(note: dict) -> str:
    txt = _esc(note.get("text", ""))
    att = ", ".join(_esc(a.get("name", "?")) for a in note.get("attachments", []))
    return f"<div class='note'>📝 {txt}" + (f"<br><span class='small'>Anhänge: {att}</span>" if att else "") + "</div>"


def _measurement_html(m: dict, png: bytes | None) -> str:
    src = {"raw": "Roh-Take", "clip": "archivierter Clip"}.get(m.get("analysed_on"), "")
    cap = m.get("capture") or {}
    origin = {"recording": "eigene Aufnahme", "import": "Import"}.get(cap.get("kind"), m.get("source_kind", ""))
    bits = [f"{_dt(m['recorded_at'])}", f"{m['duration_s']:g} s", f"Quelle: {_esc(origin or '–')}"]
    if src:
        bits.append(f"ausgewertet auf {src}")
    if m.get("deidentified"):
        bits.append("Clip anonymisiert")
    if cap.get("camera", {}).get("name"):
        bits.append(f"Kamera {_esc(cap['camera']['name'])}")
    mpi = (m.get("features") or {}).get("mpi")
    head = f"<h3>{_esc(_paradigm_label(m['test_type']))} · {_HAND.get(m['hand'], m['hand'])}"
    if isinstance(mpi, (int, float)):
        head += f" · <b>MPI {mpi:.2f}</b>"
    head += "</h3>"
    out = [head, f"<div class='small'>{' · '.join(bits)}</div>"]
    if m.get("note"):
        out.append(_note_html(m["note"]))
    out.append("<table>" + _feature_rows(m.get("features") or {}, m.get("source_kind", "")) + "</table>")
    if png:
        out.append(f"<img src='data:image/png;base64,{base64.b64encode(png).decode('ascii')}' "
                   f"width='620'>")
    return "\n".join(out)


def render_pdf(html_text: str, path: str) -> bool:
    """Write the HTML as PDF with Qt's text engine. Needs a QGuiApplication."""
    try:
        from PyQt6.QtCore import QMarginsF
        from PyQt6.QtGui import QPageLayout, QPageSize, QTextDocument
        from PyQt6.QtPrintSupport import QPrinter
        doc = QTextDocument()
        doc.setHtml(html_text)
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(str(path))
        printer.setPageLayout(QPageLayout(QPageSize(QPageSize.PageSizeId.A4),
                                          QPageLayout.Orientation.Portrait,
                                          QMarginsF(15, 15, 15, 15), QPageLayout.Unit.Millimeter))
        doc.print(printer)
        return True
    except Exception:
        log.warning("PDF konnte nicht erzeugt werden", exc_info=True)
        return False
