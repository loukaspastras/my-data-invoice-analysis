"""End-to-end tests for the API-LEFT-JOIN-PDF processing pipeline.

These run the REAL PDF parsing against the REAL invoices in test_invoices/,
with the API boundary supplied as a synthetic cache (so the suite stays
deterministic and offline). Ground truth for the hand-verified sample is read
from the gitignored test_invoices/expected.json — no invoice data is hardcoded
here. One optional `live` test exercises the genuine myDATA API when
MYDATA_LIVE=1 and credentials are available.
"""

import os
import io

import pytest

from mydata.processing import process_pdf_with_cached_data
from mydata.pdf_parser import extract_mark_from_pdf
from conftest import all_invoice_paths, load_as_upload


def _cache(mark, details, issue_date="2026-01-01", series="SER", aa="1", invoice_type="1.1"):
    return {mark: {"invoiceHeader": {"issueDate": issue_date, "series": series,
                                     "aa": aa, "invoiceType": invoice_type},
                   "invoiceDetails": details}}


def _api_line(line, **overrides):
    """Build an API detail line from the fixture's line data."""
    base = {
        "lineNumber": line["lineNumber"],
        "quantity": line["quantity"],
        "netValue": line["netValue"],
        "vatAmount": line["vatAmount"],
    }
    base.update(overrides)
    return base


# ------------------------------------------------------------------
# Hand-verified end-to-end: sample invoice, line 1
# ------------------------------------------------------------------

def test_sample_invoice_full_extraction(sample):
    line = sample["line"]
    cache = _cache(sample["mark"], [_api_line(line)], issue_date=sample["issue_date"],
                   series=sample["series"], aa=sample["aa"], invoice_type=sample["invoice_type"])

    rows, err, rate = process_pdf_with_cached_data(load_as_upload(sample["pdf_path"]), sample["account"], cache)

    assert err is None and rate is False
    assert len(rows) == 1
    r = rows[0]
    assert r["MARK"] == sample["mark"]
    assert r["Σειρά"] == sample["series"]
    assert r["Α/Α"] == sample["aa"]
    assert r["Τύπος"] == sample["invoice_type"]
    assert r["Ημερομηνία"] == sample["issue_date"]
    assert r["Γραμμή"] == int(line["lineNumber"])
    assert r["Κωδικός"] == line["code"]
    # multi-line description captured across all its text rows
    for token in line["desc_tokens"]:
        assert token in r["Περιγραφή"]
    assert r["Ποσότητα"] == line["qty"]            # numeric, positive for a sale
    assert r["Καθαρή Αξία"] == line["net"]
    assert r["ΦΠΑ"] == line["vat"]
    assert r["Σύνολο"] == line["total"]
    assert r["Επιχείρηση"] == sample["account"]


def test_credit_note_values_are_negated(credit_sample):
    """A Πιστωτικό (invoiceType 5.x) must come back with every numeric value
    negated, so it nets out against the sale it reverses."""
    line = credit_sample["line"]
    cache = _cache(credit_sample["mark"], [_api_line(line)],
                   series=credit_sample["series"], aa=credit_sample["aa"],
                   invoice_type=credit_sample["invoice_type"])

    rows, err, _ = process_pdf_with_cached_data(load_as_upload(credit_sample["pdf_path"]), "acc", cache)

    assert err is None
    r = rows[0]
    assert r["Τύπος"] == credit_sample["invoice_type"]   # 5.x
    assert r["Τύπος"].startswith("5")
    assert r["Καθαρή Αξία"] == -float(line["netValue"])
    assert r["ΦΠΑ"] == -float(line["vatAmount"])
    assert r["Σύνολο"] == -(float(line["netValue"]) + float(line["vatAmount"]))
    assert r["Ποσότητα"] == -float(line["quantity"])     # ALL values negated


def test_credit_note_nets_out_against_its_sale(credit_sample):
    """Sale + its credit note for the same amounts should sum to zero."""
    line = credit_sample["line"]
    sale = process_pdf_with_cached_data(
        load_as_upload(credit_sample["pdf_path"]), "acc",
        _cache(credit_sample["mark"], [_api_line(line)], invoice_type="1.1"))[0]
    credit = process_pdf_with_cached_data(
        load_as_upload(credit_sample["pdf_path"]), "acc",
        _cache(credit_sample["mark"], [_api_line(line)], invoice_type="5.1"))[0]
    assert round(sale[0]["Καθαρή Αξία"] + credit[0]["Καθαρή Αξία"], 2) == 0.0
    assert round(sale[0]["Σύνολο"] + credit[0]["Σύνολο"], 2) == 0.0


# ------------------------------------------------------------------
# LEFT JOIN contract: every API line is returned, even unmatched ones
# ------------------------------------------------------------------

def test_left_join_returns_all_api_lines_even_when_unmatched(sample):
    # The sample PDF has only line 1; lines 2 & 3 cannot be found in the PDF,
    # but the API data must still come through with empty code/description.
    details = [
        _api_line(sample["line"]),
        {"lineNumber": "2", "quantity": "5", "netValue": "10.00", "vatAmount": "2.40"},
        {"lineNumber": "3", "quantity": "7", "netValue": "20.00", "vatAmount": "4.80"},
    ]
    rows, err, _ = process_pdf_with_cached_data(load_as_upload(sample["pdf_path"]), "acc", _cache(sample["mark"], details))

    assert err is None
    assert len(rows) == 3
    by_line = {r["Γραμμή"]: r for r in rows}
    # unmatched lines still carry correct money, just no code/desc
    assert by_line[2]["Κωδικός"] == "" and by_line[2]["Περιγραφή"] == ""
    assert by_line[2]["Καθαρή Αξία"] == 10.00
    assert by_line[3]["Σύνολο"] == 24.80


def test_total_is_net_plus_vat(sample):
    details = [{"lineNumber": "1", "quantity": "1", "netValue": "100.00", "vatAmount": "24.00"}]
    rows, _, _ = process_pdf_with_cached_data(load_as_upload(sample["pdf_path"]), "acc", _cache(sample["mark"], details))
    assert rows[0]["Σύνολο"] == 124.00


# ------------------------------------------------------------------
# Error paths
# ------------------------------------------------------------------

def test_mark_not_in_cache_returns_error(sample):
    rows, err, rate = process_pdf_with_cached_data(load_as_upload(sample["pdf_path"]), "acc", {})
    assert rows == []
    assert err is not None and sample["mark"] in err
    assert rate is False


def test_no_mark_in_file_returns_error():
    fake = io.BytesIO(b"not a pdf at all")
    fake.name = "junk.pdf"
    rows, err, rate = process_pdf_with_cached_data(fake, "acc", {})
    assert rows == []
    assert "MARK" in err


def test_invoice_with_no_detail_lines(sample):
    cache = _cache(sample["mark"], [])
    rows, err, _ = process_pdf_with_cached_data(load_as_upload(sample["pdf_path"]), "acc", cache)
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
        direct = extract_mark_from_pdf(load_as_upload(path))
        details = [{"lineNumber": "1", "quantity": "1", "netValue": "1", "vatAmount": "0"}]
        rows, _, _ = process_pdf_with_cached_data(load_as_upload(path), "acc", _cache(direct, details))
        assert rows[0]["MARK"] == direct


# ------------------------------------------------------------------
# Optional: genuine end-to-end against the live myDATA API
# ------------------------------------------------------------------

def _single_fetch(uid, sk, mark):
    """Fetch one transmitted invoice by MARK from the live myDATA API."""
    import requests, xmltodict
    h = {'aade-user-id': uid, 'ocp-apim-subscription-key': sk}
    r = requests.get('https://mydatapi.aade.gr/myDATA/RequestTransmittedDocs',
                     headers=h, params={'mark': str(int(mark) - 1)}, timeout=60)
    if r.status_code != 200:
        return None
    invs = ((xmltodict.parse(r.text).get('RequestedDoc') or {}).get('invoicesDoc') or {}).get('invoice', [])
    if not isinstance(invs, list):
        invs = [invs]
    return next((i for i in invs if str(i.get('mark')) == mark), None)


@pytest.mark.live
@pytest.mark.skipif(os.environ.get("MYDATA_LIVE") != "1",
                    reason="live API test; set MYDATA_LIVE=1 to run")
def test_live_api_end_to_end(sample, credit_sample):
    """Genuine end-to-end against myDATA: a sale stays positive, a credit note
    comes back negated, and the credit note resolves to its OWN MARK."""
    import json
    with open("mydata_credentials.json", encoding="utf-8") as f:
        creds = json.load(f)

    # Find a saved account whose transmitted docs include the sample invoice.
    acct = next((c for c in creds.values() if _single_fetch(c["uid"], c["sk"], sample["mark"])), None)
    if not acct:
        pytest.skip("no saved account can access the sample invoices")
    uid, sk = acct["uid"], acct["sk"]

    # SALES: invoiceType 1.x, positive net
    inv = _single_fetch(uid, sk, sample["mark"])
    assert inv["invoiceHeader"]["invoiceType"].startswith("1")
    rows, err, _ = process_pdf_with_cached_data(load_as_upload(sample["pdf_path"]), "live", {sample["mark"]: inv})
    assert err is None
    assert rows[0]["MARK"] == sample["mark"]
    assert rows[0]["Καθαρή Αξία"] > 0

    # CREDIT NOTE: invoiceType 5.x, negated values, OWN mark (not Συσχετιζόμενο)
    cn = _single_fetch(uid, sk, credit_sample["mark"])
    assert cn["invoiceHeader"]["invoiceType"].startswith("5")
    rows, err, _ = process_pdf_with_cached_data(load_as_upload(credit_sample["pdf_path"]), "live", {credit_sample["mark"]: cn})
    assert err is None
    assert rows[0]["MARK"] == credit_sample["mark"]
    assert rows[0]["Τύπος"].startswith("5")
    assert rows[0]["Καθαρή Αξία"] < 0
    assert rows[0]["Σύνολο"] < 0
