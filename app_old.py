"""
Invoice Parser - Streamlit App
Combines PDF spatial parsing with AADE myDATA API for complete invoice extraction.
"""

import streamlit as st
import pandas as pd
import pdfplumber
import requests
import xmltodict
import re
import io
from datetime import datetime


def extract_mark_from_pdf(pdf_file):
    """Extract the 15-digit MARK from PDF"""
    try:
        with pdfplumber.open(pdf_file) as pdf:
            text = pdf.pages[0].extract_text()
        mark_match = re.search(r'\d{15}', text)
        if mark_match:
            return mark_match.group(0), None
        return None, "Δεν βρέθηκε MARK (15-ψήφιος αριθμός) στο PDF"
    except Exception as e:
        return None, f"Σφάλμα ανάγνωσης PDF: {str(e)}"


def group_words_by_row(words, y_tolerance=5):
    """Group words into rows with fuzzy Y matching"""
    if not words:
        return {}
    sorted_words = sorted(words, key=lambda w: w['top'])
    rows = {}
    for w in sorted_words:
        matched_row = None
        for row_y in rows.keys():
            if abs(w['top'] - row_y) <= y_tolerance:
                matched_row = row_y
                break
        if matched_row is not None:
            rows[matched_row].append(w)
        else:
            rows[w['top']] = [w]
    for y in rows:
        rows[y].sort(key=lambda w: w['x0'])
    return rows


def find_column_boundaries(words):
    """Find column X boundaries from table header"""
    columns = {}
    keywords = {
        'A/A': 'aa', 'Κωδ.': 'code', 'Κωδ': 'code',
        'Περιγραφή': 'desc', 'Ποσότητα': 'qty',
        'Μ.Μ.': 'unit', 'Τιμή': 'price', 'Αξία': 'value'
    }
    for w in words:
        for kw, name in keywords.items():
            if w['text'] == kw or w['text'].startswith(kw):
                columns[name] = {'x0': w['x0'], 'x1': w['x1']}
    return columns


def normalize_number(num):
    """Convert number to searchable formats"""
    if isinstance(num, (int, float)):
        num = str(num)
    num_str = str(num).replace(',', '.')
    try:
        val = float(num_str)
        if val == int(val):
            return [str(int(val))]
        else:
            return [f"{val:.2f}".replace('.', ','), f"{val:.2f}", str(int(val))]
    except:
        return [num_str]


def find_value_in_row(row_words, target_values):
    """Check if any target value exists in the row"""
    row_texts = [w['text'].replace(',', '.') for w in row_words]
    row_text_joined = ' '.join([w['text'] for w in row_words])
    for target in target_values:
        if target is None:
            continue
        target_normalized = str(target).replace(',', '.')
        if target_normalized in row_texts or target in row_texts:
            return True
        if target in row_text_joined or target_normalized in row_text_joined:
            return True
    return False


def find_line_rows_by_api_anchors(rows, api_details):
    """Find Y position of each line using multi-anchor strategy"""
    line_y_positions = {}
    for i, api_line in enumerate(api_details):
        line_no = i + 1
        qty = str(int(float(api_line.get('quantity', 0))))
        net_value = api_line.get('netValue', '0')
        qty_variants = normalize_number(qty)
        net_variants = normalize_number(net_value)
        best_match_y = None
        best_match_score = 0
        for row_y, row_words in rows.items():
            score = 0
            for w in row_words:
                if w['text'] == str(line_no) and w['x0'] < 60:
                    score += 2
                    break
            if find_value_in_row(row_words, qty_variants):
                score += 1
            if find_value_in_row(row_words, net_variants):
                score += 1
            row_text = ' '.join([w['text'] for w in row_words])
            if 'τμχ' in row_text.lower() or 'τεμ' in row_text.lower():
                score += 1
            if score > best_match_score:
                best_match_score = score
                best_match_y = row_y
        if best_match_y is not None and best_match_score >= 3:
            line_y_positions[line_no] = best_match_y
    return line_y_positions


def calculate_boundaries(line_y_positions):
    """Calculate Y boundaries for each line using midpoints"""
    sorted_lines = sorted(line_y_positions.keys())
    boundaries = {}
    for i, line_no in enumerate(sorted_lines):
        y = line_y_positions[line_no]
        if i == 0:
            y_up = y - 20
        else:
            prev_y = line_y_positions[sorted_lines[i - 1]]
            y_up = (prev_y + y) / 2
        if i == len(sorted_lines) - 1:
            y_down = y + 30
        else:
            next_y = line_y_positions[sorted_lines[i + 1]]
            y_down = (y + next_y) / 2
        boundaries[line_no] = {'y': y, 'y_up': y_up, 'y_down': y_down}
    return boundaries


def extract_code_and_description(rows, y_up, y_down, columns):
    """Extract code and description - POSITION BASED, no format assumptions"""
    aa_x_end = columns.get('aa', {}).get('x1', 46)
    desc_x_start = columns.get('desc', {}).get('x0', 107) - 10
    desc_x_end = columns.get('qty', {}).get('x0', 184) - 10
    code = None
    desc_words = []
    for row_y in sorted(rows.keys()):
        if y_up <= row_y <= y_down:
            for w in rows[row_y]:
                text = w['text']
                x = w['x0']
                if code is None and aa_x_end - 5 < x < desc_x_start:
                    if text.isdigit():
                        continue
                    if len(text) < 2:
                        continue
                    if text.lower() in ['τμχ', 'τεμ', 'kg', 'lt']:
                        continue
                    code = text
                if desc_x_start - 5 < x < desc_x_end + 5:
                    if re.match(r'^[\d,\.]+$', text):
                        continue
                    if text.lower() in ['τμχ', 'τεμ', 'kg', 'lt', '€']:
                        continue
                    desc_words.append((row_y, x, text))
    desc_words.sort(key=lambda w: (w[0], w[1]))
    description = ' '.join([w[2] for w in desc_words])
    return code, description


def parse_pdf_spatial(pdf_file, api_details):
    """Parse PDF using Multi-Anchor Strategy"""
    try:
        with pdfplumber.open(pdf_file) as pdf:
            words = pdf.pages[0].extract_words()
        if not words:
            return {}, "Δεν βρέθηκαν λέξεις στο PDF"
        columns = find_column_boundaries(words)
        rows = group_words_by_row(words, y_tolerance=5)
        line_y_positions = find_line_rows_by_api_anchors(rows, api_details)
        if not line_y_positions:
            return {}, "Δεν βρέθηκαν γραμμές προϊόντων στο PDF"
        boundaries = calculate_boundaries(line_y_positions)
        results = {}
        for line_no, b in boundaries.items():
            code, description = extract_code_and_description(
                rows, b['y_up'], b['y_down'], columns
            )
            results[line_no] = {'code': code, 'description': description}
        for line_no in range(1, len(api_details) + 1):
            if line_no not in results:
                results[line_no] = {'code': None, 'description': '[Δεν βρέθηκε στο PDF]'}
        return results, None
    except Exception as e:
        return {}, f"Σφάλμα parsing PDF: {str(e)}"


def fetch_invoice_from_api(mark, user_id, subscription_key):
    """Fetch invoice from AADE API by MARK"""
    try:
        headers = {
            'aade-user-id': user_id,
            'ocp-apim-subscription-key': subscription_key
        }
        url = 'https://mydatapi.aade.gr/myDATA/RequestTransmittedDocs'
        params = {'mark': str(int(mark) - 1)}
        response = requests.get(url, headers=headers, params=params, timeout=30)
        if response.status_code == 401:
            return None, "Σφάλμα 401: Λάθος credentials"
        elif response.status_code != 200:
            return None, f"Σφάλμα API: {response.status_code}"
        data = xmltodict.parse(response.text)
        if 'invoicesDoc' not in data.get('RequestedDoc', {}):
            return None, f"Το τιμολόγιο με MARK {mark} δεν βρέθηκε στο API"
        invoices = data['RequestedDoc']['invoicesDoc']['invoice']
        if not isinstance(invoices, list):
            invoices = [invoices]
        for inv in invoices:
            if inv.get('mark') == mark:
                return inv, None
        return None, f"Το τιμολόγιο με MARK {mark} δεν βρέθηκε"
    except requests.exceptions.Timeout:
        return None, "Timeout - το API δεν απάντησε"
    except Exception as e:
        return None, f"Σφάλμα: {str(e)}"


def parse_api_invoice(api_invoice):
    """Parse API invoice into structured data"""
    result = {
        'mark': api_invoice.get('mark', ''),
        'issuer_vat': '',
        'customer_vat': '',
        'customer_name': '',
        'issue_date': '',
        'series': '',
        'aa': '',
        'invoice_type': '',
        'lines': []
    }
    issuer = api_invoice.get('issuer', {})
    result['issuer_vat'] = issuer.get('vatNumber', '')
    counterpart = api_invoice.get('counterpart', {})
    if isinstance(counterpart, dict):
        result['customer_vat'] = counterpart.get('vatNumber', '')
        result['customer_name'] = counterpart.get('name', '')
    header = api_invoice.get('invoiceHeader', {})
    result['series'] = header.get('series', '')
    result['aa'] = header.get('aa', '')
    result['issue_date'] = header.get('issueDate', '')
    result['invoice_type'] = header.get('invoiceType', '')
    details = api_invoice.get('invoiceDetails', [])
    if not isinstance(details, list):
        details = [details]
    for i, line in enumerate(details, 1):
        if isinstance(line, dict):
            result['lines'].append({
                'line_no': int(line.get('lineNumber', i)),
                'quantity': line.get('quantity', 0),
                'net_value': line.get('netValue', 0),
                'vat_amount': line.get('vatAmount', 0),
                'vat_category': line.get('vatCategory', '')
            })
    return result


def process_invoice(pdf_file, user_id, subscription_key):
    """Process a single invoice PDF"""
    result = {
        'filename': pdf_file.name,
        'mark': None,
        'status': 'pending',
        'error': None,
        'data': None
    }
    mark, error = extract_mark_from_pdf(pdf_file)
    if error:
        result['status'] = 'error'
        result['error'] = error
        return result
    result['mark'] = mark
    pdf_file.seek(0)
    api_invoice, error = fetch_invoice_from_api(mark, user_id, subscription_key)
    if error:
        result['status'] = 'error'
        result['error'] = error
        return result
    api_data = parse_api_invoice(api_invoice)
    api_details = api_invoice.get('invoiceDetails', [])
    if not isinstance(api_details, list):
        api_details = [api_details]
    pdf_file.seek(0)
    pdf_enrichment, pdf_error = parse_pdf_spatial(pdf_file, api_details)
    enriched_lines = []
    for line in api_data['lines']:
        line_no = line['line_no']
        pdf_data = pdf_enrichment.get(line_no, {})
        enriched_lines.append({
            'line_no': line_no,
            'code': pdf_data.get('code', ''),
            'description': pdf_data.get('description', ''),
            'quantity': line['quantity'],
            'net_value': float(line['net_value']),
            'vat_amount': float(line['vat_amount']),
            'total_value': float(line['net_value']) + float(line['vat_amount']),
            'vat_category': line['vat_category']
        })
    result['status'] = 'success'
    result['data'] = {
        'mark': api_data['mark'],
        'series': api_data['series'],
        'aa': api_data['aa'],
        'issue_date': api_data['issue_date'],
        'invoice_type': api_data['invoice_type'],
        'issuer_vat': api_data['issuer_vat'],
        'customer_vat': api_data['customer_vat'],
        'customer_name': api_data['customer_name'],
        'lines': enriched_lines
    }
    if pdf_error:
        result['warning'] = pdf_error
    return result


def results_to_dataframe(results):
    """Convert processed results to DataFrame"""
    rows = []
    for r in results:
        if r['status'] != 'success' or not r['data']:
            continue
        data = r['data']
        for line in data['lines']:
            rows.append({
                'Αρχείο': r['filename'],
                'MARK': data['mark'],
                'Σειρά': data['series'],
                'Α/Α': data['aa'],
                'Ημερομηνία': data['issue_date'],
                'Τύπος': data['invoice_type'],
                'ΑΦΜ Εκδότη': data['issuer_vat'],
                'ΑΦΜ Πελάτη': data['customer_vat'],
                'Επωνυμία Πελάτη': data['customer_name'],
                'Γραμμή': line['line_no'],
                'Κωδικός': line['code'] or '',
                'Περιγραφή': line['description'] or '',
                'Ποσότητα': line['quantity'],
                'Καθαρή Αξία': line['net_value'],
                'ΦΠΑ': line['vat_amount'],
                'Τελική Αξία': line['total_value'],
                'Κατηγορία ΦΠΑ': line['vat_category']
            })
    return pd.DataFrame(rows)


# ============================================================
# STREAMLIT UI
# ============================================================

st.set_page_config(page_title="Invoice Parser - myDATA", page_icon="📄", layout="wide")

st.title("📄 Invoice Parser")
st.markdown("*Συνδυάζει PDF parsing + myDATA API για πλήρη εξαγωγή τιμολογίων*")

with st.sidebar:
    st.header("🔑 Credentials")
    user_id = st.text_input("AADE User ID", value=st.session_state.get('user_id', ''))
    subscription_key = st.text_input("Subscription Key", value=st.session_state.get('subscription_key', ''), type="password")
    if user_id:
        st.session_state['user_id'] = user_id
    if subscription_key:
        st.session_state['subscription_key'] = subscription_key
    st.divider()
    st.markdown("### Οδηγίες\n1. Εισάγετε τα credentials\n2. Ανεβάστε τα PDF\n3. Πατήστε Επεξεργασία\n4. Κατεβάστε το Excel")

if not user_id or not subscription_key:
    st.warning("⚠️ Εισάγετε τα credentials στο sidebar για να συνεχίσετε")
    st.stop()

st.header("📤 Ανέβασμα Τιμολογίων")
uploaded_files = st.file_uploader("Σύρετε τα PDF τιμολόγια εδώ", type=['pdf'], accept_multiple_files=True)

if uploaded_files:
    st.info(f"📁 {len(uploaded_files)} αρχεία επιλέχθηκαν")
    
    if st.button("🚀 Επεξεργασία Τιμολογίων", type="primary", use_container_width=True):
        results = []
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        for i, pdf_file in enumerate(uploaded_files):
            status_text.text(f"Επεξεργασία: {pdf_file.name} ({i+1}/{len(uploaded_files)})")
            result = process_invoice(pdf_file, user_id, subscription_key)
            results.append(result)
            progress_bar.progress((i + 1) / len(uploaded_files))
        
        status_text.text("Ολοκληρώθηκε!")
        st.session_state['results'] = results
        
        st.divider()
        st.header("📊 Αποτελέσματα")
        
        success_count = sum(1 for r in results if r['status'] == 'success')
        error_count = sum(1 for r in results if r['status'] == 'error')
        
        col1, col2, col3 = st.columns(3)
        col1.metric("Συνολικά", len(results))
        col2.metric("Επιτυχή", success_count)
        col3.metric("Σφάλματα", error_count)
        
        for r in results:
            if r['status'] == 'success':
                with st.expander(f"✅ {r['filename']} - MARK: {r['mark']}", expanded=False):
                    data = r['data']
                    st.write(f"**Σειρά/Α.Α.:** {data['series']}/{data['aa']}")
                    st.write(f"**Ημερομηνία:** {data['issue_date']}")
                    st.write(f"**Πελάτης:** {data['customer_name']} ({data['customer_vat']})")
                    if r.get('warning'):
                        st.warning(r['warning'])
                    lines_df = pd.DataFrame(data['lines'])
                    st.dataframe(lines_df, use_container_width=True)
            else:
                with st.expander(f"❌ {r['filename']}", expanded=True):
                    st.error(r['error'])

if 'results' in st.session_state and st.session_state['results']:
    results = st.session_state['results']
    df = results_to_dataframe(results)
    
    if not df.empty:
        st.divider()
        st.header("📥 Εξαγωγή")
        st.dataframe(df, use_container_width=True, height=400)
        
        col1, col2, col3 = st.columns(3)
        col1.metric("Συνολική Καθαρή Αξία", f"€{df['Καθαρή Αξία'].sum():,.2f}")
        col2.metric("Συνολικό ΦΠΑ", f"€{df['ΦΠΑ'].sum():,.2f}")
        col3.metric("Συνολική Τελική Αξία", f"€{df['Τελική Αξία'].sum():,.2f}")
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Τιμολόγια')
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        st.download_button(
            label="📥 Κατέβασμα Excel",
            data=output.getvalue(),
            file_name=f"invoices_{timestamp}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            use_container_width=True
        )
