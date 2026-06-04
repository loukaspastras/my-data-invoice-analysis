"""Shared pytest fixtures and helpers.

The test suite runs against the REAL invoice PDFs in test_invoices/.
Each filename embeds the 15-digit MARK (e.g. printinvoice400013434431428.pdf),
which gives us ground-truth for MARK extraction without any network access.
"""

import io
import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INVOICE_DIR = PROJECT_ROOT / "test_invoices"

# Make the `mydata` package importable no matter how pytest is invoked.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# A specific invoice we have hand-verified down to the line level.
SAMPLE_MARK = "400013434431428"
SAMPLE_PDF = INVOICE_DIR / f"printinvoice{SAMPLE_MARK}.pdf"


def mark_from_filename(path):
    """Pull the 15-digit MARK out of an invoice filename."""
    m = re.search(r"\d{15}", Path(path).name)
    return m.group(0) if m else None


def all_invoice_paths():
    """Every real invoice PDF, sorted for deterministic test ordering."""
    return sorted(INVOICE_DIR.glob("*.pdf"))


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
def sample_pdf_path():
    return SAMPLE_PDF


@pytest.fixture
def sample_upload():
    return load_as_upload(SAMPLE_PDF)
