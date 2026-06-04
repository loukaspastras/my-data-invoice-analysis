"""
Debug helper: fetch a single invoice by MARK from the myDATA API and print it.

Credentials are read from mydata_credentials.json (gitignored) — never hardcoded.

Usage:
    python check_invoice.py <MARK> [account_name]

If account_name is omitted, the first account in the creds file is used.
"""

import sys
import json
import requests
import xmltodict

CREDS_FILE = "mydata_credentials.json"


def load_creds(account_name=None):
    with open(CREDS_FILE, "r", encoding="utf-8") as f:
        creds = json.load(f)
    if not creds:
        raise SystemExit(f"❌ No accounts found in {CREDS_FILE}")
    name = account_name or next(iter(creds))
    if name not in creds:
        raise SystemExit(f"❌ Account '{name}' not found. Available: {', '.join(creds)}")
    return creds[name]["uid"], creds[name]["sk"]


def main():
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python check_invoice.py <MARK> [account_name]")

    mark = sys.argv[1]
    account_name = sys.argv[2] if len(sys.argv) > 2 else None
    uid, sk = load_creds(account_name)

    headers = {
        'aade-user-id': uid,
        'ocp-apim-subscription-key': sk
    }

    # The API returns documents with mark > parameter.
    # To get exactly <mark>, we search for mark-1.
    url = 'https://mydatapi.aade.gr/myDATA/RequestTransmittedDocs'
    params = {'mark': str(int(mark) - 1)}

    print(f"📡 Fetching MARK {mark} from myDATA API...")

    try:
        res = requests.get(url, headers=headers, params=params, timeout=30)

        if res.status_code == 200:
            data = xmltodict.parse(res.text)
            # Deep extract the invoice
            requested_doc = data.get('RequestedDoc', {})
            invoices_doc = requested_doc.get('invoicesDoc', {})
            invoices = invoices_doc.get('invoice', [])

            if not isinstance(invoices, list):
                invoices = [invoices]

            target_inv = None
            for inv in invoices:
                if str(inv.get('mark')) == mark:
                    target_inv = inv
                    break

            if target_inv:
                print("✅ Invoice Found!")
                # Print as pretty JSON for readability
                print(json.dumps(target_inv, indent=4, ensure_ascii=False))
            else:
                print(f"❌ MARK {mark} not found in the response.")
                print("Raw response preview:", res.text[:500])
        else:
            print(f"❌ Error: HTTP {res.status_code}")
            print(res.text)

    except Exception as e:
        print(f"❌ Exception: {str(e)}")


if __name__ == "__main__":
    main()
