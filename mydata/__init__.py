"""myDATA Invoice Tools — core package.

Separation of concerns:
    credentials  — persistence of saved AADE logins
    pdf_parser   — pure PDF geometry + extraction (no Streamlit, no network)
    api          — myDATA REST API client (bulk fetch)
    processing   — orchestration: API data LEFT JOIN PDF enrichment
    ui/          — Streamlit views (sidebar, parser tab, analytics tab)
"""
