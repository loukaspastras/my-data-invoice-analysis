"""Render the invoice DataFrame to a properly formatted .xlsx (bytes).

Used by the Streamlit download button so the user gets a ready-to-use Excel —
styled table, frozen header, MARK/Τύπος kept as text, € money format with red
negatives (credit notes), real dates, and a totals row that nets out because
Πιστωτικά carry negative values.
"""

import io

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

# Columns that must stay textual (IDs / codes — never numbers or dates).
TEXT_COLS = ("MARK", "Σειρά", "Α/Α", "Τύπος", "Κωδικός")
DATE_COLS = ("Ημερομηνία",)
MONEY_COLS = ("Καθαρή Αξία", "ΦΠΑ", "Σύνολο")
QTY_COLS = ("Ποσότητα",)
CENTER_INT_COLS = ("Γραμμή",)

MONEY_FMT = '#,##0.00" €";[Red]-#,##0.00" €"'
QTY_FMT = '#,##0.##;[Red]-#,##0.##'
WIDTH_CAPS = {"Περιγραφή": 55, "Αρχείο": 40}


def dataframe_to_xlsx(df, sheet_name="Τιμολόγια"):
    """Return raw .xlsx bytes for the given invoice DataFrame."""
    df = df.copy()
    cols = list(df.columns)
    idx = {c: i + 1 for i, c in enumerate(cols)}
    n = len(df)

    # Normalize textual columns; pre-parse date columns.
    for c in TEXT_COLS:
        if c in df.columns:
            df[c] = df[c].apply(lambda v: "" if pd.isna(v) else str(v))
    parsed_dates = {c: pd.to_datetime(df[c], errors="coerce") for c in DATE_COLS if c in df.columns}

    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws.append(cols)
    for ri in range(n):
        row = []
        for c in cols:
            if c in parsed_dates:
                d = parsed_dates[c].iloc[ri]
                row.append(None if pd.isna(d) else d.date())
            else:
                row.append(df[c].iloc[ri])
        ws.append(row)

    def cells(col):
        L = get_column_letter(idx[col])
        return [ws[f"{L}{r}"] for r in range(2, n + 2)]

    for c in MONEY_COLS:
        if c in idx:
            for cell in cells(c):
                cell.number_format = MONEY_FMT
                cell.alignment = Alignment(horizontal="right")
    for c in QTY_COLS:
        if c in idx:
            for cell in cells(c):
                cell.number_format = QTY_FMT
                cell.alignment = Alignment(horizontal="right")
    for c in CENTER_INT_COLS:
        if c in idx:
            for cell in cells(c):
                cell.number_format = "0"
                cell.alignment = Alignment(horizontal="center")
    for c in DATE_COLS:
        if c in idx:
            for cell in cells(c):
                cell.number_format = "DD/MM/YYYY"
    for c in TEXT_COLS:
        if c in idx:
            for cell in cells(c):
                cell.number_format = "@"

    # Totals row (nets out: credit notes are negative).
    present_money = [c for c in MONEY_COLS if c in idx]
    if n and present_money:
        tr = n + 2
        label_col = idx.get("Ποσότητα") or idx.get("Περιγραφή") or 1
        lc = ws.cell(tr, label_col, "ΣΥΝΟΛΟ")
        lc.alignment = Alignment(horizontal="right")
        for c in present_money:
            L = get_column_letter(idx[c])
            ws.cell(tr, idx[c], f"=SUM({L}2:{L}{n + 1})").number_format = MONEY_FMT
        fill = PatternFill("solid", fgColor="DDEBF7")
        top = Border(top=Side(style="double"))
        for col in range(1, len(cols) + 1):
            cc = ws.cell(tr, col)
            cc.font = Font(bold=True)
            cc.border = top
            cc.fill = fill

    # Styled, filterable table over header + data (excluding totals row).
    if n:
        ref = f"A1:{get_column_letter(len(cols))}{n + 1}"
        tbl = Table(displayName="Invoices", ref=ref)
        tbl.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True,
                                            showFirstColumn=False, showLastColumn=False,
                                            showColumnStripes=False)
        ws.add_table(tbl)

    for col in range(1, len(cols) + 1):
        ws.cell(1, col).alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 22

    for c in cols:
        L = get_column_letter(idx[c])
        longest = max([len(str(c))] + [len(str(v)) for v in df[c].astype(str).tolist()] + [4])
        ws.column_dimensions[L].width = min(longest + 2, WIDTH_CAPS.get(c, 22))

    ws.freeze_panes = "A2"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
