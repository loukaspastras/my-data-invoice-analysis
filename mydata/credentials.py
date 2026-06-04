"""Persistence of saved AADE myDATA logins (name -> {uid, sk})."""

import os
import json

# Αρχείο αποθήκευσης credentials
CREDS_FILE = "mydata_credentials.json"


def load_saved_creds():
    if os.path.exists(CREDS_FILE):
        try:
            with open(CREDS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except: return {}
    return {}


def save_creds(name, uid, sk):
    creds = load_saved_creds()
    creds[name] = {"uid": uid, "sk": sk}
    with open(CREDS_FILE, "w", encoding="utf-8") as f:
        json.dump(creds, f, ensure_ascii=False, indent=4)


def delete_creds(name):
    creds = load_saved_creds()
    if name in creds:
        del creds[name]
        with open(CREDS_FILE, "w", encoding="utf-8") as f:
            json.dump(creds, f, ensure_ascii=False, indent=4)
