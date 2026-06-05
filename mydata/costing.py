"""Cost-of-goods join: enrich invoice rows with unit cost and net profit.

The cost table is a persistent Excel under database/ (gitignored — real business
data). Schema: ΚΩΔΙΚΟΣ, ΤΙΜΗ ΚΤΗΣΗΣ (ΠΕΡΙΓΡΑΦΗ optional).

    Τιμή Κτήσης  = unit cost looked up by SKU
    Καθαρό Κέρδος = Καθαρή Αξία − (Τιμή Κτήσης × Ποσότητα)

Because Ποσότητα is already negative for Πιστωτικά, returns subtract profit back
out automatically — no special casing.
"""

import os
import re
import unicodedata

import pandas as pd

COST_DB_PATH = os.path.join("database", "ΤΙΜΕΣ ΚΤΗΣΗΣ.xlsx")

# Required columns in the cost table (ΠΕΡΙΓΡΑΦΗ is optional / for the user's eyes).
SKU_HEADER = "ΚΩΔΙΚΟΣ"
COST_HEADER = "ΤΙΜΗ ΚΤΗΣΗΣ"

# Output column names added to the export.
COL_UNIT_COST = "Τιμή Κτήσης"
COL_PROFIT = "Καθαρό Κέρδος"


def normalize_sku(value):
    """Normalize a SKU for joining: NFKC, trim, collapse inner spaces, uppercase."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)          # avoid '198401.0' from numeric cells
    s = unicodedata.normalize("NFKC", str(value)).strip()
    s = re.sub(r"\s+", " ", s)
    return s.upper()


def _norm_header(c):
    return re.sub(r"\s+", " ", str(c).strip()).upper()


def parse_cost_table(source):
    """Read + validate a cost table.

    Returns (cost_map, error, info):
      - cost_map: {normalized_sku: unit_cost} on success, else None
      - error:    a Greek error message on failure, else None
      - info:     {'count', 'duplicates', 'invalid_rows'} on success
    """
    try:
        df = pd.read_excel(source)
    except Exception as e:
        return None, f"❌ Δεν μπόρεσα να διαβάσω το αρχείο Excel: {e}", {}

    headers = {_norm_header(c): c for c in df.columns}
    missing = [h for h in (SKU_HEADER, COST_HEADER) if h not in headers]
    if missing:
        found = ", ".join(str(c) for c in df.columns) or "(καμία στήλη)"
        return None, (
            "❌ Λάθος δομή αρχείου. Λείπουν οι στήλες: " + ", ".join(missing) + ". "
            f"Περίμενα τις στήλες «{SKU_HEADER}» και «{COST_HEADER}». Βρήκα: {found}."
        ), {}

    skus = df[headers[SKU_HEADER]]
    costs = pd.to_numeric(df[headers[COST_HEADER]], errors="coerce")

    cost_map = {}
    duplicates = []
    invalid = 0
    for raw_sku, cost in zip(skus, costs):
        key = normalize_sku(raw_sku)
        if not key:
            continue
        if pd.isna(cost):
            invalid += 1
            continue
        if key in cost_map:
            duplicates.append(key)
        cost_map[key] = float(cost)      # last value wins

    if not cost_map:
        return None, (
            f"❌ Δεν βρέθηκε καμία έγκυρη γραμμή (ΚΩΔΙΚΟΣ + αριθμητική «{COST_HEADER}»)."
        ), {}

    info = {"count": len(cost_map), "duplicates": sorted(set(duplicates)), "invalid_rows": invalid}
    return cost_map, None, info


def load_cost_db(path=COST_DB_PATH):
    """Load the persisted cost table into a cost_map, or None if missing/invalid."""
    if not os.path.exists(path):
        return None
    cost_map, error, _ = parse_cost_table(path)
    return cost_map if not error else None


def save_cost_db(file_bytes, path=COST_DB_PATH):
    """Persist uploaded cost-table bytes, replacing the existing database file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(file_bytes)


def join_costs(df, cost_map):
    """Append Τιμή Κτήσης and Καθαρό Κέρδος to df.

    Returns (enriched_df, unmatched_skus) where unmatched_skus is the sorted list
    of non-empty SKUs in df that had no match in the cost table.
    """
    df = df.copy()
    unit_costs, profits = [], []
    unmatched = set()

    for _, row in df.iterrows():
        raw = row.get("Κωδικός", "")
        key = normalize_sku(raw)
        unit = cost_map.get(key) if key else None

        if unit is None:
            unit_costs.append(None)
            profits.append(None)
            if key:                      # has a SKU but no cost match
                unmatched.add(str(raw).strip())
            continue

        unit_costs.append(unit)
        try:
            qty = float(row.get("Ποσότητα", 0) or 0)
            net = float(row.get("Καθαρή Αξία", 0) or 0)
            profits.append(round(net - unit * qty, 2))
        except (TypeError, ValueError):
            profits.append(None)

    df[COL_UNIT_COST] = unit_costs
    df[COL_PROFIT] = profits
    return df, sorted(unmatched)
