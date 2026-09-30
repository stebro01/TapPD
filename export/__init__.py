"""Exports of the record: research tables, clinical report, hand-over bundle.

* ``export.record``    — one serializer: a patient's record as a dict
* ``export.research``  — long-format CSV tables across patients + codebook
* ``export.report``    — HTML/PDF report from the record dict
* ``export.bundle``    — ZIP with report, videos, tracks, raw data, manifest
* ``export.pseudonyms``— stable pseudonyms kept locally, never exported
"""
