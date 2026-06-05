"""myDATA Invoice Tools — Streamlit entry point.

Thin composition root: page config, session state, and view wiring.
All logic lives in the `mydata` package (credentials, pdf_parser, api,
processing) and the `mydata.ui` views.

Launched by ΕΚΚΙΝΗΣΗ.bat via: streamlit run app.py
"""

import streamlit as st

from mydata.costing import load_cost_db
from mydata.ui.sidebar import render_sidebar
from mydata.ui.parser_tab import render_parser_tab
from mydata.ui.analytics_tab import render_analytics_tab

# ============================================================
# STREAMLIT CONFIG  (must be the first Streamlit command)
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
if 'cost_table' not in st.session_state:
    # Load the persisted cost-of-goods table (database/) into memory; None if absent.
    st.session_state['cost_table'] = load_cost_db()

# ============================================================
# SIDEBAR (common for all tabs)
# ============================================================

render_sidebar()

# ============================================================
# MAIN TABS
# ============================================================

st.title("📄 myDATA Invoice Tools")

tab1, tab2 = st.tabs(["📥 Invoice Parser", "📊 Analytics Dashboard"])

with tab1:
    render_parser_tab()

with tab2:
    render_analytics_tab()

# Footer
st.markdown("---")
st.caption("🚀 myDATA Invoice Tools v2.0 | Invoice Parser + Analytics Dashboard")
