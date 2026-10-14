from pathlib import Path

from launcher import (
    CREDIT,
    ensure_base_python,
    install_arguments,
    installer_url,
    meets_minimum,
)


def test_credit_line():
    assert CREDIT == "Created by Harvir Vineypal for larping purposes."


def test_python_minimum():
    assert not meets_minimum(3, 10)
    assert meets_minimum(3, 11)
    assert meets_minimum(3, 14)


def test_quiet_per_user_installer_flags():
    flags = install_arguments()
    assert "/quiet" in flags
    assert "InstallAllUsers=0" in flags
    assert "PrependPath=1" in flags
    assert "Include_pip=1" in flags
    url = installer_url("AMD64")
    assert url.startswith("https://www.python.org/ftp/python/")
    assert url.endswith("amd64.exe")


def test_missing_python_calls_the_installer_and_existing_python_does_not(tmp_path):
    calls = {"count": 0}

    def installer():
        calls["count"] += 1
        return tmp_path / "python.exe"

    assert ensure_base_python(finder=lambda: None, installer=installer) == tmp_path / "python.exe"
    assert calls["count"] == 1

    ready = tmp_path / "already.exe"
    assert ensure_base_python(finder=lambda: ready, installer=installer) == ready
    assert calls["count"] == 1
