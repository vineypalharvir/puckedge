import os
from pathlib import Path

os.environ["PUCKEDGE_DISABLE_SCHEDULER"] = "1"
os.environ["PUCKEDGE_LIVE"] = "0"

from streamlit.testing.v1 import AppTest

APP_FILE = Path(__file__).resolve().parents[1] / "app.py"


def test_dashboard_generates_and_saves_a_paper_ticket(tmp_path, monkeypatch):
    monkeypatch.setenv("PUCKEDGE_DB", str(tmp_path / "ui.db"))
    monkeypatch.setenv("PUCKEDGE_LIVE", "0")
    monkeypatch.setenv("PUCKEDGE_DISABLE_SCHEDULER", "1")
    app = AppTest.from_file(str(APP_FILE), default_timeout=60)
    app.run()
    assert not app.exception, app.exception
    page_text = " ".join(block.value for block in app.markdown)
    assert "Created by Harvir Vineypal for larping purposes." in page_text
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
