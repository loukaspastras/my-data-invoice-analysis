"""myDATA REST API client — bulk fetch of all transmitted invoices."""

import time
import requests
import xmltodict


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
