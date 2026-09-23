"""Dependency-light structured extraction primitives.

This module intentionally does not change the existing markdown fetch contract.
It exposes a small, explicit result shape for callers that want structured
data from already-fetched HTML without an LLM or browser dependency.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any


@dataclass
class ExtractionResult:
    """Structured extraction result with explicit partial/error states."""

    status: str
    data: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "data": self.data, "errors": list(self.errors)}


class _StructuredHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.jsonld: list[str] = []
        self.tables: list[list[list[str]]] = []
        self._script_type = ""
        self._script_parts: list[str] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attr_map = {key.lower(): value or "" for key, value in attrs}
        if tag == "script":
            self._script_type = attr_map.get("type", "").lower()
            self._script_parts = []
        elif tag == "table" and self._table is None:
            self._table = []
        elif tag == "tr" and self._table is not None and self._row is None:
            self._row = []
        elif tag in {"th", "td"} and self._row is not None and self._cell_parts is None:
            self._cell_parts = []

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._script_type == "application/ld+json":
            self._script_parts.append(data)
        if self._cell_parts is not None:
            self._cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "script":
            if self._script_type == "application/ld+json":
                self.jsonld.append("".join(self._script_parts))
            self._script_type = ""
            self._script_parts = []
        elif tag in {"th", "td"} and self._cell_parts is not None and self._row is not None:
            self._row.append(_clean_cell("".join(self._cell_parts)))
            self._cell_parts = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if self._row:
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
            self._table = None


def _clean_cell(value: str) -> str:
    return " ".join(value.split())


def _parse_jsonld_blocks(blocks: list[str]) -> tuple[list[Any], list[str]]:
    values: list[Any] = []
    errors: list[str] = []
    for index, raw in enumerate(blocks, 1):
        try:
            values.append(json.loads(raw))
        except json.JSONDecodeError as exc:
            errors.append(f"json-ld block {index}: invalid JSON ({exc.msg})")
    return values, errors


def _normalize_table(rows: list[list[str]]) -> dict[str, Any] | None:
    if not rows:
        return None
    width = max(len(row) for row in rows)
    if width == 0:
        return None
    padded = [row + [""] * (width - len(row)) for row in rows]
    headers = padded[0]
    data = [dict(zip(headers, row, strict=False)) for row in padded[1:]]
    return {"headers": headers, "rows": data}


def extract_structured(html: str) -> ExtractionResult:
    """Extract JSON-LD blocks and HTML tables from one HTML document.

    ``success`` means at least one structured item was extracted and there
    were no parse errors. ``incomplete`` means useful data was extracted but
    one or more blocks/tables were malformed. ``error`` means the input is not
    text or parsing failed before any structured result could be produced.
    """
    if not isinstance(html, str) or not html.strip():
        return ExtractionResult("error", errors=["html must be non-empty text"])
    parser = _StructuredHTMLParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:  # noqa: BLE001 - parser must return a stable shape
        return ExtractionResult("error", errors=[f"HTML parse failed: {exc}"])

    jsonld, errors = _parse_jsonld_blocks(parser.jsonld)
    tables = [table for raw in parser.tables if (table := _normalize_table(raw))]
    data = {"json_ld": jsonld, "tables": tables}
    if not jsonld and not tables:
        if errors:
            return ExtractionResult("error", data=data, errors=errors)
        return ExtractionResult("incomplete", data=data, errors=["no structured data found"])
    return ExtractionResult("incomplete" if errors else "success", data=data, errors=errors)
