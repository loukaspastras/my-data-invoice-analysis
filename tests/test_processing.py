"""End-to-end tests for the API-LEFT-JOIN-PDF processing pipeline.

These run the REAL PDF parsing against the REAL invoices in test_invoices/,
with the API boundary supplied as a synthetic cache (so the suite stays
deterministic and offline). One optional `live` test exercises the genuine
myDATA API when MYDATA_LIVE=1 and credentials are available.
"""

import os
import io

import pytest

from mydata.processing import process_pdf_with_cached_data
from mydata.pdf_parser import extract_mark_from_pdf
from conftest import all_invoice_paths, load_as_upload, SAMPLE_MARK, SAMPLE_PDF


def _cache(mark, details, issue_date="2026-05-04"):
    return {mark: {"invoiceHeader": {"issueDate": issue_date}, "invoiceDetails": details}}


# ------------------------------------------------------------------
# Hand-verified end-to-end: sample invoice, line 1
# ------------------------------------------------------------------

def test_sample_invoice_full_extraction():
    details = [{"lineNumber": "1", "quantity": "340", "netValue": "234.60", "vatAmount": "56.30"}]
    cache = _cache(SAMPLE_MARK, details)

    rows, err, rate = process_pdf_with_cached_data(load_as_upload(SAMPLE_PDF), "citadel trade", cache)

    assert err is None and rate is False
    assert len(rows) == 1
    r = rows[0]
    assert r["MARK"] == SAMPLE_MARK
    assert r["Ημερομηνία"] == "2026-05-04"
    assert r["Γραμμή"] == 1
    assert r["Κωδικός"] == "MAT.198401"
    # multi-line description captured across all three text rows
    for token in ("Jet", "Lighter", "Barrel", "Cleveland", "Rubberized", "Black"):
        assert token in r["Περιγραφή"]
    assert r["Ποσότητα"] == "340"
    assert r["Καθαρή Αξία"] == 234.60
    assert r["ΦΠΑ"] == 56.30
    assert r["Σύνολο"] == 290.90
    assert r["Επιχείρηση"] == "citadel trade"


# ------------------------------------------------------------------
# LEFT JOIN contract: every API line is returned, even unmatched ones
# ------------------------------------------------------------------

def test_left_join_returns_all_api_lines_even_when_unmatched():
    # The sample PDF has only line 1; lines 2 & 3 cannot be found in the PDF,
    # but the API data must still come through with empty code/description.
    details = [
        {"lineNumber": "1", "quantity": "340", "netValue": "234.60", "vatAmount": "56.30"},
        {"lineNumber": "2", "quantity": "5", "netValue": "10.00", "vatAmount": "2.40"},
        {"lineNumber": "3", "quantity": "7", "netValue": "20.00", "vatAmount": "4.80"},
    ]
    rows, err, _ = process_pdf_with_cached_data(load_as_upload(SAMPLE_PDF), "acc", _cache(SAMPLE_MARK, details))

    assert err is None
    assert len(rows) == 3
    by_line = {r["Γραμμή"]: r for r in rows}
    # unmatched lines still carry correct money, just no code/desc
    assert by_line[2]["Κωδικός"] == "" and by_line[2]["Περιγραφή"] == ""
    assert by_line[2]["Καθαρή Αξία"] == 10.00
    assert by_line[3]["Σύνολο"] == 24.80


def test_total_is_net_plus_vat():
    details = [{"lineNumber": "1", "quantity": "1", "netValue": "100.00", "vatAmount": "24.00"}]
    rows, _, _ = process_pdf_with_cached_data(load_as_upload(SAMPLE_PDF), "acc", _cache(SAMPLE_MARK, details))
    assert rows[0]["Σύνολο"] == 124.00


# ------------------------------------------------------------------
# Error paths
# ------------------------------------------------------------------

def test_mark_not_in_cache_returns_error():
    rows, err, rate = process_pdf_with_cached_data(load_as_upload(SAMPLE_PDF), "acc", {})
    assert rows == []
    assert err is not None and SAMPLE_MARK in err
    assert rate is False


def test_no_mark_in_file_returns_error():
    fake = io.BytesIO(b"not a pdf at all")
    fake.name = "junk.pdf"
    rows, err, rate = process_pdf_with_cached_data(fake, "acc", {})
    assert rows == []
    assert "MARK" in err


def test_invoice_with_no_detail_lines():
    cache = _cache(SAMPLE_MARK, [])
    rows, err, _ = process_pdf_with_cached_data(load_as_upload(SAMPLE_PDF), "acc", cache)
    assert rows == []
    assert err is not None


# ------------------------------------------------------------------
# Broad end-to-end across ALL real invoices
# ------------------------------------------------------------------

@pytest.mark.parametrize("pdf_path", all_invoice_paths(), ids=lambda p: p.name)
def test_pipeline_runs_on_every_real_invoice(pdf_path):
    """For each real PDF: a one-line synthetic cache must yield exactly one
    row with correct money fields and no crash — proving the parse + LEFT JOIN
    pipeline is robust across the whole invoice set.

    The cache is keyed by the MARK the pipeline actually extracts (not the
    filename), so this stays decoupled from the credit-note MARK bug that
    test_pdf_parser documents separately."""
    mark = extract_mark_from_pdf(load_as_upload(pdf_path))
    details = [{"lineNumber": "1", "quantity": "1", "netValue": "10.00", "vatAmount": "2.40"}]
    rows, err, rate = process_pdf_with_cached_data(load_as_upload(pdf_path), "acc", _cache(mark, details))

    assert err is None, f"{pdf_path.name}: {err}"
    assert rate is False
    assert len(rows) == 1
    r = rows[0]
    assert r["MARK"] == mark
    assert r["Καθαρή Αξία"] == 10.00
    assert r["ΦΠΑ"] == 2.40
    assert r["Σύνολο"] == 12.40
    # code/description are best-effort enrichments; strings either way
    assert isinstance(r["Κωδικός"], str)
    assert isinstance(r["Περιγραφή"], str)


def test_mark_extraction_consistent_with_processing():
    # the MARK the pipeline reports must equal the one extract_mark_from_pdf sees
    for path in all_invoice_paths()[:5]:
        upload = load_as_upload(path)
        direct = extract_mark_from_pdf(upload)
        details = [{"lineNumber": "1", "quantity": "1", "netValue": "1", "vatAmount": "0"}]
        rows, _, _ = process_pdf_with_cached_data(load_as_upload(path), "acc", _cache(direct, details))
        assert rows[0]["MARK"] == direct


# ------------------------------------------------------------------
# Optional: genuine end-to-end against the live myDATA API
# ------------------------------------------------------------------

@pytest.mark.live
@pytest.mark.skipif(os.environ.get("MYDATA_LIVE") != "1",
                    reason="live API test; set MYDATA_LIVE=1 to run")
def test_live_api_end_to_end():
    import json
    from mydata.api import fetch_all_invoices_bulk

    with open("mydata_credentials.json", encoding="utf-8") as f:
        creds = json.load(f)
    name = next(iter(creds))
    uid, sk = creds[name]["uid"], creds[name]["sk"]

    invoices, err, rate = fetch_all_invoices_bulk(uid, sk)
    assert err is None, err
    assert SAMPLE_MARK in invoices, "sample MARK should be in this account's transmitted docs"

    rows, err, _ = process_pdf_with_cached_data(load_as_upload(SAMPLE_PDF), name, invoices)
    assert err is None
    assert rows[0]["MARK"] == SAMPLE_MARK
    assert rows[0]["Κωδικός"] == "MAT.198401"
