import streamlit as st
import pandas as pd
import pdfplumber
import requests
import xmltodict
import re
import io
import time
import json
import os
from datetime import datetime

# Αρχείο αποθήκευσης credentials
CREDS_FILE = "mydata_credentials.json"

# ============================================================
# ΔΙΑΧΕΙΡΙΣΗ CREDENTIALS & PDF HELPERS
# ============================================================

def load_saved_creds():
    if os.path.exists(CREDS_FILE):
        try:
            with open(CREDS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except: return {}
    return {}

def save_creds(name, uid, sk):
    creds = load_saved_creds()
    creds[name] = {"uid": uid, "sk": sk}
    with open(CREDS_FILE, "w", encoding="utf-8") as f:
        json.dump(creds, f, ensure_ascii=False, indent=4)

def delete_creds(name):
    creds = load_saved_creds()
    if name in creds:
        del creds[name]
        with open(CREDS_FILE, "w", encoding="utf-8") as f:
            json.dump(creds, f, ensure_ascii=False, indent=4)

def extract_mark_from_pdf(pdf_file):
    try:
        with pdfplumber.open(pdf_file) as pdf:
            text = pdf.pages[0].extract_text()
        mark_match = re.search(r'\d{15}', text)
        return mark_match.group(0) if mark_match else None
    except: return None

# ============================================================
# SPATIAL PARSING LOGIC
# ============================================================

# ============================================================
# ROBUST PDF PARSING WITH HEADER-BASED COLUMN DETECTION
# ============================================================

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

# ============================================================
# API - BULK FETCH ALL INVOICES
# ============================================================

def fetch_all_invoices_bulk(uid, sk, status_container=None):
    headers = {'aade-user-id': uid, 'ocp-apim-subscription-key': sk}
    url = 'https://mydatapi.aade.gr/myDATA/RequestTransmittedDocs'
    
    all_invoices = {}
    next_partition_key = None
    next_row_key = None
    page = 1
    
    while True:
        params = {'mark': '0'}
        
        if next_partition_key and next_row_key:
            params['nextPartitionKey'] = next_partition_key
            params['nextRowKey'] = next_row_key
        
        if status_container:
            status_container.write(f"📡 Λήψη σελίδας {page} από το API...")
        
        try:
            res = requests.get(url, headers=headers, params=params, timeout=60)
            
            if res.status_code == 429:
                return None, "⏳ Rate limit - Δοκιμάστε ξανά σε λίγα λεπτά", True
            if res.status_code == 401:
                return None, "🔐 Σφάλμα credentials", False
            if res.status_code != 200:
                return None, f"❌ HTTP {res.status_code}", False
            
            data = xmltodict.parse(res.text)
            requested_doc = data.get('RequestedDoc', {})
            invoices_doc = requested_doc.get('invoicesDoc', {})
            
            if invoices_doc:
                invoices = invoices_doc.get('invoice', [])
                if not isinstance(invoices, list):
                    invoices = [invoices] if invoices else []
                
                for inv in invoices:
                    mark = inv.get('mark')
                    if mark:
                        all_invoices[str(mark)] = inv
                
                if status_container:
                    status_container.write(f"✅ Σελίδα {page}: +{len(invoices)} (Σύνολο: {len(all_invoices)})")
            
            continuation = requested_doc.get('continuationToken', {})
            next_partition_key = continuation.get('nextPartitionKey')
            next_row_key = continuation.get('nextRowKey')
            
            if not next_partition_key or not next_row_key:
                break
            
            page += 1
            time.sleep(0.3)
            
        except requests.exceptions.Timeout:
            return None, "⏰ Timeout", True
        except Exception as e:
            return None, f"❌ {str(e)}", False
    
    if status_container:
        status_container.write(f"🎉 Βρέθηκαν **{len(all_invoices)}** invoices")
    
    return all_invoices, None, False

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

# ============================================================
# STREAMLIT CONFIG
# ============================================================

st.set_page_config(page_title="myDATA Invoice Parser", layout="wide")

# ============================================================
# SESSION STATE
# ============================================================

if 'active_accounts' not in st.session_state: 
    st.session_state['active_accounts'] = []
if 'invoices_cache' not in st.session_state:
    st.session_state['invoices_cache'] = {}
if 'all_rows' not in st.session_state:
    st.session_state['all_rows'] = []
if 'errors' not in st.session_state:
    st.session_state['errors'] = []

saved_creds = load_saved_creds()

# ============================================================
# SIDEBAR (Common for all tabs)
# ============================================================

with st.sidebar:
    st.header("🔐 Λογαριασμοί")
    
    if saved_creds:
        sel = st.selectbox("Αποθηκευμένα:", ["--"] + list(saved_creds.keys()), label_visibility="collapsed")
        
        col1, col2 = st.columns(2)
        with col1:
            if sel != "--" and st.button("➕ Ενεργοποίηση", use_container_width=True):
                acc = {"name": sel, **saved_creds[sel]}
                if acc not in st.session_state['active_accounts']:
                    st.session_state['active_accounts'].append(acc)
                    st.rerun()
        with col2:
            if sel != "--" and st.button("🗑️", use_container_width=True):
                delete_creds(sel)
                st.rerun()
    
    st.divider()
    
    with st.expander("➕ Νέο Login", expanded=not bool(saved_creds)):
        new_name = st.text_input("Όνομα", key="new_name")
        new_uid = st.text_input("User ID", key="new_uid")
        new_sk = st.text_input("Subscription Key", type="password", key="new_sk")
        
        if st.button("💾 Αποθήκευση", use_container_width=True):
            if new_name and new_uid and new_sk:
                save_creds(new_name, new_uid, new_sk)
                st.rerun()
    
    st.divider()
    
    st.subheader("✅ Ενεργοί")
    if st.session_state['active_accounts']:
        for i, acc in enumerate(st.session_state['active_accounts']):
            col1, col2 = st.columns([4, 1])
            col1.success(f"🏢 {acc['name']}")
            if col2.button("✖", key=f"rm_{i}"):
                st.session_state['active_accounts'].pop(i)
                if acc['name'] in st.session_state['invoices_cache']:
                    del st.session_state['invoices_cache'][acc['name']]
                st.rerun()
        
        st.divider()
        st.caption("📦 **Cache:**")
        for acc in st.session_state['active_accounts']:
            cached = len(st.session_state['invoices_cache'].get(acc['name'], {}))
            st.caption(f"{'✅' if cached else '⏳'} {acc['name']}: {cached or 'N/A'}")
        
        if st.button("🗑️ Clear Cache", use_container_width=True):
            st.session_state['invoices_cache'] = {}
            st.rerun()
    else:
        st.warning("⚠️ Προσθέστε λογαριασμό!")

# ============================================================
# MAIN TABS
# ============================================================

st.title("📄 myDATA Invoice Tools")

tab1, tab2 = st.tabs(["📥 Invoice Parser", "📊 Analytics Dashboard"])

# ============================================================
# TAB 1: INVOICE PARSER
# ============================================================

with tab1:
    st.header("Εξαγωγή Δεδομένων από PDF")
    st.caption("Bulk API fetch - 1 κλήση για όλα τα invoices!")
    
    with st.expander("📖 Οδηγίες", expanded=False):
        st.markdown("""
        1. **Προσθέστε λογαριασμό** από το sidebar (User ID + Subscription Key)
        2. **Ανεβάστε PDF** τιμολογίων
        3. **Πατήστε Έναρξη** - το app κατεβάζει ΟΛΑ τα invoices με 1 κλήση
        4. **Κατεβάστε Excel** με τα αποτελέσματα
        """)
    
    st.divider()
    
    if not st.session_state['active_accounts']:
        st.warning("👈 Προσθέστε έναν λογαριασμό από το sidebar.")
    
    uploaded_files = st.file_uploader("📤 Σύρετε PDF εδώ", type=['pdf'], accept_multiple_files=True, key="pdf_upload")
    
    if uploaded_files:
        st.info(f"📁 {len(uploaded_files)} αρχεία")
    
    if uploaded_files and st.session_state['active_accounts']:
        if st.button("🚀 Έναρξη Επεξεργασίας", type="primary", use_container_width=True):
            st.session_state['all_rows'] = []
            st.session_state['errors'] = []
            
            progress = st.progress(0)
            
            for acc_idx, acc in enumerate(st.session_state['active_accounts']):
                st.markdown(f"### 🏢 {acc['name']}")
                
                if acc['name'] not in st.session_state['invoices_cache'] or not st.session_state['invoices_cache'][acc['name']]:
                    with st.status(f"📡 Λήψη invoices...", expanded=True) as fetch_status:
                        invoices, err, rate_limited = fetch_all_invoices_bulk(acc['uid'], acc['sk'], fetch_status)
                        
                        if rate_limited or err:
                            st.error(f"❌ {err}")
                            continue
                        
                        st.session_state['invoices_cache'][acc['name']] = invoices
                        fetch_status.update(label=f"✅ {len(invoices)} invoices!", state="complete")
                else:
                    st.success(f"✅ Cache: {len(st.session_state['invoices_cache'][acc['name']])} invoices")
                
                invoices_cache = st.session_state['invoices_cache'].get(acc['name'], {})
                
                if not invoices_cache:
                    continue
                
                for f_idx, f in enumerate(uploaded_files):
                    progress.progress((acc_idx * len(uploaded_files) + f_idx + 1) / (len(st.session_state['active_accounts']) * len(uploaded_files)))
                    
                    col1, col2 = st.columns([1, 4])
                    with col1:
                        st.write(f"📄 {f.name[:25]}...")
                    
                    with col2:
                        detail = st.empty()
                        rows, err, _ = process_pdf_with_cached_data(f, acc['name'], invoices_cache, detail)
                        
                        if rows:
                            st.session_state['all_rows'].extend(rows)
                            detail.success(f"✅ {len(rows)} γραμμές")
                        elif err:
                            st.session_state['errors'].append({'file': f.name, 'account': acc['name'], 'error': err})
                            detail.error(f"❌ {err}")
            
            progress.progress(1.0)
            
            # Results
            st.markdown("---")
            st.subheader("📊 Αποτελέσματα")
            
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("✅ Γραμμές", len(st.session_state['all_rows']))
            with col2:
                st.metric("⚠️ Σφάλματα", len(st.session_state['errors']))
            with col3:
                if st.session_state['all_rows']:
                    total = sum(r['Σύνολο'] for r in st.session_state['all_rows'])
                    st.metric("💰 Αξία", f"€{total:,.2f}")
            
            if st.session_state['all_rows']:
                df = pd.DataFrame(st.session_state['all_rows'])
                st.dataframe(df, use_container_width=True)
                
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    df.to_excel(writer, index=False)
                
                st.download_button("📥 Κατέβασμα Excel", output.getvalue(), 
                                 f"invoices_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                                 use_container_width=True)
    
    elif st.session_state['all_rows']:
        st.subheader("📊 Προηγούμενα Αποτελέσματα")
        df = pd.DataFrame(st.session_state['all_rows'])
        st.dataframe(df, use_container_width=True)
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        
        st.download_button("📥 Κατέβασμα Excel", output.getvalue(),
                         f"invoices_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                         use_container_width=True)

# ============================================================
# TAB 2: ANALYTICS DASHBOARD
# ============================================================

with tab2:
    st.header("📊 Analytics Dashboard")
    st.caption("Ανεβάστε CSV/Excel με δεδομένα τιμολογίων για ανάλυση")
    
    st.divider()
    
    # File upload for analytics
    analytics_file = st.file_uploader(
        "📤 Ανεβάστε CSV ή Excel (από το Invoice Parser ή άλλη πηγή)",
        type=['csv', 'xlsx', 'xls'],
        key="analytics_upload"
    )
    
    # Or use existing data
    use_existing = False
    if st.session_state['all_rows']:
        use_existing = st.checkbox(f"🔄 Χρήση δεδομένων από Invoice Parser ({len(st.session_state['all_rows'])} γραμμές)")
    
    df = None
    
    if analytics_file:
        try:
            if analytics_file.name.endswith('.csv'):
                df = pd.read_csv(analytics_file)
            else:
                df = pd.read_excel(analytics_file)
            st.success(f"✅ Φορτώθηκαν {len(df)} γραμμές από {analytics_file.name}")
        except Exception as e:
            st.error(f"❌ Σφάλμα ανάγνωσης: {e}")
    elif use_existing and st.session_state['all_rows']:
        df = pd.DataFrame(st.session_state['all_rows'])
        st.success(f"✅ Χρήση {len(df)} γραμμών από Invoice Parser")
    
    if df is not None and len(df) > 0:
        st.divider()
        
        # ============================================================
        # DATA PREVIEW
        # ============================================================
        
        with st.expander("🔍 Προεπισκόπηση Δεδομένων", expanded=False):
            st.dataframe(df.head(20), use_container_width=True)
            st.caption(f"Στήλες: {', '.join(df.columns.tolist())}")
        
        # ============================================================
        # AUTO-DETECT COLUMNS
        # ============================================================
        
        # Try to auto-detect relevant columns
        date_col = None
        amount_col = None
        vat_col = None
        total_col = None
        company_col = None
        product_col = None
        code_col = None
        
        for col in df.columns:
            col_lower = col.lower()
            if 'ημερομηνία' in col_lower or 'date' in col_lower:
                date_col = col
            elif 'καθαρ' in col_lower or 'net' in col_lower:
                amount_col = col
            elif 'φπα' in col_lower or 'vat' in col_lower:
                vat_col = col
            elif 'σύνολο' in col_lower or 'total' in col_lower:
                total_col = col
            elif 'επιχείρηση' in col_lower or 'company' in col_lower:
                company_col = col
            elif 'περιγραφή' in col_lower or 'description' in col_lower or 'product' in col_lower:
                product_col = col
            elif 'κωδικός' in col_lower or 'code' in col_lower:
                code_col = col
        
        # Use total if no amount
        if not amount_col and total_col:
            amount_col = total_col
        
        # ============================================================
        # KPI METRICS
        # ============================================================
        
        st.subheader("📈 Βασικά Στοιχεία")
        
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric("📄 Γραμμές", f"{len(df):,}")
        
        with col2:
            if amount_col and amount_col in df.columns:
                total_net = df[amount_col].sum()
                st.metric("💵 Καθαρή Αξία", f"€{total_net:,.2f}")
        
        with col3:
            if vat_col and vat_col in df.columns:
                total_vat = df[vat_col].sum()
                st.metric("🏛️ ΦΠΑ", f"€{total_vat:,.2f}")
        
        with col4:
            if total_col and total_col in df.columns:
                grand_total = df[total_col].sum()
                st.metric("💰 Σύνολο", f"€{grand_total:,.2f}")
        
        st.divider()
        
        # ============================================================
        # CHARTS (using Streamlit built-in + Altair)
        # ============================================================
        
        import altair as alt
        
        chart_col1, chart_col2 = st.columns(2)
        
        # Chart 1: Revenue by Company (Donut with Altair)
        with chart_col1:
            if company_col and amount_col and company_col in df.columns and amount_col in df.columns:
                st.subheader("🏢 Έσοδα ανά Επιχείρηση")
                
                company_revenue = df.groupby(company_col)[amount_col].sum().reset_index()
                company_revenue.columns = ['Επιχείρηση', 'Έσοδα']
                
                chart = alt.Chart(company_revenue).mark_arc(innerRadius=50).encode(
                    theta=alt.Theta(field='Έσοδα', type='quantitative'),
                    color=alt.Color(field='Επιχείρηση', type='nominal', 
                                   scale=alt.Scale(scheme='category10')),
                    tooltip=['Επιχείρηση', alt.Tooltip('Έσοδα:Q', format=',.2f')]
                ).properties(height=300)
                st.altair_chart(chart, use_container_width=True)
        
        # Chart 2: VAT vs Net (Donut)
        with chart_col2:
            if amount_col and vat_col and amount_col in df.columns and vat_col in df.columns:
                st.subheader("💹 Καθαρή Αξία vs ΦΠΑ")
                
                total_net = df[amount_col].sum()
                total_vat = df[vat_col].sum()
                
                vat_data = pd.DataFrame({
                    'Κατηγορία': ['Καθαρή Αξία', 'ΦΠΑ'],
                    'Ποσό': [total_net, total_vat]
                })
                
                chart = alt.Chart(vat_data).mark_arc(innerRadius=50).encode(
                    theta=alt.Theta(field='Ποσό', type='quantitative'),
                    color=alt.Color(field='Κατηγορία', type='nominal',
                                   scale=alt.Scale(domain=['Καθαρή Αξία', 'ΦΠΑ'],
                                                  range=['#2ecc71', '#e74c3c'])),
                    tooltip=['Κατηγορία', alt.Tooltip('Ποσό:Q', format=',.2f')]
                ).properties(height=300)
                st.altair_chart(chart, use_container_width=True)
        
        # Chart 3: Revenue over Time (Line - Streamlit built-in)
        if date_col and amount_col and date_col in df.columns and amount_col in df.columns:
            st.subheader("📅 Έσοδα ανά Ημερομηνία")
            
            try:
                df_temp = df.copy()
                df_temp[date_col] = pd.to_datetime(df_temp[date_col], errors='coerce')
                df_temp = df_temp.dropna(subset=[date_col])
                
                if len(df_temp) > 0:
                    daily_revenue = df_temp.groupby(df_temp[date_col].dt.date)[amount_col].sum()
                    st.line_chart(daily_revenue)
            except Exception as e:
                st.warning(f"Δεν ήταν δυνατή η ανάλυση ημερομηνιών: {e}")
        
        # Chart 4: Top Products (Bar - Streamlit built-in)
        chart_col3, chart_col4 = st.columns(2)
        
        with chart_col3:
            if product_col and amount_col and product_col in df.columns and amount_col in df.columns:
                st.subheader("🏆 Top 10 Προϊόντα")
                
                product_revenue = df.groupby(product_col)[amount_col].sum().reset_index()
                product_revenue.columns = ['Προϊόν', 'Έσοδα']
                product_revenue = product_revenue.nlargest(10, 'Έσοδα').set_index('Προϊόν')
                
                st.bar_chart(product_revenue)
        
        with chart_col4:
            if code_col and amount_col and code_col in df.columns and amount_col in df.columns:
                st.subheader("📦 Top 10 Κωδικοί")
                
                code_revenue = df.groupby(code_col)[amount_col].sum().reset_index()
                code_revenue.columns = ['Κωδικός', 'Έσοδα']
                code_revenue = code_revenue.nlargest(10, 'Έσοδα').set_index('Κωδικός')
                
                st.bar_chart(code_revenue)
        
        # ============================================================
        # DETAILED TABLES
        # ============================================================
        
        st.divider()
        st.subheader("📋 Αναλυτικοί Πίνακες")
        
        table_tabs = st.tabs(["Ανά Ημερομηνία", "Ανά Επιχείρηση", "Ανά Προϊόν", "Ανά Κωδικό"])
        
        with table_tabs[0]:
            if date_col and amount_col and date_col in df.columns:
                try:
                    df_temp = df.copy()
                    df_temp[date_col] = pd.to_datetime(df_temp[date_col], errors='coerce')
                    
                    agg_cols = {amount_col: 'sum'}
                    if vat_col and vat_col in df.columns:
                        agg_cols[vat_col] = 'sum'
                    if total_col and total_col in df.columns:
                        agg_cols[total_col] = 'sum'
                    
                    daily = df_temp.groupby(df_temp[date_col].dt.date).agg(agg_cols).reset_index()
                    daily = daily.sort_values(date_col, ascending=False)
                    st.dataframe(daily, use_container_width=True)
                except:
                    st.info("Δεν είναι δυνατή η ομαδοποίηση ανά ημερομηνία")
            else:
                st.info("Δεν βρέθηκε στήλη ημερομηνίας")
        
        with table_tabs[1]:
            if company_col and amount_col and company_col in df.columns:
                agg_cols = {amount_col: 'sum'}
                if vat_col and vat_col in df.columns:
                    agg_cols[vat_col] = 'sum'
                if total_col and total_col in df.columns:
                    agg_cols[total_col] = 'sum'
                
                by_company = df.groupby(company_col).agg(agg_cols).reset_index()
                by_company = by_company.sort_values(amount_col, ascending=False)
                st.dataframe(by_company, use_container_width=True)
            else:
                st.info("Δεν βρέθηκε στήλη επιχείρησης")
        
        with table_tabs[2]:
            if product_col and amount_col and product_col in df.columns:
                agg_cols = {amount_col: 'sum'}
                if 'Ποσότητα' in df.columns:
                    agg_cols['Ποσότητα'] = 'sum'
                
                by_product = df.groupby(product_col).agg(agg_cols).reset_index()
                by_product = by_product.sort_values(amount_col, ascending=False)
                st.dataframe(by_product.head(50), use_container_width=True)
            else:
                st.info("Δεν βρέθηκε στήλη περιγραφής")
        
        with table_tabs[3]:
            if code_col and amount_col and code_col in df.columns:
                agg_cols = {amount_col: 'sum'}
                if 'Ποσότητα' in df.columns:
                    agg_cols['Ποσότητα'] = 'sum'
                
                by_code = df.groupby(code_col).agg(agg_cols).reset_index()
                by_code = by_code.sort_values(amount_col, ascending=False)
                st.dataframe(by_code.head(50), use_container_width=True)
            else:
                st.info("Δεν βρέθηκε στήλη κωδικού")
    
    else:
        st.info("👆 Ανεβάστε ένα αρχείο CSV/Excel ή χρησιμοποιήστε τα δεδομένα από το Invoice Parser")
        
        st.markdown("""
        ### 📌 Αναμενόμενες Στήλες
        
        Το analytics dashboard αναγνωρίζει αυτόματα τις εξής στήλες:
        
        | Στήλη | Περιγραφή |
        |-------|-----------|
        | `Ημερομηνία` | Ημερομηνία τιμολογίου |
        | `Καθαρή Αξία` | Καθαρό ποσό χωρίς ΦΠΑ |
        | `ΦΠΑ` | Ποσό ΦΠΑ |
        | `Σύνολο` | Τελικό ποσό με ΦΠΑ |
        | `Επιχείρηση` | Όνομα επιχείρησης |
        | `Περιγραφή` | Περιγραφή προϊόντος |
        | `Κωδικός` | Κωδικός προϊόντος |
        
        💡 Μπορείτε να χρησιμοποιήσετε απευθείας το Excel που εξάγει το Invoice Parser!
        """)

# Footer
st.markdown("---")
st.caption("🚀 myDATA Invoice Tools v2.0 | Invoice Parser + Analytics Dashboard")
