# myDATA Invoice Tools

**Recover the product codes & descriptions that the AADE myDATA export leaves out — straight into a clean Excel.**

Greece's [myDATA](https://www.aade.gr/mydata) platform stores the *financial* data of every invoice (quantities, values, VAT, dates, series, type) — but **not** the product **codes** and **descriptions**. Those only ever exist on the printed invoice PDF. So if you want a spreadsheet of *what you actually sold*, the official tools leave you copy‑pasting from PDFs by hand.

This little Streamlit app bridges that gap: it pulls the authoritative figures from the myDATA REST API and recovers the SKU code + description from your invoice PDFs, then gives you a formatted Excel and a small analytics dashboard.

> The UI is in Greek (the target users are Greek accountants/businesses), but the code and this README are in English.

---

## How it works

The core idea is **"API is the source of truth, PDF is enrichment"** — a `LEFT JOIN` of API data onto PDF data:

- The **myDATA API** provides every line's `quantity`, `netValue`, `vatAmount`, `issueDate`, `series`, `aa`, and `invoiceType`.
- The **PDF** provides the one thing the API doesn't have: the line's **code** and **description**.
- Each API line is matched to its PDF row (spatially, using the line number as the anchor) and the two are joined.

The nice property of this design: **if the PDF parsing misses a line, the financial data still comes through correctly** — you just get a blank code/description for that row, never a wrong number.

### Credit notes (Πιστωτικά)

Credit notes are identified **authoritatively from the API** (`invoiceType` starting with `5`, e.g. `5.1`/`5.2`) — not by reading the PDF. For every credit note, all numeric values are **negated**, so when you sum a batch the credits net out against the sales automatically.

---

## Features

- 🔑 Multiple myDATA accounts, saved locally
- 📡 Bulk‑fetch all of an account's invoices in one API pass (cached for the session)
- 📄 Spatial PDF parser that recovers the code and the (often multi‑line) description per line item
- 🧾 Σειρά / Α.Α. / Τύπος (series / number / document type) columns straight from the API
- ➖ Automatic negative signing for credit notes so totals net out
- 📥 One‑click **formatted** Excel export (styled table, € formatting with red negatives, real dates, totals row)
- 📊 Analytics dashboard — KPIs and charts by company, product, code, and date

---

## Requirements

- Python 3.9+ (developed/tested on 3.13)
- The dependencies in [`requirements.txt`](requirements.txt): `streamlit`, `pandas`, `pdfplumber`, `requests`, `xmltodict`, `openpyxl`, `altair`

## Install

```bash
git clone https://github.com/loukaspastras/my-data-invoice-analysis.git
cd my-data-invoice-analysis
pip install -r requirements.txt
```

## Run

```bash
streamlit run app.py
```

On Windows you can also just double‑click **`ΕΚΚΙΝΗΣΗ.bat`**, which checks Python, installs requirements, and launches the app. (See [`ΟΔΗΓΙΕΣ.md`](ΟΔΗΓΙΕΣ.md) for step‑by‑step Greek setup instructions.)

---

## myDATA credentials

You need a myDATA **REST API** registration (done from the AADE myDATA portal):

- **User ID** → sent as the `aade-user-id` header
- **Subscription Key** → sent as the `ocp-apim-subscription-key` header

Use the **production** environment. The app calls `RequestTransmittedDocs`, which returns the documents the authenticated entity **issued** — so the credentials must belong to the company that issued the invoices you're uploading.

Credentials you enter are saved locally to `mydata_credentials.json` (see [Security](#security--limitations)).

## Usage

1. Add an account in the sidebar (name + User ID + Subscription Key)
2. Upload one or more invoice PDFs
3. Click **Έναρξη Επεξεργασίας** (Start Processing)
4. Click **Κατέβασμα Excel** (Download Excel)

### Output columns

`Επιχείρηση` · `Αρχείο` · `MARK` · `Σειρά` · `Α/Α` · `Τύπος` · `Ημερομηνία` · `Γραμμή` · `Κωδικός` · `Περιγραφή` · `Ποσότητα` · `Καθαρή Αξία` · `ΦΠΑ` · `Σύνολο`

---

## Project structure

```
app.py                 # Streamlit entry point (thin)
mydata/
  credentials.py       # save/load/delete saved logins
  pdf_parser.py        # pure PDF geometry + extraction (no Streamlit/network)
  api.py               # myDATA REST client (bulk fetch)
  processing.py        # orchestration: API LEFT JOIN PDF, credit-note signing
  excel_export.py      # formatted .xlsx generation
  ui/                  # sidebar, parser tab, analytics tab
tests/                 # pytest suite
check_invoice.py       # standalone debug helper (fetch one MARK)
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

The suite runs against real invoice PDFs kept in a **gitignored** `test_invoices/` folder (not included in the repo — they contain real business data). Tests that depend on them skip cleanly when the folder is absent. A genuine end‑to‑end test against the live myDATA API is gated behind an env var:

```bash
MYDATA_LIVE=1 pytest
```

---

## Security & limitations

Be aware of these before relying on it — and PRs that improve them are welcome:

- **Credentials are stored in plaintext** in `mydata_credentials.json` (gitignored). Fine for one person on their own machine; **do not** use on a shared computer.
- **The PDF parser is tuned to a specific invoice layout.** Code/description extraction relies on the table's column geometry; a markedly different invoice template may produce blank codes/descriptions. (Thanks to the LEFT‑JOIN design, the financial figures are still correct in that case.)
- **Not affiliated with AADE.** Always verify the figures against the official source before using them for anything that matters.

---

## License

[**0BSD**](LICENSE) (BSD Zero Clause License) — public‑domain‑equivalent. Use it, change it, ship it, sell it, no attribution required. If it's useful to you, great.
