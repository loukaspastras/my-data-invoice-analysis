"""Shared pytest fixtures and helpers.

The suite runs against the REAL invoice PDFs in test_invoices/ (gitignored as
customer PII). Each filename embeds the 15-digit MARK, which gives ground-truth
for MARK extraction without any network access.

NO real invoice data lives in committed test code. The hand-verified ground
truth for the sample invoice is read from test_invoices/expected.json (also
gitignored). When that data is absent (e.g. a fresh clone), the dependent tests
skip cleanly rather than fail.
"""

import io
import re
import sys
import json
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INVOICE_DIR = PROJECT_ROOT / "test_invoices"
EXPECTED_FILE = INVOICE_DIR / "expected.json"

# Make the `mydata` package importable no matter how pytest is invoked.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _load_expected():
    if EXPECTED_FILE.exists():
        try:
            return json.loads(EXPECTED_FILE.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


# Loaded once at collection time; None when the gitignored fixture is absent.
EXPECTED = _load_expected()


def mark_from_filename(path):
    """Pull the 15-digit MARK out of an invoice filename."""
    m = re.search(r"\d{15}", Path(path).name)
    return m.group(0) if m else None


def all_invoice_paths():
    """Every real invoice PDF, sorted for deterministic test ordering.

    Empty when test_invoices/ is absent (fresh clone) -> parametrized tests
    over this list are skipped automatically.
    """
    if not INVOICE_DIR.exists():
        return []
    return sorted(p for p in INVOICE_DIR.glob("*.pdf"))


def load_as_upload(path):
    """Return a BytesIO that mimics a Streamlit UploadedFile (has .name).

    The processing code calls f.seek(0), pdfplumber.open(f) and reads f.name,
    so an in-memory bytes buffer with a .name attribute is a faithful stand-in.
    """
    data = Path(path).read_bytes()
    buf = io.BytesIO(data)
    buf.name = Path(path).name
    return buf


@pytest.fixture
def invoice_dir():
    return INVOICE_DIR


@pytest.fixture
def sample():
    """Hand-verified ground truth for one invoice, loaded from the gitignored
    fixture. Skips the test when the fixture or its PDF is unavailable."""
    if not EXPECTED:
        pytest.skip("requires gitignored test_invoices/expected.json")
    pdf_path = INVOICE_DIR / EXPECTED["sample_pdf"]
    if not pdf_path.exists():
        pytest.skip("requires gitignored sample invoice PDF")
    return {**EXPECTED, "pdf_path": pdf_path}
