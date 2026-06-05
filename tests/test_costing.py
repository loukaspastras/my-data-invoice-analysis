"""Tests for the cost-of-goods join (mydata.costing). Fully synthetic data."""

import pandas as pd
import pytest

from mydata.costing import (
    normalize_sku, parse_cost_table, join_costs, load_cost_db, save_cost_db,
    COL_LINE_COST, COL_PROFIT,
)


def _write_cost_xlsx(path, rows, headers=("ΚΩΔΙΚΟΣ", "ΠΕΡΙΓΡΑΦΗ", "ΤΙΜΗ ΚΤΗΣΗΣ")):
    pd.DataFrame(rows, columns=list(headers)).to_excel(path, index=False)
    return str(path)


# ------------------------------------------------------------------ normalize
@pytest.mark.parametrize("raw,expected", [
    ("  abc.001 ", "ABC001"),        # trim + dot removed + upper
    ("abc.001", "ABC001"),
    ("MAT214041", "MAT214041"),      # already dot-free
    ("MAT.214041", "MAT214041"),     # dot stripped -> equals the dot-free form
    ("XY 0001", "XY 0001"),          # inner single space preserved
    ("XY   0001", "XY 0001"),        # collapsed
    (123456.0, "123456"),            # numeric cell -> no '.0'
    (None, ""),
    ("", ""),
])
def test_normalize_sku(raw, expected):
    assert normalize_sku(raw) == expected


def test_normalize_makes_dotted_and_undotted_equal():
    assert normalize_sku("MAT.214041") == normalize_sku("mat214041")


# --------------------------------------------------------------- parse table
def test_parse_valid(tmp_path):
    p = _write_cost_xlsx(tmp_path / "c.xlsx",
                         [["ABC.001", "x", 0.10], ["DEF.002", "y", 2.45]])
    cost_map, err, info = parse_cost_table(p)
    assert err is None
    assert cost_map == {"ABC001": 0.10, "DEF002": 2.45}    # keys normalized (dots dropped)
    assert info["count"] == 2


def test_parse_header_case_and_space_tolerant(tmp_path):
    p = _write_cost_xlsx(tmp_path / "c.xlsx", [["A", 1.0]],
                         headers=(" κωδικοσ ", "τιμη   κτησησ"))
    cost_map, err, _ = parse_cost_table(p)
    assert err is None and cost_map == {"A": 1.0}


def test_parse_missing_cost_column_greek_error(tmp_path):
    p = _write_cost_xlsx(tmp_path / "c.xlsx", [["A", "desc"]],
                         headers=("ΚΩΔΙΚΟΣ", "ΠΕΡΙΓΡΑΦΗ"))
    cost_map, err, _ = parse_cost_table(p)
    assert cost_map is None
    assert "ΤΙΜΗ ΚΤΗΣΗΣ" in err and "Λάθος δομή" in err


def test_parse_non_numeric_prices_counted(tmp_path):
    p = _write_cost_xlsx(tmp_path / "c.xlsx",
                         [["A", "x", "abc"], ["B", "y", 1.5]])
    cost_map, err, info = parse_cost_table(p)
    assert err is None
    assert cost_map == {"B": 1.5}
    assert info["invalid_rows"] == 1


def test_parse_all_invalid_is_error(tmp_path):
    p = _write_cost_xlsx(tmp_path / "c.xlsx", [["A", "x", "abc"]])
    cost_map, err, _ = parse_cost_table(p)
    assert cost_map is None and "καμία έγκυρη γραμμή" in err


def test_parse_duplicates_last_wins_and_reported(tmp_path):
    p = _write_cost_xlsx(tmp_path / "c.xlsx",
                         [["A", "x", 1.0], ["a", "y", 2.0]])
    cost_map, err, info = parse_cost_table(p)
    assert err is None
    assert cost_map == {"A": 2.0}           # normalized 'a' == 'A', last wins
    assert info["duplicates"] == ["A"]


# ------------------------------------------------------------------ join
def _invoice_df(rows):
    cols = ["Κωδικός", "Ποσότητα", "Καθαρή Αξία"]
    return pd.DataFrame(rows, columns=cols)


def test_join_sale_profit():
    df = _invoice_df([["ABC001", 340, 234.60]])
    out, unmatched = join_costs(df, {"ABC001": 0.10})
    assert unmatched == []
    assert out[COL_LINE_COST].iloc[0] == round(0.10 * 340, 2)         # 34.00
    assert out[COL_PROFIT].iloc[0] == round(234.60 - 0.10 * 340, 2)   # 200.60


def test_join_credit_note_reverses():
    # credit note: qty and net already negative -> cost and profit both reverse
    df = _invoice_df([["DEF002", -2, -9.80]])
    out, _ = join_costs(df, {"DEF002": 2.45})
    assert out[COL_LINE_COST].iloc[0] == round(2.45 * -2, 2)          # -4.90
    assert out[COL_PROFIT].iloc[0] == round(-9.80 - 2.45 * (-2), 2)   # -4.90


def test_join_unmatched_sku_blank_and_warned():
    df = _invoice_df([["NOPE.1", 5, 50.0]])
    out, unmatched = join_costs(df, {"ABC001": 0.10})
    assert unmatched == ["NOPE.1"]                  # original SKU shown in the warning
    assert pd.isna(out[COL_LINE_COST].iloc[0])
    assert pd.isna(out[COL_PROFIT].iloc[0])


def test_join_blank_sku_not_in_unmatched():
    df = _invoice_df([["", 1, 10.0]])
    out, unmatched = join_costs(df, {"ABC001": 0.10})
    assert unmatched == []
    assert pd.isna(out[COL_LINE_COST].iloc[0])


def test_join_matches_across_dot_space_case():
    # invoice 'mat.214041' (dotted/lower) must match cost key 'MAT214041'
    df = _invoice_df([["  mat.214041 ", 10, 100.0]])
    out, unmatched = join_costs(df, {"MAT214041": 1.0})
    assert unmatched == []
    assert out[COL_LINE_COST].iloc[0] == round(1.0 * 10, 2)
    assert out[COL_PROFIT].iloc[0] == round(100.0 - 1.0 * 10, 2)


# ------------------------------------------------------------- db load/save
def test_load_cost_db_missing_returns_none(tmp_path):
    assert load_cost_db(str(tmp_path / "nope.xlsx")) is None


def test_save_then_load_roundtrip(tmp_path):
    p = str(tmp_path / "sub" / "ΤΙΜΕΣ.xlsx")
    src = _write_cost_xlsx(tmp_path / "src.xlsx", [["A", "x", 3.0]])
    with open(src, "rb") as f:
        save_cost_db(f.read(), path=p)
    assert load_cost_db(p) == {"A": 3.0}
