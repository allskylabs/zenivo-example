"""Software update: check GitHub releases (automatic, once a day by default) and apply a release by hand.

Checking only reads the public releases list of the repository; nothing about the station is sent. Applying is
always started by the admin: it downloads the release archive, checks it (version, byte-compile), keeps a backup of the
program files, swaps them in (never touching data/, the virtual environment or vendor/), installs changed Python
requirements and restarts the service (systemd brings it back). A git checkout is updated with `git merge --ff-only`.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from packaging.version import InvalidVersion, Version

from src import net
from src.version import __version__

REPO = "hoanb1/zenivo"
RELEASES_URL = f"https://api.github.com/repos/{REPO}/releases?per_page=10"
CHECK_EVERY_S = 24 * 3600.0
FIRST_CHECK_DELAY_S = 180.0
MAX_ARCHIVE_BYTES = 120 * 1024 * 1024
KEEP_BACKUPS = 3
# Replaced as whole directories / single files; everything else in the install directory is left alone
APP_DIRS = ("src", "scripts", "deploy")
APP_FILES = ("run_allsky.py", "requirements.txt", "install.sh", "pyproject.toml")


def restart_process(delay: float = 2.0) -> str:
    """Restart this service shortly (so the HTTP answer gets out first): under systemd by ending the process, which
    `Restart=always` brings back; otherwise by re-running the same command line in place."""
    systemd = bool(os.environ.get("INVOCATION_ID"))

    def go() -> None:
        if "pytest" in sys.modules:   # never restart a test run
            return
        if systemd:
            os.kill(os.getpid(), signal.SIGTERM)
        else:
            os.execv(sys.executable, [sys.executable] + list(getattr(sys, "orig_argv", sys.argv))[1:])

    threading.Timer(delay, go).start()
    return "systemd" if systemd else "reexec"


def parse_version(tag: str) -> Optional[Version]:
    try:
        return Version(str(tag).strip().lstrip("vV"))
    except InvalidVersion:
        return None


def pick_latest(releases: List[Dict[str, Any]], current: str) -> Optional[Dict[str, Any]]:
    """Newest release; a stable install ignores pre-releases, a pre-release install sees both."""
    cur = parse_version(current)
    allow_pre = bool(cur and cur.is_prerelease)
    best, best_v = None, None
    for r in releases:
        if r.get("draft"):
            continue
        v = parse_version(r.get("tag_name", ""))
        if v is None or (v.is_prerelease and not allow_pre):
            continue
        if best_v is None or v > best_v:
            best, best_v = r, v
    return best


class UpdateManager:
    def __init__(self, install_dir: str, data_dir: str, get_settings: Callable[[], Dict[str, Any]],
                 save_settings: Callable[[Dict[str, Any]], None]):
        self.install_dir = os.path.abspath(install_dir)
        self.data_dir = data_dir
        self._get, self._save = get_settings, save_settings
        self.lock = threading.Lock()
        self.state: Dict[str, Any] = {"checked_at": None, "latest": None, "error": None}
        self.apply_state: Dict[str, Any] = {"state": "idle", "step": "", "message": ""}
        self._state_path = os.path.join(data_dir, "update_state.json")
        try:
            with open(self._state_path, encoding="utf-8") as fh:
                saved = json.load(fh)
            self.state.update({k: saved.get(k) for k in ("checked_at", "latest", "last_update")})
        except (OSError, ValueError):
            pass

    # ------------------------------------------------------------ settings
    def settings(self) -> Dict[str, Any]:
        s = self._get() or {}
        return {"auto_check": bool(s.get("auto_check", True))}

    def set_settings(self, auto_check: bool) -> Dict[str, Any]:
        self._save({"auto_check": bool(auto_check)})
        return self.settings()

    # ------------------------------------------------------------ check
    def _persist(self) -> None:
        try:
            tmp = self._state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({k: self.state.get(k) for k in ("checked_at", "latest", "last_update")}, fh)
            os.replace(tmp, self._state_path)
        except OSError:
            pass

    def check(self) -> Dict[str, Any]:
        """Ask GitHub for the newest release (one small request) and remember the answer."""
        try:
            req = net.urllib.request.Request(
                RELEASES_URL, headers={"Accept": "application/vnd.github+json", "User-Agent": f"Zenivo/{__version__}"})
            with net.urlopen(req, 15) as resp:
                releases = json.loads(resp.read().decode("utf-8"))
            rel = pick_latest(releases, __version__)
            latest = None
            if rel:
                latest = {"version": rel["tag_name"].lstrip("vV"), "tag": rel["tag_name"], "name": rel.get("name") or "",
                          "url": rel.get("html_url"), "published_at": rel.get("published_at"),
                          "prerelease": bool(rel.get("prerelease")), "tarball_url": rel.get("tarball_url"),
                          "notes": (rel.get("body") or "")[:4000]}
            self.state.update({"checked_at": time.time(), "latest": latest, "error": None})
        except Exception as exc:  # offline, rate limited, bad JSON: keep the last answer
            self.state.update({"checked_at": time.time(), "error": f"{type(exc).__name__}: {exc}"[:200]})
        self._persist()
        return self.status()

    def status(self) -> Dict[str, Any]:
        latest = self.state.get("latest")
        lv, cv = parse_version((latest or {}).get("version", "")), parse_version(__version__)
        return {
            "current": __version__,
            "latest": latest,
            "available": bool(lv and cv and lv > cv),
            "checked_at": self.state.get("checked_at"),
            "error": self.state.get("error"),
            "auto_check": self.settings()["auto_check"],
            "install": "git" if os.path.isdir(os.path.join(self.install_dir, ".git")) else "files",
            "systemd": bool(os.environ.get("INVOCATION_ID")),
            "apply": dict(self.apply_state),
            "last_update": self.state.get("last_update"),
            "backups": [b["name"] for b in self.backups()],
        }

    def start_background(self) -> None:
        def loop() -> None:
            time.sleep(FIRST_CHECK_DELAY_S)
            while True:
                if self.settings()["auto_check"]:
                    self.check()
                time.sleep(CHECK_EVERY_S)
        threading.Thread(target=loop, name="update-check", daemon=True).start()

    # ------------------------------------------------------------ apply
    def _step(self, state: str, step: str, message: str = "") -> None:
        self.apply_state = {"state": state, "step": step, "message": message}

    def apply(self) -> Dict[str, Any]:
        """Start the update in a background thread (progress in status()['apply'])."""
        with self.lock:
            if self.apply_state["state"] == "running":
                raise RuntimeError("An update is already running")
            latest = self.state.get("latest")
            if not latest:
                raise RuntimeError("No release known yet: check for updates first")
            lv, cv = parse_version(latest["version"]), parse_version(__version__)
            if not (lv and cv and lv > cv):
                raise RuntimeError("Already up to date")
            self._step("running", "start", f"Updating to {latest['version']}")
        threading.Thread(target=self._apply_worker, args=(dict(latest),), name="update-apply", daemon=True).start()
        return self.status()

    def _apply_worker(self, latest: Dict[str, Any]) -> None:
        try:
            if os.path.isdir(os.path.join(self.install_dir, ".git")):
                self._apply_git(latest)
            else:
                self._apply_archive(latest)
            self.state["last_update"] = {"from": __version__, "to": latest["version"], "at": time.time()}
            self._persist()
            self._step("done", "done", f"Updated to {latest['version']}. Restarting the service...")
            restart_process(3.0)
        except Exception as exc:
            self._step("error", self.apply_state.get("step", ""), f"{type(exc).__name__}: {exc}"[:300])

    def _run(self, cmd: List[str], timeout: float, cwd: Optional[str] = None) -> str:
        p = subprocess.run(cmd, cwd=cwd or self.install_dir, capture_output=True, text=True, timeout=timeout)
        if p.returncode != 0:
            raise RuntimeError(f"{' '.join(cmd[:3])} failed: {(p.stderr or p.stdout).strip()[-300:]}")
        return p.stdout

    def _apply_git(self, latest: Dict[str, Any]) -> None:
        self._step("running", "git", "Fetching the release")
        if self._run(["git", "status", "--porcelain", "--untracked-files=no"], 30).strip():
            raise RuntimeError("The checkout has local changes: commit or stash them, then update again")
        self._run(["git", "fetch", "--tags", "--quiet", "origin"], 180)
        self._run(["git", "merge", "--ff-only", latest["tag"]], 60)
        self._install_requirements(None)

    def _download(self, url: str, dest: str) -> None:
        req = net.urllib.request.Request(url, headers={"User-Agent": f"Zenivo/{__version__}"})
        size = 0
        with net.urlopen(req, 60) as resp, open(dest, "wb") as out:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_ARCHIVE_BYTES:
                    raise RuntimeError("Release archive is too large")
                out.write(chunk)

    @staticmethod
    def _extract(archive: str, dest: str) -> str:
        with tarfile.open(archive, "r:*") as tar:
            for m in tar.getmembers():   # no links, no absolute or parent paths
                if m.issym() or m.islnk() or m.name.startswith(("/", "..")) or ".." in m.name.split("/"):
                    raise RuntimeError(f"Unsafe path in the archive: {m.name}")
            tar.extractall(dest)
        tops = [d for d in os.listdir(dest) if os.path.isdir(os.path.join(dest, d))]
        if len(tops) != 1:
            raise RuntimeError("Unexpected archive layout")
        return os.path.join(dest, tops[0])

    def _apply_archive(self, latest: Dict[str, Any]) -> None:
        url = latest.get("tarball_url")
        if not url:
            raise RuntimeError("The release has no archive")
        tmp = tempfile.mkdtemp(prefix="update-", dir=self.data_dir)
        try:
            self._step("running", "download", "Downloading the release")
            arc = os.path.join(tmp, "release.tar.gz")
            self._download(url, arc)
            self._step("running", "verify", "Checking the release")
            new_root = self._extract(arc, os.path.join(tmp, "x"))
            ns: Dict[str, Any] = {}
            with open(os.path.join(new_root, "src", "version.py"), encoding="utf-8") as fh:
                exec(compile(fh.read(), "version.py", "exec"), ns)  # a constants-only file
            if parse_version(ns.get("__version__", "")) != parse_version(latest["version"]) or not os.path.isfile(
                    os.path.join(new_root, "run_allsky.py")):
                raise RuntimeError("The archive does not contain the announced version")
            self._run([sys.executable, "-m", "compileall", "-q", os.path.join(new_root, "src")], 300)
            self._step("running", "backup", "Backing up the current version")
            self._backup()
            old_req = self._sha(os.path.join(self.install_dir, "requirements.txt"))
            self._step("running", "install", "Replacing program files")
            self._swap(new_root)
            if self._sha(os.path.join(self.install_dir, "requirements.txt")) != old_req:
                try:
                    self._install_requirements(None)
                except Exception:
                    self.rollback(restart=False)
                    raise
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @staticmethod
    def _sha(path: str) -> str:
        try:
            with open(path, "rb") as fh:
                return hashlib.sha256(fh.read()).hexdigest()
        except OSError:
            return ""

    def _install_requirements(self, _unused: Any) -> None:
        req = os.path.join(self.install_dir, "requirements.txt")
        if os.path.isfile(req) and os.path.isdir(os.path.join(self.install_dir, ".venv")):
            self._step("running", "dependencies", "Installing Python requirements")
            self._run([sys.executable, "-m", "pip", "install", "-q", "-r", req], 900)

    def _swap(self, new_root: str) -> None:
        for name in APP_DIRS:
            src = os.path.join(new_root, name)
            if not os.path.isdir(src):
                continue
            dst = os.path.join(self.install_dir, name)
            old = dst + ".old"
            shutil.rmtree(old, ignore_errors=True)
            if os.path.isdir(dst):
                os.rename(dst, old)
            shutil.copytree(src, dst)
            shutil.rmtree(old, ignore_errors=True)
        for name in APP_FILES:
            src = os.path.join(new_root, name)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(self.install_dir, name))

    # ------------------------------------------------------------ backup / rollback
    def _backup_dir(self) -> str:
        d = os.path.join(self.data_dir, "update_backup")
        os.makedirs(d, exist_ok=True)
        return d

    def backups(self) -> List[Dict[str, Any]]:
        d = os.path.join(self.data_dir, "update_backup")
        try:
            names = sorted((n for n in os.listdir(d) if n.endswith(".tgz")), reverse=True)
        except OSError:
            return []
        return [{"name": n, "path": os.path.join(d, n)} for n in names]

    def _backup(self) -> str:
        path = os.path.join(self._backup_dir(), f"{__version__}-{time.strftime('%Y%m%d-%H%M%S')}.tgz")
        with tarfile.open(path, "w:gz") as tar:
            for name in APP_DIRS + APP_FILES:
                p = os.path.join(self.install_dir, name)
                if os.path.exists(p):
                    tar.add(p, arcname=name, filter=lambda ti: None if "__pycache__" in ti.name else ti)
        for old in self.backups()[KEEP_BACKUPS:]:
            try:
                os.remove(old["path"])
            except OSError:
                pass
        return path

    def rollback(self, restart: bool = True) -> Dict[str, Any]:
        """Put the program files of the newest backup back (admin, or automatically when requirements fail)."""
        bks = self.backups()
        if not bks:
            raise RuntimeError("No backup to go back to")
        buf = io.BytesIO()
        with open(bks[0]["path"], "rb") as fh:
            buf.write(fh.read())
        buf.seek(0)
        tmp = tempfile.mkdtemp(prefix="rollback-", dir=self.data_dir)
        try:
            with tarfile.open(fileobj=buf, mode="r:gz") as tar:
                for m in tar.getmembers():
                    if m.issym() or m.islnk() or m.name.startswith(("/", "..")) or ".." in m.name.split("/"):
                        raise RuntimeError("Unsafe backup")
                tar.extractall(tmp)
            self._swap(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self._step("done", "rollback", f"Restored {bks[0]['name']}" + (". Restarting..." if restart else ""))
        if restart:
            restart_process(3.0)
        return self.status()
