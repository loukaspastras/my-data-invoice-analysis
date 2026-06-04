"""Tests for the formatted-Excel export used by the download button.

Uses fully synthetic data — the exporter only formats; it never depends on real
invoice values.
"""

import io

import pandas as pd
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from mydata.excel_export import dataframe_to_xlsx

COLUMNS = ['Επιχείρηση', 'Αρχείο', 'MARK', 'Σειρά', 'Α/Α', 'Τύπος', 'Ημερομηνία',
           'Γραμμή', 'Κωδικός', 'Περιγραφή', 'Ποσότητα', 'Καθαρή Αξία', 'ΦΠΑ', 'Σύνολο']

FAKE_MARK = '123456789012345'   # synthetic 15-digit id


def _df():
    rows = [
        # a sale (positive)
        ['ACME', 'a.pdf', FAKE_MARK, 'SER', '1', '1.1', '2026-01-02',
         1, 'SKU.001', 'Widget', 10, 100.00, 24.00, 124.00],
        # a credit note (negated, invoiceType 5.x)
        ['ACME', 'b.pdf', '123456789012999', 'CRD', '2', '5.1', '2026-01-03',
         1, 'SKU.002', 'Returned widget', -4, -50.00, -12.00, -62.00],
    ]
    return pd.DataFrame(rows, columns=COLUMNS)


def _load(xlsx_bytes):
    return load_workbook(io.BytesIO(xlsx_bytes))


def test_returns_nonempty_bytes():
    data = dataframe_to_xlsx(_df())
    assert isinstance(data, (bytes, bytearray)) and len(data) > 0


def test_structure_table_and_freeze():
    ws = _load(dataframe_to_xlsx(_df())).active
    assert ws.title == "Τιμολόγια"
    assert "Invoices" in ws.tables
    assert ws.freeze_panes == "A2"
    assert [ws.cell(1, c).value for c in range(1, len(COLUMNS) + 1)] == COLUMNS


def test_mark_and_type_kept_as_text():
    ws = _load(dataframe_to_xlsx(_df())).active
    mark = ws.cell(2, COLUMNS.index('MARK') + 1)
    typ = ws.cell(2, COLUMNS.index('Τύπος') + 1)
    assert mark.value == FAKE_MARK and isinstance(mark.value, str)
    assert mark.number_format == '@'
    assert typ.value == '1.1' and typ.number_format == '@'   # not the number 1.1


def test_dates_are_real_dates():
    import datetime
    ws = _load(dataframe_to_xlsx(_df())).active
    cell = ws.cell(2, COLUMNS.index('Ημερομηνία') + 1)
    assert isinstance(cell.value, (datetime.date, datetime.datetime))
    assert cell.number_format == 'DD/MM/YYYY'


def test_money_format_and_negative_credit_note():
    ws = _load(dataframe_to_xlsx(_df())).active
    net_col = COLUMNS.index('Καθαρή Αξία') + 1
    assert '€' in ws.cell(2, net_col).number_format
    assert ws.cell(3, net_col).value == -50.00          # credit note stays negative


def test_totals_row_sums_and_nets_out():
    ws = _load(dataframe_to_xlsx(_df())).active
    tr = ws.max_row                                     # totals row after 2 data rows
    net_col = COLUMNS.index('Καθαρή Αξία') + 1
    L = get_column_letter(net_col)
    assert ws.cell(tr, net_col).value == f"=SUM({L}2:{L}3)"
    # numeric net-out: 100.00 + (-50.00) = 50.00
    assert round(_df()['Καθαρή Αξία'].sum(), 2) == 50.00


def test_empty_dataframe_does_not_crash():
    data = dataframe_to_xlsx(pd.DataFrame(columns=COLUMNS))
    ws = _load(data).active
    assert [ws.cell(1, c).value for c in range(1, len(COLUMNS) + 1)] == COLUMNS
