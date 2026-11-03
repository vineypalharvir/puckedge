import os
from pathlib import Path

os.environ["PUCKEDGE_DISABLE_SCHEDULER"] = "1"
os.environ["PUCKEDGE_LIVE"] = "0"

from streamlit.testing.v1 import AppTest

APP_FILE = Path(__file__).resolve().parents[1] / "app.py"


def test_non_owner_cannot_see_the_sharp_key(tmp_path, monkeypatch):
    monkeypatch.setenv("PUCKEDGE_DB", str(tmp_path / "ui.db"))
    monkeypatch.setenv("PUCKEDGE_LIVE", "0")
    monkeypatch.setenv("PUCKEDGE_DISABLE_SCHEDULER", "1")
    monkeypatch.setenv("SHARPAPI_KEY", "sharp-secret-should-not-render")
    app = AppTest.from_file(str(APP_FILE), default_timeout=60)
    app.session_state["account"] = {"name": "Test User", "username": "tester", "ips": ["127.0.0.1"]}
    app.session_state["owner_override"] = True
    app.run()
    assert not app.exception, app.exception
    pages = next(item for item in app.radio if "Setup" in item.options)
    pages.set_value("Setup").run()
    assert not app.exception, app.exception
    visible = " ".join(block.value for block in app.markdown)
    visible += " ".join(getattr(box, "value", "") or "" for box in app.text_input)
    assert "sharp-secret-should-not-render" not in visible
    assert not any(box.label == "SharpAPI key" for box in app.text_input)


def test_dashboard_generates_and_saves_a_paper_ticket(tmp_path, monkeypatch):
    monkeypatch.setenv("PUCKEDGE_DB", str(tmp_path / "ui.db"))
    monkeypatch.setenv("PUCKEDGE_LIVE", "0")
    monkeypatch.setenv("PUCKEDGE_DISABLE_SCHEDULER", "1")
    app = AppTest.from_file(str(APP_FILE), default_timeout=60)
    app.session_state["account"] = {"name": "Test User", "username": "tester", "ips": ["127.0.0.1"]}
    app.session_state["owner_override"] = True
    app.run()
    assert not app.exception, app.exception
    page_text = " ".join(block.value for block in app.markdown)
    assert "Created and Owned by Harvir Vineypal" in page_text
    generate = next(button for button in app.button if button.label == "Generate optimal parlays")
    generate.click()
    app.run()
    assert not app.exception, app.exception
    save = next(button for button in app.button if button.label == "Save & Track (Paper)")
    save.click()
    app.run()
    assert not app.exception, app.exception
    assert any("already in the ledger" in text.value.lower() or "paper" in text.value.lower() for text in app.success) or any(
        "ledger" in (toast.value or "").lower() or "paper" in (toast.value or "").lower() for toast in getattr(app, "toast", [])
    )
