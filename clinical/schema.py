"""YAML-described clinical forms: schema, loader, validation, computed values.

A form is one YAML file in ``clinical/forms``. The app renders it, validates
answers against it and stores every coded item as its own observation
(clinical/store.py). Same pattern as the recording protocols: schema →
loader with ``Issue`` list → generic UI.

Answers are a plain dict ``{key: value}``; a repeat group's answers are a
list of dicts under the group's key. Values: numbers for integer/decimal/
scale, the choice ``code`` (str) for choice, a list of codes for multichoice,
bool, str for text, ISO date string for date. ``None``/``""``/``[]`` = not
answered.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

FORMS_DIR = Path(__file__).parent / "forms"

TYPES = ("integer", "decimal", "scale", "choice", "multichoice", "bool", "text", "date")
NUMERIC = ("integer", "decimal", "scale")


class FormError(ValueError):
    pass


@dataclass
class Issue:
    level: str          # "error" | "note"
    message: str
    key: str = ""

    def __str__(self) -> str:
        return f"{self.key}: {self.message}" if self.key else self.message


@dataclass
class Choice:
    code: str
    label: str
    extra: dict = field(default_factory=dict)


@dataclass
class Item:
    key: str
    label: str
    type: str = "text"
    range: tuple[float, float] | None = None
    unit: str = ""
    choices: list[Choice] = field(default_factory=list)
    catalog: str = ""
    concept: str = ""
    required: bool = False
    help: str = ""
    multiline: bool = False
    store_as: str = ""              # "number": choice code stored as NVAL_NUM

    @property
    def is_numeric(self) -> bool:
        return self.type in NUMERIC or (self.type == "choice" and self.store_as == "number")

    def choice_label(self, code) -> str:
        for c in self.choices:
            if c.code == str(code):
                return c.label
        return str(code)


@dataclass
class Repeat:
    key: str
    label: str
    items: list[Item]
    concept: str = ""


@dataclass
class Section:
    id: str
    title: str
    items: list[Item] = field(default_factory=list)
    repeat: Repeat | None = None


@dataclass
class Computed:
    key: str
    label: str
    expr: str
    unit: str = ""
    concept: str = ""


@dataclass
class Form:
    id: str
    name: str
    version: int = 1
    scope: str = "visit"
    carry_forward: bool = True
    summary: list[str] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    computed: list[Computed] = field(default_factory=list)
    catalogs: dict[str, list[Choice]] = field(default_factory=dict)
    path: str = ""

    # ── lookups ───────────────────────────────────────────────────
    @property
    def items(self) -> list[Item]:
        return [it for s in self.sections for it in s.items]

    @property
    def repeats(self) -> list[Repeat]:
        return [s.repeat for s in self.sections if s.repeat is not None]

    def item(self, key: str) -> Item | None:
        return next((it for it in self.items if it.key == key), None)

    def repeat(self, key: str) -> Repeat | None:
        return next((r for r in self.repeats if r.key == key), None)

    def computed_by_key(self, key: str) -> Computed | None:
        return next((c for c in self.computed if c.key == key), None)

    def label(self, key: str) -> str:
        it = self.item(key)
        if it is not None:
            return it.label
        c = self.computed_by_key(key)
        if c is not None:
            return c.label
        r = self.repeat(key)
        return r.label if r is not None else key

    @property
    def concept_cd(self) -> str:
        return f"TAPPD:FORM_{self.id.upper()}"


# ── loading ────────────────────────────────────────────────────────

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _choices(raw, catalogs: dict[str, list[Choice]], catalog: str, where: str) -> list[Choice]:
    if catalog:
        if catalog not in catalogs:
            raise FormError(f"{where}: unbekannter Katalog '{catalog}'")
        return catalogs[catalog]
    out = []
    for c in raw or []:
        if isinstance(c, dict):
            code = str(c.get("code", "")).strip()
            label = str(c.get("label", code))
            extra = {k: v for k, v in c.items() if k not in ("code", "label")}
        else:
            code = label = str(c)
            extra = {}
        if not code:
            raise FormError(f"{where}: Auswahl ohne code")
        out.append(Choice(code=code, label=label, extra=extra))
    return out


def _item(raw: dict, defaults: dict, catalogs: dict, where: str) -> Item:
    d = {**defaults, **(raw or {})}
    key = str(d.get("key", "")).strip()
    if not _KEY_RE.match(key):
        raise FormError(f"{where}: ungültiger Schlüssel {key!r}")
    typ = str(d.get("type", "text"))
    if typ not in TYPES:
        raise FormError(f"{where}.{key}: unbekannter Typ '{typ}' (erlaubt: {', '.join(TYPES)})")
    rng = d.get("range")
    if rng is not None:
        if not (isinstance(rng, (list, tuple)) and len(rng) == 2):
            raise FormError(f"{where}.{key}: range muss [min, max] sein")
        rng = (float(rng[0]), float(rng[1]))
        if rng[0] > rng[1]:
            raise FormError(f"{where}.{key}: range min > max")
    catalog = str(d.get("catalog", "") or "")
    choices = _choices(d.get("choices"), catalogs, catalog, f"{where}.{key}") \
        if typ in ("choice", "multichoice") else []
    if typ in ("choice", "multichoice") and not choices:
        raise FormError(f"{where}.{key}: {typ} ohne choices")
    return Item(key=key, label=str(d.get("label", key)), type=typ, range=rng,
                unit=str(d.get("unit", "") or ""), choices=choices, catalog=catalog,
                concept=str(d.get("concept", "") or ""), required=bool(d.get("required", False)),
                help=str(d.get("help", "") or ""), multiline=bool(d.get("multiline", False)),
                store_as=str(d.get("store_as", "") or ""))


def load_form_file(path: str | Path) -> Form:
    path = Path(path)
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        raise FormError(f"{path.name}: kein Mapping auf oberster Ebene")
    form_id = str(raw.get("id", "")).strip()
    if not _KEY_RE.match(form_id):
        raise FormError(f"{path.name}: ungültige id {form_id!r}")
    catalogs: dict[str, list[Choice]] = {}
    for name, entries in (raw.get("catalogs") or {}).items():
        catalogs[str(name)] = _choices(entries, {}, "", f"catalogs.{name}")

    item_defaults = raw.get("item_defaults") or {}
    sections: list[Section] = []
    seen: set[str] = set()
    for i, s in enumerate(raw.get("sections") or []):
        sid = str(s.get("id", f"s{i + 1}"))
        items = [_item(it, item_defaults, catalogs, f"sections.{sid}") for it in (s.get("items") or [])]
        rep = None
        if s.get("repeat"):
            r = s["repeat"]
            rkey = str(r.get("key", "")).strip()
            if not _KEY_RE.match(rkey):
                raise FormError(f"sections.{sid}.repeat: ungültiger Schlüssel {rkey!r}")
            ritems = [_item(it, item_defaults, catalogs, f"sections.{sid}.repeat")
                      for it in (r.get("items") or [])]
            if not ritems:
                raise FormError(f"sections.{sid}.repeat: ohne items")
            rep = Repeat(key=rkey, label=str(r.get("label", rkey)), items=ritems,
                         concept=str(r.get("concept", "") or ""))
            if rkey in seen:
                raise FormError(f"doppelter Schlüssel {rkey!r}")
            seen.add(rkey)
        for it in items:
            if it.key in seen:
                raise FormError(f"doppelter Schlüssel {it.key!r}")
            seen.add(it.key)
        sections.append(Section(id=sid, title=str(s.get("title", sid)), items=items, repeat=rep))

    computed: list[Computed] = []
    for c in raw.get("computed") or []:
        key = str(c.get("key", "")).strip()
        if not _KEY_RE.match(key) or key in seen:
            raise FormError(f"computed: ungültiger oder doppelter Schlüssel {key!r}")
        seen.add(key)
        expr = str(c.get("expr", "")).strip()
        if not _parse_expr(expr):
            raise FormError(f"computed.{key}: unbekannter Ausdruck {expr!r}")
        computed.append(Computed(key=key, label=str(c.get("label", key)), expr=expr,
                                 unit=str(c.get("unit", "") or ""),
                                 concept=str(c.get("concept", "") or "")))

    form = Form(id=form_id, name=str(raw.get("name", form_id)), version=int(raw.get("version", 1)),
                scope=str(raw.get("scope", "visit")), carry_forward=bool(raw.get("carry_forward", True)),
                summary=[str(k) for k in (raw.get("summary") or [])],
                sections=sections, computed=computed, catalogs=catalogs, path=str(path))
    if form.scope not in ("visit", "patient"):
        raise FormError(f"{path.name}: scope muss visit oder patient sein")
    for k in form.summary:
        if k not in seen:
            raise FormError(f"summary: unbekannter Schlüssel {k!r}")
    # every expression must point at something that exists
    for c in computed:
        fn, arg = _parse_expr(c.expr)
        if fn == "ledd" and form.repeat(arg) is None:
            raise FormError(f"computed.{c.key}: ledd() braucht eine Wiederholgruppe, '{arg}' fehlt")
        if fn == "years_since" and form.item(arg) is None:
            raise FormError(f"computed.{c.key}: years_since() verweist auf unbekanntes Item '{arg}'")
    return form


def load_form(form_id: str) -> Form:
    return load_form_file(FORMS_DIR / f"{form_id}.yaml")


def list_forms() -> list[Form]:
    out = []
    for p in sorted(FORMS_DIR.glob("*.yaml")):
        try:
            out.append(load_form_file(p))
        except Exception:
            continue
    return out


# ── validation ─────────────────────────────────────────────────────

def is_empty(value) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _check_item(it: Item, value, issues: list[Issue], prefix: str = "") -> None:
    key = f"{prefix}{it.key}"
    if is_empty(value):
        if it.required:
            issues.append(Issue("error", f"„{it.label}“ fehlt.", key))
        return
    if it.type in NUMERIC:
        try:
            num = float(value)
        except (TypeError, ValueError):
            issues.append(Issue("error", f"„{it.label}“ ist keine Zahl.", key))
            return
        if it.type in ("integer", "scale") and num != int(num):
            issues.append(Issue("error", f"„{it.label}“ muss ganzzahlig sein.", key))
        if it.range and not (it.range[0] <= num <= it.range[1]):
            issues.append(Issue("error", f"„{it.label}“ muss zwischen {it.range[0]:g} und "
                                         f"{it.range[1]:g} liegen.", key))
    elif it.type == "choice":
        if str(value) not in {c.code for c in it.choices}:
            issues.append(Issue("error", f"„{it.label}“: unbekannte Auswahl {value!r}.", key))
    elif it.type == "multichoice":
        if not isinstance(value, (list, tuple)):
            issues.append(Issue("error", f"„{it.label}“ erwartet eine Liste.", key))
            return
        bad = [v for v in value if str(v) not in {c.code for c in it.choices}]
        if bad:
            issues.append(Issue("error", f"„{it.label}“: unbekannte Auswahl {bad}.", key))
    elif it.type == "bool":
        if not isinstance(value, bool):
            issues.append(Issue("error", f"„{it.label}“ muss ja/nein sein.", key))
    elif it.type == "date":
        try:
            date.fromisoformat(str(value))
        except ValueError:
            issues.append(Issue("error", f"„{it.label}“ ist kein Datum (JJJJ-MM-TT).", key))


def validate(form: Form, answers: dict) -> list[Issue]:
    issues: list[Issue] = []
    for it in form.items:
        _check_item(it, answers.get(it.key), issues)
    for rep in form.repeats:
        rows = answers.get(rep.key) or []
        if not isinstance(rows, list):
            issues.append(Issue("error", f"„{rep.label}“ erwartet eine Liste.", rep.key))
            continue
        for i, row in enumerate(rows, start=1):
            for it in rep.items:
                _check_item(it, (row or {}).get(it.key), issues, prefix=f"{rep.key}[{i}].")
    known = {it.key for it in form.items} | {r.key for r in form.repeats}
    for k in answers:
        if k not in known:
            issues.append(Issue("note", f"Antwort auf unbekanntes Feld '{k}' wird ignoriert.", k))
    return issues


# ── computed values ────────────────────────────────────────────────

_EXPR_RE = re.compile(r"^(ledd|years_since|sum)\(([a-z][a-z0-9_*]*)\)$")


def _parse_expr(expr: str) -> tuple[str, str] | None:
    m = _EXPR_RE.match(expr or "")
    return (m.group(1), m.group(2)) if m else None


def ledd(form: Form, rows: list[dict], catalog: str = "substances") -> float | None:
    """Levodopa equivalent daily dose in mg (Tomlinson 2010 factors from the
    catalog). None when nothing contributes; COMT inhibitors scale the
    levodopa daily total instead of their own dose."""
    entries = {c.code: c for c in form.catalogs.get(catalog, [])}
    levodopa_total = 0.0
    contribs: list[float] = []
    comt: list[float] = []
    any_row = False
    for row in rows or []:
        row = row or {}
        c = entries.get(str(row.get("substance", "")))
        if c is None:
            continue
        try:
            daily = float(row.get("dose_mg") or 0) * float(row.get("per_day") or 1)
        except (TypeError, ValueError):
            continue
        any_row = True
        factor = float(c.extra.get("ledd_factor", 0) or 0)
        if c.extra.get("of") == "levodopa":
            comt.append(factor)
            continue
        if c.extra.get("atc") == "N04BA02":         # any levodopa preparation
            levodopa_total += daily
        contribs.append(daily * factor)
    if not any_row:
        return None
    total = sum(contribs) + sum(f * levodopa_total for f in comt)
    return round(total, 1)


def compute(form: Form, answers: dict, today: date | None = None) -> dict:
    """All computed values of the form for these answers (missing → None)."""
    today = today or date.today()
    out: dict = {}
    for c in form.computed:
        fn, arg = _parse_expr(c.expr)
        val = None
        if fn == "ledd":
            val = ledd(form, answers.get(arg) or [])
        elif fn == "years_since":
            raw = answers.get(arg)
            if not is_empty(raw):
                try:
                    val = today.year - int(float(raw))
                except (TypeError, ValueError):
                    val = None
        elif fn == "sum":
            prefix = arg.rstrip("*")
            vals = [answers.get(k) for k in answers if k.startswith(prefix)]
            nums = [float(v) for v in vals if not is_empty(v) and isinstance(v, (int, float))]
            val = sum(nums) if nums else None
        out[c.key] = val
    return out


def summary_line(form: Form, answers: dict, computed: dict | None = None) -> str:
    """Short text for the tree: the ``summary`` keys that have a value."""
    computed = computed if computed is not None else compute(form, answers)
    parts = []
    for k in form.summary:
        it = form.item(k)
        if it is not None:
            v = answers.get(k)
            if is_empty(v):
                continue
            if it.type == "choice":
                short = {"hoehn_yahr": "H&Y ", "med_state": "", "updrs3_state": ""}.get(k, "")
                label = it.choice_label(v)
                label = label.split(" – ")[0].split(" (")[0]
                parts.append(f"{short}{label}")
            elif it.type in NUMERIC:
                parts.append(f"{_short(it.label)} {v:g}" if isinstance(v, (int, float)) else str(v))
            else:
                parts.append(str(v))
        else:
            c = form.computed_by_key(k)
            v = computed.get(k)
            if c is None or is_empty(v):
                continue
            parts.append(f"{_short(c.label)} {v:g}{(' ' + c.unit) if c.unit else ''}")
    return "  ·  ".join(parts)


_SHORT = {"MDS-UPDRS Teil III": "UPDRS III", "Erkrankungsdauer": "Dauer"}


def _short(label: str) -> str:
    base = label.split(" (")[0]
    return _SHORT.get(base, base)


def describe(form: Form, answers: dict, computed: dict | None = None) -> list[tuple[str, str]]:
    """All answered items as (label, text) rows, section by section."""
    computed = computed if computed is not None else compute(form, answers)
    rows: list[tuple[str, str]] = []
    for s in form.sections:
        for it in s.items:
            v = answers.get(it.key)
            if is_empty(v):
                continue
            rows.append((it.label, format_value(it, v)))
        if s.repeat is not None:
            for i, row in enumerate(answers.get(s.repeat.key) or [], start=1):
                cells = []
                for it in s.repeat.items:
                    v = (row or {}).get(it.key)
                    if not is_empty(v):
                        cells.append(format_value(it, v))
                if cells:
                    rows.append((f"{s.repeat.label} {i}", "  ·  ".join(cells)))
    for c in form.computed:
        v = computed.get(c.key)
        if not is_empty(v):
            rows.append((c.label, f"{v:g}{(' ' + c.unit) if c.unit else ''}"))
    return rows


def format_value(it: Item, value) -> str:
    if it.type == "choice":
        return it.choice_label(value)
    if it.type == "multichoice":
        return ", ".join(it.choice_label(v) for v in value)
    if it.type == "bool":
        return "ja" if value else "nein"
    if it.type in NUMERIC:
        try:
            num = float(value)
            txt = f"{num:g}"
        except (TypeError, ValueError):
            txt = str(value)
        return f"{txt} {it.unit}".strip()
    return str(value)
