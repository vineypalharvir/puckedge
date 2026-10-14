import json

from src.updater import apply_tree, apply_update, describe_update, update_settings


def test_empty_repo_skips_the_download(tmp_path, monkeypatch):
    monkeypatch.delenv("PUCKEDGE_UPDATE_REPO", raising=False)
    (tmp_path / "update.json").write_text(json.dumps({"repo": "", "branch": "main"}), encoding="utf-8")
    assert update_settings(tmp_path) == ("", "main")
    assert apply_update(tmp_path) == (False, False)
    assert "off" in describe_update(tmp_path)


def test_apply_tree_replaces_code_and_keeps_secrets(tmp_path):
    dest = tmp_path / "app"
    source = tmp_path / "download"
    dest.mkdir()
    source.mkdir()
    (dest / ".env").write_text("ODDS_API_KEY=local-secret\n", encoding="utf-8")
    (dest / "data").mkdir()
    (dest / "data" / "puckedge.db").write_text("ledger", encoding="utf-8")
    (dest / ".venv").mkdir()
    (dest / "PuckEdge.exe").write_bytes(b"old-exe")
    (dest / "app.py").write_text("print('old')\n", encoding="utf-8")
    (source / ".env").write_text("ODDS_API_KEY=do-not-copy\n", encoding="utf-8")
    (source / "data").mkdir()
    (source / "data" / "puckedge.db").write_text("remote-ledger", encoding="utf-8")
    (source / "app.py").write_text("print('new')\n", encoding="utf-8")
    (source / "src").mkdir()
    (source / "src" / "new_module.py").write_text("x = 1\n", encoding="utf-8")

    apply_tree(source, dest)

    assert (dest / ".env").read_text(encoding="utf-8") == "ODDS_API_KEY=local-secret\n"
    assert (dest / "data" / "puckedge.db").read_text(encoding="utf-8") == "ledger"
    assert (dest / ".venv").is_dir()
    assert (dest / "PuckEdge.exe").read_bytes() == b"old-exe"
    assert (dest / "app.py").read_text(encoding="utf-8") == "print('new')\n"
    assert (dest / "src" / "new_module.py").is_file()
