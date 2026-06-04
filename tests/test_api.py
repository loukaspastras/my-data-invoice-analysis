"""Tests for the myDATA API client, with the network fully mocked."""

import requests

from mydata import api


class FakeResp:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


def _xml(invoices_xml, continuation=None):
    cont = ""
    if continuation:
        pk, rk = continuation
        cont = f"<continuationToken><nextPartitionKey>{pk}</nextPartitionKey><nextRowKey>{rk}</nextRowKey></continuationToken>"
    return f"<RequestedDoc><invoicesDoc>{invoices_xml}</invoicesDoc>{cont}</RequestedDoc>"


def _invoice(mark):
    return f"<invoice><mark>{mark}</mark><invoiceHeader><issueDate>2026-01-01</issueDate></invoiceHeader></invoice>"


def test_single_page_returns_dict_keyed_by_mark(monkeypatch):
    monkeypatch.setattr(api.time, "sleep", lambda *_: None)
    monkeypatch.setattr(api.requests, "get",
                        lambda *a, **k: FakeResp(200, _xml(_invoice("111") + _invoice("222"))))

    invoices, err, rate = api.fetch_all_invoices_bulk("uid", "sk")

    assert err is None and rate is False
    assert set(invoices.keys()) == {"111", "222"}
    assert invoices["111"]["mark"] == "111"


def test_single_invoice_not_a_list(monkeypatch):
    # xmltodict returns a dict (not list) when there is exactly one invoice
    monkeypatch.setattr(api.time, "sleep", lambda *_: None)
    monkeypatch.setattr(api.requests, "get",
                        lambda *a, **k: FakeResp(200, _xml(_invoice("999"))))

    invoices, err, rate = api.fetch_all_invoices_bulk("uid", "sk")
    assert list(invoices.keys()) == ["999"]


def test_pagination_follows_continuation_token(monkeypatch):
    monkeypatch.setattr(api.time, "sleep", lambda *_: None)
    pages = [
        FakeResp(200, _xml(_invoice("1"), continuation=("PK", "RK"))),  # page 1 -> more
        FakeResp(200, _xml(_invoice("2"))),                              # page 2 -> done
    ]
    calls = {"n": 0}

    def fake_get(*a, **k):
        resp = pages[calls["n"]]
        calls["n"] += 1
        return resp

    monkeypatch.setattr(api.requests, "get", fake_get)

    invoices, err, rate = api.fetch_all_invoices_bulk("uid", "sk")
    assert err is None
    assert set(invoices.keys()) == {"1", "2"}
    assert calls["n"] == 2  # exactly two pages fetched


def test_http_401_credentials_error(monkeypatch):
    monkeypatch.setattr(api.requests, "get", lambda *a, **k: FakeResp(401))
    invoices, err, rate = api.fetch_all_invoices_bulk("uid", "sk")
    assert invoices is None
    assert rate is False
    assert "credentials" in err.lower() or "🔐" in err


def test_http_429_rate_limited(monkeypatch):
    monkeypatch.setattr(api.requests, "get", lambda *a, **k: FakeResp(429))
    invoices, err, rate = api.fetch_all_invoices_bulk("uid", "sk")
    assert invoices is None
    assert rate is True


def test_http_other_status(monkeypatch):
    monkeypatch.setattr(api.requests, "get", lambda *a, **k: FakeResp(500))
    invoices, err, rate = api.fetch_all_invoices_bulk("uid", "sk")
    assert invoices is None
    assert "500" in err


def test_timeout_returns_retryable(monkeypatch):
    def boom(*a, **k):
        raise requests.exceptions.Timeout()
    monkeypatch.setattr(api.requests, "get", boom)
    invoices, err, rate = api.fetch_all_invoices_bulk("uid", "sk")
    assert invoices is None
    assert rate is True  # timeout is flagged as retryable


def test_empty_invoices_doc(monkeypatch):
    # An account with no matching docs: RequestedDoc present, invoicesDoc empty.
    monkeypatch.setattr(api.requests, "get",
                        lambda *a, **k: FakeResp(200, "<RequestedDoc><invoicesDoc></invoicesDoc></RequestedDoc>"))
    invoices, err, rate = api.fetch_all_invoices_bulk("uid", "sk")
    assert invoices == {} and err is None
