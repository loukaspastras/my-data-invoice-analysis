"""Tests for the pure PDF parsing logic, exercised against real invoices."""

import io

import pdfplumber
import pytest

from mydata.pdf_parser import (
    extract_mark_from_pdf,
    group_words_by_row,
    find_header_columns,
    normalize_number,
    find_line_in_page,
    DEFAULT_COLUMNS,
)
from conftest import all_invoice_paths, mark_from_filename, load_as_upload


# ------------------------------------------------------------------
# extract_mark_from_pdf — ground truth is the MARK in each filename
# ------------------------------------------------------------------

@pytest.mark.parametrize("pdf_path", all_invoice_paths(), ids=lambda p: p.name)
def test_extract_mark_matches_filename(pdf_path):
    # Must return the document's OWN MARK for every invoice — including credit
    # notes, whose Συσχετιζόμενο (related) MARK must NOT be returned.
    expected = mark_from_filename(pdf_path)
    assert expected is not None, "filename should contain a 15-digit MARK"
    got = extract_mark_from_pdf(load_as_upload(pdf_path))
    assert got == expected


def test_credit_note_returns_own_mark_not_correlated():
    """On a credit note, the Συσχετιζόμενο MARK must never be returned."""
    import re
    for pdf_path in all_invoice_paths():
        with pdfplumber.open(pdf_path) as pdf:
            text = pdf.pages[0].extract_text() or ""
        if "Συσχετιζόμενο" not in text:
            continue
        m = re.search(r'Συσχετιζόμεν\w*\D{0,10}(\d{15})', text)
        if not m:
            continue
        correlated = m.group(1)
        own = mark_from_filename(pdf_path)
        got = extract_mark_from_pdf(load_as_upload(pdf_path))
        assert got == own, f"{pdf_path.name}: expected own MARK {own}, got {got}"
        assert got != correlated, f"{pdf_path.name}: returned the Συσχετιζόμενο MARK {correlated}"
        return  # one real credit note is enough to prove the behaviour
    pytest.skip("no credit-note PDF available (gitignored test_invoices/)")


def test_extract_mark_on_non_pdf_returns_none():
    assert extract_mark_from_pdf(io.BytesIO(b"definitely not a pdf")) is None


# ------------------------------------------------------------------
# group_words_by_row
# ------------------------------------------------------------------

def test_group_words_by_row_empty():
    assert group_words_by_row([]) == {}


def test_group_words_by_row_clusters_within_tolerance():
    words = [
        {"text": "a", "top": 100.0, "x0": 10, "x1": 20},
        {"text": "b", "top": 101.5, "x0": 30, "x1": 40},  # within y_tolerance=3 of 100
        {"text": "c", "top": 120.0, "x0": 5, "x1": 15},   # new row
    ]
    rows = group_words_by_row(words, y_tolerance=3)
    assert len(rows) == 2
    # the 100-row has two words, sorted left-to-right by x0
    first_row = rows[100.0]
    assert [w["text"] for w in first_row] == ["a", "b"]


def test_group_words_by_row_sorts_each_row_by_x():
    words = [
        {"text": "right", "top": 50.0, "x0": 90, "x1": 99},
        {"text": "left", "top": 50.0, "x0": 5, "x1": 9},
    ]
    rows = group_words_by_row(words, y_tolerance=3)
    assert [w["text"] for w in rows[50.0]] == ["left", "right"]


# ------------------------------------------------------------------
# normalize_number  (synthetic values — not tied to any invoice)
# ------------------------------------------------------------------

@pytest.mark.parametrize("value,expected_contains", [
    ("1234,56", ["1234,56", "1234.56"]),
    (1234.56, ["1234,56", "1234.56"]),
    ("7", ["7,00", "7.00", "7"]),      # integer also yields bare int variant
    (7.0, ["7,00", "7.00", "7"]),
])
def test_normalize_number_variants(value, expected_contains):
    variants = normalize_number(value)
    for exp in expected_contains:
        assert exp in variants


def test_normalize_number_non_numeric_passthrough():
    assert normalize_number("abc") == ["abc"]


# ------------------------------------------------------------------
# find_header_columns — real header on the sample invoice
# ------------------------------------------------------------------

def _words(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        return pdf.pages[0].extract_words()


def test_find_header_columns_detects_table_columns(sample):
    cols = find_header_columns(_words(sample["pdf_path"]))
    # the four columns the parser actually relies on
    for key in ("aa", "code", "desc", "qty"):
        assert key in cols, f"missing column {key}"
    # columns must be ordered left-to-right on the page
    assert cols["aa"]["x0"] < cols["code"]["x0"] < cols["desc"]["x0"] < cols["qty"]["x0"]


# ------------------------------------------------------------------
# find_line_in_page — locate line 1 on the sample invoice
# ------------------------------------------------------------------

def test_find_line_in_page_extracts_code_and_desc(sample):
    words = _words(sample["pdf_path"])
    rows = group_words_by_row(words)
    cols = find_header_columns(words)
    line = sample["line"]
    api_line = {"quantity": line["quantity"], "netValue": line["netValue"], "vatAmount": line["vatAmount"]}

    code, desc, y = find_line_in_page(rows, cols, api_line, int(line["lineNumber"]), set(), set())

    assert code == line["code"]
    assert any(tok in desc for tok in line["desc_tokens"])  # main-row description fragment
    assert y is not None


def test_find_line_in_page_missing_line_returns_none(sample):
    words = _words(sample["pdf_path"])
    rows = group_words_by_row(words)
    cols = find_header_columns(words)
    # line 99 does not exist on this single-line invoice
    api_line = {"quantity": "1", "netValue": "1", "vatAmount": "1"}
    assert find_line_in_page(rows, cols, api_line, 99, set(), set()) == (None, None, None)


def test_default_columns_shape():
    # the fallback must expose the four boundaries the extractor reads
    for key in ("aa", "code", "desc", "qty"):
        assert "x0" in DEFAULT_COLUMNS[key] and "x1" in DEFAULT_COLUMNS[key]
