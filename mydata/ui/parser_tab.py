"""Tab 1: Invoice Parser — bulk API fetch + PDF enrichment + Excel export."""

import io
from datetime import datetime

import streamlit as st
import pandas as pd

from ..api import fetch_all_invoices_bulk
from ..processing import process_pdf_with_cached_data


def render_parser_tab():
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
