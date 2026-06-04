"""Tab 2: Analytics Dashboard — KPIs, charts, and aggregated tables."""

import streamlit as st
import pandas as pd
import altair as alt


def render_analytics_tab():
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
