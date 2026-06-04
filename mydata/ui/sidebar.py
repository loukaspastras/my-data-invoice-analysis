"""Sidebar: saved/active account management (common to all tabs)."""

import streamlit as st

from ..credentials import load_saved_creds, save_creds, delete_creds


def render_sidebar():
    saved_creds = load_saved_creds()

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
