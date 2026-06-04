"""Tests for credential persistence (load/save/delete)."""

import json

import pytest

from mydata import credentials


@pytest.fixture
def temp_creds(tmp_path, monkeypatch):
    """Point CREDS_FILE at a throwaway file in a temp dir."""
    creds_file = tmp_path / "creds.json"
    monkeypatch.setattr(credentials, "CREDS_FILE", str(creds_file))
    return creds_file


def test_load_missing_returns_empty(temp_creds):
    assert credentials.load_saved_creds() == {}


def test_load_corrupt_returns_empty(temp_creds):
    temp_creds.write_text("{ this is not valid json", encoding="utf-8")
    assert credentials.load_saved_creds() == {}


def test_save_then_load_roundtrip(temp_creds):
    credentials.save_creds("acme", "UID1", "SK1")
    loaded = credentials.load_saved_creds()
    assert loaded == {"acme": {"uid": "UID1", "sk": "SK1"}}


def test_save_multiple_accounts(temp_creds):
    credentials.save_creds("a", "u1", "s1")
    credentials.save_creds("b", "u2", "s2")
    loaded = credentials.load_saved_creds()
    assert set(loaded.keys()) == {"a", "b"}
    assert loaded["b"] == {"uid": "u2", "sk": "s2"}


def test_save_overwrites_same_name(temp_creds):
    credentials.save_creds("a", "u1", "s1")
    credentials.save_creds("a", "u2", "s2")
    loaded = credentials.load_saved_creds()
    assert loaded["a"] == {"uid": "u2", "sk": "s2"}


def test_delete_removes_account(temp_creds):
    credentials.save_creds("a", "u1", "s1")
    credentials.save_creds("b", "u2", "s2")
    credentials.delete_creds("a")
    loaded = credentials.load_saved_creds()
    assert "a" not in loaded
    assert "b" in loaded


def test_delete_missing_is_noop(temp_creds):
    credentials.save_creds("a", "u1", "s1")
    credentials.delete_creds("does-not-exist")  # must not raise
    assert "a" in credentials.load_saved_creds()


def test_saved_file_is_utf8_and_pretty(temp_creds):
    credentials.save_creds("ελληνικά", "u1", "s1")
    raw = temp_creds.read_text(encoding="utf-8")
    # ensure_ascii=False keeps Greek readable in the file
    assert "ελληνικά" in raw
    # indent=4 -> pretty printed
    assert json.loads(raw)["ελληνικά"]["uid"] == "u1"
