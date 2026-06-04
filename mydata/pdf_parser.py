"""Pure PDF parsing logic — geometry + spatial extraction.

No Streamlit, no network. Everything here is deterministic and unit-testable.

ROBUST PDF PARSING WITH HEADER-BASED COLUMN DETECTION
"""

import re
import pdfplumber


def extract_mark_from_pdf(pdf_file):
    """Extract the document's OWN 15-digit MARK from page 1.

    Credit notes (Πιστωτικά) print a "Συσχετιζόμενο: <MARK>" line — the related
    original-invoice MARK — BEFORE the document's own MARK in the text. That
    related MARK belongs to a real invoice (often in the same batch), so naively
    taking the first 15-digit run would resolve to the WRONG document. We strip
    the Συσχετιζόμενο MARK first so only the document's own MARK can be returned.
    """
    try:
        with pdfplumber.open(pdf_file) as pdf:
            text = pdf.pages[0].extract_text() or ""
        # Remove the correlated/related MARK (credit notes) before searching.
        text = re.sub(r'Συσχετιζόμεν\w*\D{0,10}\d{15}', ' ', text)
        mark_match = re.search(r'\d{15}', text)
        return mark_match.group(0) if mark_match else None
    except: return None


def group_words_by_row(words, y_tolerance=3):
    """Group words by their Y position (row)"""
    if not words: return {}
    rows = {}
    for w in sorted(words, key=lambda x: x['top']):
        matched = None
        for row_y in rows:
            if abs(w['top'] - row_y) <= y_tolerance:
                matched = row_y
                break
        if matched:
            rows[matched].append(w)
        else:
            rows[w['top']] = [w]
    for y in rows:
        rows[y].sort(key=lambda w: w['x0'])
    return rows


def find_header_columns(words):
    """
    Find the header row and extract EXACT column positions.
    Header format: A/A | Κωδ. | Περιγραφή | Ποσότητα | Μ.Μ. | Τιμή | ...

    More flexible matching to handle variations across pages.
    """
    # Multiple possible keywords for each column (handles variations)
    header_patterns = {
        'aa': ['A/A', 'Α/Α', 'α/α', 'a/a', 'AA'],
        'code': ['Κωδ', 'κωδ', 'Kod', 'SKU'],
        'desc': ['Περιγραφή', 'περιγραφή', 'Περιγρ', 'Description'],
        'qty': ['Ποσότητα', 'ποσότητα', 'Ποσ.', 'Qty'],
        'price': ['Τιμή', 'τιμή', 'Price'],
        'value': ['Αξία', 'αξία', 'Value']
    }

    columns = {}
    for w in words:
        text = w['text']
        for col_name, patterns in header_patterns.items():
            if col_name not in columns:
                for pattern in patterns:
                    if text.startswith(pattern) or pattern in text:
                        columns[col_name] = {'x0': w['x0'], 'x1': w['x1'], 'y': w['top']}
                        break

    return columns


# Default column positions (fallback if header not found)
DEFAULT_COLUMNS = {
    'aa': {'x0': 20, 'x1': 45},
    'code': {'x0': 45, 'x1': 100},
    'desc': {'x0': 100, 'x1': 200},
    'qty': {'x0': 200, 'x1': 240}
}


def normalize_number(num):
    """Create variants of a number for matching (handles comma/dot differences)"""
    num_str = str(num).replace(',', '.')
    try:
        val = float(num_str)
        variants = [f"{val:.2f}".replace('.', ','), f"{val:.2f}"]
        if val == int(val):
            variants.append(str(int(val)))
        return variants
    except:
        return [num_str]


def find_line_in_page(rows, columns, api_line, line_no, used_codes, used_descs):
    """
    Try to find a specific API line in the page rows.
    Returns (code, description, y_position) or (None, None, None) if not found.

    Uses API anchors: lineNumber (REQUIRED), quantity, netValue, vatAmount
    """
    qty_variants = normalize_number(api_line.get('quantity', 0))
    net_variants = normalize_number(api_line.get('netValue', 0))
    vat_variants = normalize_number(api_line.get('vatAmount', 0))

    # Column boundaries from header (with generous defaults)
    aa_x_end = columns.get('aa', {}).get('x1', 50)
    desc_x_start = columns.get('desc', {}).get('x0', 100)
    qty_x_start = columns.get('qty', {}).get('x0', 200)

    best_match = None
    best_score = 0
    line_number_found = False

    for row_y, row_words in rows.items():
        score = 0
        has_line_number = False
        row_texts = [w['text'].replace(',', '.') for w in row_words]

        # FIRST: Check if line number exists in this row (REQUIRED)
        for w in row_words:
            if w['text'] == str(line_no) and w['x0'] < 60:
                has_line_number = True
                score += 10  # Line number is the PRIMARY anchor
                break

        # Only consider rows that have the correct line number
        if not has_line_number:
            continue

        # THEN: Check numerical anchors (for extra confidence)
        for v in qty_variants:
            if v.replace(',', '.') in row_texts:
                score += 1
                break
        for v in net_variants:
            if v.replace(',', '.') in row_texts:
                score += 1
                break
        for v in vat_variants:
            if v.replace(',', '.') in row_texts:
                score += 1
                break

        if score > best_score:
            best_score = score
            best_match = row_y
            line_number_found = True

    # Line number MUST be found - it's the unique identifier
    if not line_number_found or best_match is None:
        return None, None, None

    main_y = best_match

    # Extract code: On main row, after A/A column but before Description
    # Code should be on the SAME row as the line number (A/A)
    code = None
    for w in rows[main_y]:
        x = w['x0']
        text = w['text']

        # Code is after line number (x > aa_x_end) but before description (x < desc_x_start)
        # Using generous bounds
        if 40 < x < desc_x_start + 10:
            if text == str(line_no):  # Skip the line number itself
                continue
            if text.isdigit() and len(text) <= 2:  # Skip small numbers
                continue
            if text.lower() in ['τμχ', 'τεμ', 'kg', 'lt', 'pcs', 'eur', '€']:
                continue

            # Rule: Code ends in digit. Clean up any leaked description.
            match = re.match(r'^(.+\d)([^0-9]+)$', text)
            if match:
                code = match.group(1)
            else:
                code = text
            break

    # Validate: No duplicate codes
    if code and code in used_codes:
        code = None

    # Find Y boundaries for description extraction
    sorted_rows = sorted(rows.keys())
    main_idx = sorted_rows.index(main_y) if main_y in sorted_rows else -1

    if main_idx >= 0:
        # Look for next data row (skip empty space)
        y_up = main_y - 8
        if main_idx < len(sorted_rows) - 1:
            next_y = sorted_rows[main_idx + 1]
            y_down = (main_y + next_y) / 2 + 5
        else:
            y_down = main_y + 25
    else:
        y_up, y_down = main_y - 8, main_y + 25

    # Extract description: Between Description column and Quantity
    desc_words = []
    for row_y in sorted(rows.keys()):
        if y_up <= row_y <= y_down:
            for w in rows[row_y]:
                x = w['x0']
                text = w['text']

                # Description area: between desc column start and qty column start
                if desc_x_start - 20 < x < qty_x_start - 5:
                    if text.lower() in ['τμχ', 'τεμ', 'kg', 'lt', 'pcs', 'eur', '€']:
                        continue
                    if text.isdigit() and len(text) <= 2:
                        continue
                    # Skip numbers that look like prices/quantities
                    if re.match(r'^\d+[,\.]\d+$', text):
                        continue
                    desc_words.append(text)

    description = " ".join(desc_words) if desc_words else None

    # Validate: No duplicate descriptions
    if description and description in used_descs:
        description = None

    return code, description, main_y
