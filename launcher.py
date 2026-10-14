"""Double-click launcher for PuckEdge.

Builds into PuckEdge.exe. Keep that file in this project folder.
If Python 3.11+ is missing, the first launch downloads the official
Windows installer and installs it for the current user, then creates
.venv, installs requirements, and opens the app.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path


CREDIT = "Created by Harvir Vineypal for larping purposes."
PYTHON_VERSION = "3.14.8"
MIN_VERSION = (3, 11)


def project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def pause() -> None:
    try:
        input("\nPress Enter to close...")
    except EOFError:
        pass


def install_arguments() -> list[str]:
    return [
        "/quiet",
        "InstallAllUsers=0",
        "PrependPath=1",
        "Include_pip=1",
        "Include_launcher=1",
        "Include_test=0",
    ]


def installer_filename(machine: str) -> str:
    normalized = machine.lower()
    arch = "arm64" if normalized in {"arm64", "aarch64"} else "amd64"
    return f"python-{PYTHON_VERSION}-{arch}.exe"


def installer_url(machine: str) -> str:
    return f"https://www.python.org/ftp/python/{PYTHON_VERSION}/{installer_filename(machine)}"


def meets_minimum(major: int, minor: int) -> bool:
    return (major, minor) >= MIN_VERSION


def executable_version(python: Path | str) -> tuple[int, int] | None:
    try:
        result = subprocess.run(
            [str(python), "-c", "import sys; print(sys.version_info.major, sys.version_info.minor)"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    parts = result.stdout.split()
    if len(parts) < 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def command_version(command: list[str]) -> tuple[int, int] | None:
    try:
        result = subprocess.run(
            command + ["-c", "import sys; print(sys.version_info.major, sys.version_info.minor)"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    parts = result.stdout.split()
    if len(parts) < 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def installed_python_exes() -> list[Path]:
    roots: list[Path] = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(Path(local) / "Programs" / "Python")
    for key in ("ProgramFiles", "ProgramFiles(x86)"):
        value = os.environ.get(key)
        if value:
            roots.append(Path(value) / "Python")
    found: list[Path] = []
    for root in roots:
        if root.is_dir():
            found.extend(path for path in root.glob("Python*/python.exe") if path.is_file())
    return found


def find_base_python() -> Path | None:
    """Return a Python 3.11+ executable, ignoring a project virtualenv."""
    candidates: list[Path] = []
    for command in (["py", "-3"], ["python"]):
        if command[0] == "python" and not shutil.which("python"):
            continue
        if command[0] == "py" and not shutil.which("py"):
            continue
        version = command_version(command)
        if version and meets_minimum(*version):
            try:
                located = subprocess.run(
                    command + ["-c", "import sys; print(sys.executable)"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=30,
                )
            except (OSError, subprocess.TimeoutExpired):
                located = None
            if located and located.returncode == 0 and located.stdout.strip():
                candidates.append(Path(located.stdout.strip()))
    candidates.extend(installed_python_exes())
    best: tuple[tuple[int, int], Path] | None = None
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen or not candidate.is_file():
            continue
        seen.add(key)
        version = executable_version(candidate)
        if version is None or not meets_minimum(*version):
            continue
        if best is None or version > best[0]:
            best = (version, candidate)
    return None if best is None else best[1]


def download_installer(url: str, dest: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "PuckEdgeSetup/1.0"})
    print(f"Downloading official Python {PYTHON_VERSION} from python.org")
    print(url)
    with urllib.request.urlopen(request, timeout=120) as response:
        total = int(response.headers.get("Content-Length") or 0)
        dest.parent.mkdir(parents=True, exist_ok=True)
        done = 0
        next_mark = 10
        with dest.open("wb") as handle:
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                done += len(chunk)
                if total:
                    percent = int(done * 100 / total)
                    if percent >= next_mark:
                        print(f"  downloaded {percent}%")
                        next_mark = percent + 10
    print("  download finished")


def install_python(machine: str | None = None) -> Path:
    machine = machine or os.environ.get("PROCESSOR_ARCHITECTURE", "AMD64")
    url = installer_url(machine)
    dest = Path(os.environ.get("TEMP", ".")) / installer_filename(machine)
    try:
        download_installer(url, dest)
    except Exception as exc:
        print(f"The python.org download failed: {exc}")
        installed = install_with_winget()
        if not installed:
            raise SystemExit(
                "Automatic Python setup failed. The download did not finish and winget could not install Python."
            ) from exc
        found = find_base_python()
        if found is None:
            raise SystemExit("winget finished, but Python 3.11+ still could not be found.")
        return found

    print("Installing Python for the current user. This can take a few minutes.")
    print("Leave this window open.")
    completed = subprocess.run([str(dest), *install_arguments()], check=False)
    if completed.returncode not in {0, 3010}:
        print(f"The official installer returned code {completed.returncode}. Trying winget.")
        if not install_with_winget():
            raise SystemExit(f"Python setup failed. Installer exit code: {completed.returncode}.")
    found = find_base_python()
    if found is None:
        raise SystemExit("Python setup finished, but python.exe could not be found afterward.")
    try:
        dest.unlink(missing_ok=True)
    except OSError:
        pass
    print(f"Python is ready: {found}")
    return found


def install_with_winget() -> bool:
    winget = shutil.which("winget")
    if not winget:
        print("winget is not available on this PC.")
        return False
    print("Installing Python with winget. This can take a few minutes.")
    completed = subprocess.run(
        [
            winget,
            "install",
            "-e",
            "--id",
            "Python.Python.3.14",
            "--scope",
            "user",
            "--silent",
            "--disable-interactivity",
            "--accept-package-agreements",
            "--accept-source-agreements",
        ],
        check=False,
    )
    return completed.returncode == 0


def ensure_base_python(finder=find_base_python, installer=install_python) -> Path:
    found = finder()
    if found is not None:
        print(f"Using Python: {found}")
        return found
    print("Python 3.11 or newer was not found.")
    print("PuckEdge will download and install it. First-time setup can take several minutes.")
    print(CREDIT)
    return installer()


def create_venv(root: Path, base_python: Path) -> Path:
    venv_python = root / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        if executable_version(venv_python) is not None:
            return venv_python
        print("The existing .venv does not run on this PC. Rebuilding it.")
        shutil.rmtree(root / ".venv", ignore_errors=True)

    print("Creating the PuckEdge environment...")
    created = subprocess.run([str(base_python), "-m", "venv", str(root / ".venv")], check=False)
    if created.returncode != 0 or not venv_python.exists():
        raise SystemExit("Could not create the local .venv.")
    print("Installing PuckEdge dependencies. This can take several minutes...")
    installed = subprocess.run(
        [str(venv_python), "-m", "pip", "install", "--upgrade", "pip"],
        check=False,
    )
    if installed.returncode != 0:
        raise SystemExit("pip could not be updated. See the messages above.")
    installed = subprocess.run(
        [str(venv_python), "-m", "pip", "install", "-r", str(root / "requirements.txt")],
        check=False,
    )
    if installed.returncode != 0:
        raise SystemExit("Dependency install failed. See the messages above.")
    return venv_python


def main() -> None:
    if "--credit" in sys.argv:
        print(CREDIT)
        return

    root = project_root()
    os.chdir(root)
    print("PuckEdge")
    print(CREDIT)
    if not (root / "app.py").exists():
        raise SystemExit(f"Keep PuckEdge.exe in the project folder. app.py was not found in {root}")
    if not (root / "requirements.txt").exists():
        raise SystemExit(f"requirements.txt was not found in {root}")

    print(f"Project folder: {root}")
    venv_existed = (root / ".venv" / "Scripts" / "python.exe").exists()
    sys.path.insert(0, str(root))
    from src.updater import apply_update

    updated, requirements_changed = apply_update(root)
    base_python = ensure_base_python()
    venv_python = create_venv(root, base_python)
    if updated and requirements_changed and venv_existed:
        print("Installing updated dependencies...")
        installed = subprocess.run(
            [str(venv_python), "-m", "pip", "install", "-r", str(root / "requirements.txt")],
            check=False,
        )
        if installed.returncode != 0:
            raise SystemExit("The update downloaded, but dependency install failed.")
    print("Opening PuckEdge in your browser...")
    command = [
        str(venv_python),
        "-m",
        "streamlit",
        "run",
        str(root / "app.py"),
        "--server.headless",
        "false",
        "--browser.gatherUsageStats",
        "false",
    ]
    code = subprocess.call(command)
    if code != 0:
        raise SystemExit(f"PuckEdge stopped with exit code {code}.")


if __name__ == "__main__":
    try:
        main()
    except SystemExit as exc:
        if exc.code not in (0, None):
            print(exc)
            pause()
            raise SystemExit(1)
        raise
    except Exception as exc:
        print(f"PuckEdge could not start: {exc}")
        pause()
        raise SystemExit(1) from exc
