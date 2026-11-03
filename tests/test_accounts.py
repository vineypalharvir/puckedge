import json

import src.usage_log as usage_log


def test_accounts_keep_a_hash_and_an_ip_history(tmp_path, monkeypatch):
    monkeypatch.setattr(usage_log, "ACCOUNTS_PATH", tmp_path / "accounts.json")
    monkeypatch.setattr(usage_log, "LOG_PATH", tmp_path / "activity.jsonl")
    assert usage_log.create_account("Alex Stone", "alex", "secret1") is None
    assert usage_log.create_account("Alex Stone", "alex", "secret1") == "That username is already taken."
    assert usage_log.authenticate("alex", "nope") is None
    account = usage_log.authenticate("Alex", "secret1")
    assert account is not None
    assert account["name"] == "Alex Stone"
    stored = json.loads((tmp_path / "accounts.json").read_text(encoding="utf-8"))
    assert stored["users"][0]["password_hash"] != "secret1"
    ips = usage_log.remember_ips("alex", ["127.0.0.1", "203.0.113.8", "203.0.113.8"])
    assert ips == ["127.0.0.1", "203.0.113.8"]
    usage_log.record_event("sign_in", "NHL", "Signed in", username="alex", name="Alex Stone", ips="203.0.113.8")
    events = usage_log.read_events()
    assert events[0]["name"] == "Alex Stone"
    assert "secret1" not in json.dumps(events)
    assert "ODDS_API_KEY" not in json.dumps(stored)
