"""Orchestration: combine API data (source of truth) with PDF enrichment.

API.LEFT_JOIN(PDF):
- We ALWAYS return all API lines with quantity, VAT, etc.
- Code and Description from PDF are optional enrichments
- If PDF parsing fails, we still return API data with empty code/description
"""

import re
import pdfplumber

from .pdf_parser import (
    extract_mark_from_pdf,
    group_words_by_row,
    find_header_columns,
    find_line_in_page,
    DEFAULT_COLUMNS,
)


def process_pdf_with_cached_data(f, acc_name, invoices_cache, status_container=None):
    """
    Process PDF using API data as source of truth (LEFT JOIN logic).

    API.LEFT_JOIN(PDF):
    - We ALWAYS return all API lines with quantity, VAT, etc.
    - Code and Description from PDF are optional enrichments
    - If PDF parsing fails, we still return API data with empty code/description
    """
    f.seek(0)
    mark = extract_mark_from_pdf(f)

    if not mark:
        return [], "Δεν βρέθηκε MARK", False

    if status_container:
        status_container.write(f"🔍 MARK: `{mark}`")

    inv = invoices_cache.get(str(mark))

    if not inv:
        return [], f"MARK {mark} δεν βρέθηκε", False

    api_details = inv.get('invoiceDetails', [])
    if not isinstance(api_details, list):
        api_details = [api_details] if api_details else []

    if not api_details:
        return [], "Δεν υπάρχουν γραμμές", False

    # Track found codes/descriptions to prevent duplicates (Rule 3)
    used_codes = set()
    used_descs = set()

    # Map: line_no -> {code, description}
    pdf_enrichment = {}

    # Track which API lines still need to be found
    remaining_line_nos = set(int(l.get('lineNumber', i+1)) for i, l in enumerate(api_details))

    # Store first page's columns as fallback for subsequent pages
    fallback_columns = None

    f.seek(0)
    with pdfplumber.open(f) as pdf:
        if status_container:
            status_container.write(f"📄 PDF: {len(pdf.pages)} σελίδες, {len(api_details)} γραμμές API")

        for page_idx, page in enumerate(pdf.pages):
            if not remaining_line_nos:
                break  # All lines found

            words = page.extract_words()
            if not words:
                continue

            rows = group_words_by_row(words)
            columns = find_header_columns(words)

            # Use fallback if this page has no/incomplete header
            if len(columns) < 3:
                if fallback_columns:
                    columns = fallback_columns
                else:
                    columns = DEFAULT_COLUMNS
            else:
                # Save good columns as fallback for next pages
                if fallback_columns is None or len(columns) > len(fallback_columns):
                    fallback_columns = columns.copy()

            # Count matches on this page for debugging
            page_matches = 0

            # Process remaining API lines, looking for them in this page
            for i, api_line in enumerate(api_details):
                l_no = int(api_line.get('lineNumber', i + 1))

                if l_no not in remaining_line_nos:
                    continue  # Already found

                code, desc, found_y = find_line_in_page(
                    rows, columns, api_line, l_no, used_codes, used_descs
                )

                if found_y is not None:
                    # Check: Did we actually extract useful data?
                    # If BOTH code and desc are None, we might have matched the WRONG row
                    # (e.g., matched line 1's row when looking for line 12 due to same anchors)
                    # In this case, DON'T mark as found - try next page instead
                    if code is None and desc is None:
                        # False positive match - skip, will try next page
                        continue

                    # Successfully found this line with actual data
                    # Store y and page for second pass (description expansion)
                    pdf_enrichment[l_no] = {
                        'code': code,
                        'desc': desc,
                        'y': found_y,
                        'page_idx': page_idx
                    }
                    remaining_line_nos.discard(l_no)
                    page_matches += 1

                    if code:
                        used_codes.add(code)
                    if desc:
                        used_descs.add(desc)

            if status_container and page_matches > 0:
                status_container.write(f"  📄 Σελ.{page_idx+1}: {page_matches} matches")

    # ============================================================
    # SECOND PASS: Expand descriptions (capture multi-line text)
    # ============================================================
    # For each description found, look for additional text above/below
    # within the same horizontal boundaries (desc column → qty column)

    # First, collect all main_y positions per page (to avoid "stealing" from other descriptions)
    page_main_ys = {}  # page_idx -> list of main_y values
    for l_no, enrichment in pdf_enrichment.items():
        page_idx = enrichment.get('page_idx')
        main_y = enrichment.get('y')
        if page_idx is not None and main_y is not None:
            if page_idx not in page_main_ys:
                page_main_ys[page_idx] = []
            page_main_ys[page_idx].append(main_y)

    f.seek(0)
    with pdfplumber.open(f) as pdf:
        for l_no, enrichment in pdf_enrichment.items():
            if not enrichment.get('desc'):
                continue  # No description to expand

            page_idx = enrichment.get('page_idx')
            main_y = enrichment.get('y')
            original_desc = enrichment.get('desc', '')
            found_code = enrichment.get('code', '')

            if page_idx is None or main_y is None:
                continue

            if page_idx >= len(pdf.pages):
                continue

            page = pdf.pages[page_idx]
            words = page.extract_words()
            if not words:
                continue

            # Get column boundaries for this page
            columns = find_header_columns(words)
            if len(columns) < 3:
                columns = fallback_columns if fallback_columns else DEFAULT_COLUMNS

            desc_x_start = columns.get('desc', {}).get('x0', 100)
            qty_x_start = columns.get('qty', {}).get('x0', 200)

            # Group words by row
            rows = group_words_by_row(words)
            sorted_ys = sorted(rows.keys())

            # Get all main_y values for this page (other line items)
            other_main_ys = [y for y in page_main_ys.get(page_idx, []) if abs(y - main_y) > 3]

            def is_closer_to_current(row_y, current_y, others):
                """Check if row_y is closer to current_y than to any other anchor"""
                dist_to_current = abs(row_y - current_y)
                for other_y in others:
                    if abs(row_y - other_y) < dist_to_current:
                        return False  # Closer to another line item
                return True

            # Find rows ABOVE and BELOW main_y that might contain description continuation
            # Look within a reasonable vertical range (±25 pixels from main row)
            extra_desc_words_above = []
            extra_desc_words_below = []

            # Split original description into words for duplicate checking
            original_words = set(original_desc.split()) if original_desc else set()

            for row_y in sorted_ys:
                # Skip the main row itself (we already have its description)
                if abs(row_y - main_y) < 3:
                    continue

                # IMPORTANT: Only include text if it's closer to THIS line than to any other
                if not is_closer_to_current(row_y, main_y, other_main_ys):
                    continue  # This row belongs to another line item

                # Only look at rows close to the main row
                # Above: within 25 pixels
                # Below: within 25 pixels
                distance = row_y - main_y

                if -25 < distance < -2:  # Row is ABOVE main row
                    for w in rows[row_y]:
                        x = w['x0']
                        text = w['text']

                        # Must be within description column boundaries
                        if desc_x_start - 20 < x < qty_x_start - 5:
                            # Skip if already in original description (avoid duplicates)
                            if text in original_words:
                                continue
                            # Skip if it's a code we already identified
                            if found_code and text == found_code:
                                continue
                            # Skip if it's any used code
                            if text in used_codes:
                                continue
                            # Skip common non-description words
                            if text.lower() in ['τμχ', 'τεμ', 'kg', 'lt', 'pcs', 'eur', '€']:
                                continue
                            # Skip pure numbers (likely quantities/prices)
                            if re.match(r'^\d+[,\.]?\d*$', text):
                                continue
                            extra_desc_words_above.append(text)

                elif 2 < distance < 25:  # Row is BELOW main row
                    for w in rows[row_y]:
                        x = w['x0']
                        text = w['text']

                        # Must be within description column boundaries
                        if desc_x_start - 20 < x < qty_x_start - 5:
                            # Skip if already in original description (avoid duplicates)
                            if text in original_words:
                                continue
                            # Skip if it's a code we already identified
                            if found_code and text == found_code:
                                continue
                            # Skip if it's any used code
                            if text in used_codes:
                                continue
                            # Skip common non-description words
                            if text.lower() in ['τμχ', 'τεμ', 'kg', 'lt', 'pcs', 'eur', '€']:
                                continue
                            # Skip pure numbers
                            if re.match(r'^\d+[,\.]?\d*$', text):
                                continue
                            extra_desc_words_below.append(text)

            # Build expanded description: above + original + below
            expanded_parts = []
            if extra_desc_words_above:
                expanded_parts.append(" ".join(extra_desc_words_above))
            expanded_parts.append(original_desc)
            if extra_desc_words_below:
                expanded_parts.append(" ".join(extra_desc_words_below))

            expanded_desc = " ".join(expanded_parts)

            # Update the enrichment with expanded description
            if expanded_desc != original_desc:
                pdf_enrichment[l_no]['desc'] = expanded_desc

    # Build final results: API data LEFT JOIN PDF enrichment
    final_results = []
    for i, api_line in enumerate(api_details):
        l_no = int(api_line.get('lineNumber', i + 1))

        # Get PDF enrichment if available
        enrichment = pdf_enrichment.get(l_no, {})

        final_results.append({
            'Επιχείρηση': acc_name,
            'Αρχείο': f.name,
            'MARK': mark,
            'Ημερομηνία': inv.get('invoiceHeader', {}).get('issueDate', ''),
            'Γραμμή': l_no,
            'Κωδικός': enrichment.get('code', '') or '',
            'Περιγραφή': enrichment.get('desc', '') or '',
            'Ποσότητα': api_line.get('quantity', ''),
            'Καθαρή Αξία': float(api_line.get('netValue', 0)),
            'ΦΠΑ': float(api_line.get('vatAmount', 0)),
            'Σύνολο': float(api_line.get('netValue', 0)) + float(api_line.get('vatAmount', 0))
        })

    if status_container:
        found = len(pdf_enrichment)
        total = len(api_details)
        status_container.write(f"✅ {found}/{total} γραμμές με code/desc από PDF")

    return final_results, None, False
