"""Pull a newer PuckEdge from GitHub without touching local secrets or the ledger."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import urllib.request
import zipfile
from pathlib import Path

SKIP_NAMES = {".env", ".venv", "data", "PuckEdge.exe", "build", ".git", "__pycache__"}


def update_settings(root: Path) -> tuple[str, str]:
    repo = os.getenv("PUCKEDGE_UPDATE_REPO", "").strip()
    branch = os.getenv("PUCKEDGE_UPDATE_BRANCH", "").strip()
    path = root / "update.json"
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        repo = repo or str(payload.get("repo") or "").strip()
        branch = branch or str(payload.get("branch") or "main").strip() or "main"
    return repo, branch or "main"


def _state_path(root: Path) -> Path:
    return root / "data" / "update_state.json"


def local_sha(root: Path) -> str:
    path = _state_path(root)
    if not path.is_file():
        return ""
    try:
        return str(json.loads(path.read_text(encoding="utf-8")).get("sha") or "")
    except (OSError, json.JSONDecodeError):
        return ""


def _write_state(root: Path, sha: str) -> None:
    path = _state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"sha": sha}), encoding="utf-8")


def _request(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "PuckEdgeUpdater", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def remote_sha(repo: str, branch: str) -> str:
    payload = json.loads(_request(f"https://api.github.com/repos/{repo}/commits/{branch}").decode())
    sha = str(payload.get("sha") or "")
    if not sha:
        raise RuntimeError("GitHub did not return a commit.")
    return sha


def file_hash(path: Path) -> str:
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def apply_tree(source: Path, dest: Path) -> None:
    """Copy a downloaded project tree over dest, leaving secrets and the ledger alone."""
    for item in source.iterdir():
        if item.name in SKIP_NAMES or item.name.endswith(".pyc"):
            continue
        target = dest / item.name
        if item.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(item, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            if target.exists() and target.name == ".env":
                continue
            shutil.copy2(item, target)


def _zip_root(extracted: Path) -> Path:
    folders = [path for path in extracted.iterdir() if path.is_dir()]
    if len(folders) != 1:
        raise RuntimeError("The GitHub download did not contain one project folder.")
    return folders[0]


def apply_update(root: Path) -> tuple[bool, bool]:
    """Download a newer commit. Returns (updated, requirements_changed)."""
    if os.getenv("PUCKEDGE_DISABLE_UPDATES") == "1":
        return False, False
    repo, branch = update_settings(root)
    if not repo or "/" not in repo:
        print("GitHub updates are off. Put owner/repo in update.json after you create the GitHub repo.")
        return False, False
    try:
        sha = remote_sha(repo, branch)
    except Exception as exc:
        print(f"Could not check GitHub for updates: {exc}")
        return False, False
    current = local_sha(root)
    if current == sha:
        print(f"Up to date with {repo} ({sha[:7]}).")
        return False, False
    print(f"Downloading {repo} {sha[:7]}...")
    try:
        blob = _request(f"https://codeload.github.com/{repo}/zip/refs/heads/{branch}")
    except Exception as exc:
        print(f"Update download failed: {exc}")
        return False, False
    before = file_hash(root / "requirements.txt")
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        extracted = Path(tmp) / "zip"
        extracted.mkdir()
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            _safe_extract(archive, extracted)
        apply_tree(_zip_root(extracted), root)
    after = file_hash(root / "requirements.txt")
    _write_state(root, sha)
    print(f"Updated to {sha[:7]}. Your .env, ledger, and Python install were left alone.")
    return True, before != after


def _safe_extract(archive: zipfile.ZipFile, dest: Path) -> None:
    root = dest.resolve()
    for info in archive.infolist():
        target = (dest / info.filename).resolve()
        if os.path.commonpath([str(root), str(target)]) != str(root):
            raise RuntimeError(f"Unsafe path in update zip: {info.filename}")
    archive.extractall(dest)


def describe_update(root: Path) -> str:
    repo, _branch = update_settings(root)
    if not repo:
        return "GitHub updates are off until update.json has owner/repo."
    sha = local_sha(root)
    if not sha:
        return f"Updates: {repo}. Open PuckEdge.exe to sync."
    return f"Updates: {repo} ({sha[:7]})."


def peek_update(root: Path) -> str:
    repo, branch = update_settings(root)
    if not repo or "/" not in repo:
        return describe_update(root)
    local = local_sha(root)
    try:
        remote = remote_sha(repo, branch)
    except Exception:
        return f"Updates: {repo} (could not reach GitHub)."
    if not local:
        return f"Update is available on {repo}. Close this window and open PuckEdge.exe."
    if local == remote:
        return f"Up to date with {repo} ({local[:7]})."
    return f"Update available on {repo}. Close this window and open PuckEdge.exe."
