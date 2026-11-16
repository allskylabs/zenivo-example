import io
import shutil
import tarfile

import pytest

from src import updater
from src.updater import UpdateManager, parse_version, pick_latest


def _rel(tag, pre=False, draft=False):
    return {"tag_name": tag, "prerelease": pre, "draft": draft, "tarball_url": "x", "name": tag}


def test_versions_compare_like_pep440():
    assert parse_version("v1.3.0-beta.1") == parse_version("1.3.0b1") < parse_version("1.3.0")
    assert parse_version("nonsense") is None


def test_prerelease_install_sees_betas_stable_does_not():
    rels = [_rel("v1.3.0-beta.2", pre=True), _rel("v1.2.0"), _rel("v9.9.9", draft=True)]
    assert pick_latest(rels, "1.3.0b1")["tag_name"] == "v1.3.0-beta.2"
    assert pick_latest(rels, "1.2.0")["tag_name"] == "v1.2.0"


def _mgr(tmp_path):
    inst, data = tmp_path / "inst", tmp_path / "inst" / "data"
    (inst / "src").mkdir(parents=True)
    data.mkdir()
    (inst / "src" / "version.py").write_text('__version__ = "1.0.0"\n')
    (inst / "src" / "old.py").write_text("OLD = 1\n")
    (inst / "run_allsky.py").write_text("# old\n")
    (inst / "requirements.txt").write_text("a\n")
    cfg = {}
    return UpdateManager(str(inst), str(data), lambda: cfg, cfg.update), inst


def _release_tar(path, version):
    with tarfile.open(path, "w:gz") as t:
        for name, text in (("zen-abc/src/version.py", f'__version__ = "{version}"\n'), ("zen-abc/src/new.py", "NEW = 1\n"),
                           ("zen-abc/run_allsky.py", "# new\n"), ("zen-abc/requirements.txt", "a\n")):
            b = text.encode()
            ti = tarfile.TarInfo(name)
            ti.size = len(b)
            t.addfile(ti, io.BytesIO(b))


def test_apply_swaps_files_keeps_data_and_rolls_back(tmp_path, monkeypatch):
    m, inst = _mgr(tmp_path)
    monkeypatch.setattr(updater, "__version__", "1.0.0")
    (inst / "data" / "keep.json").write_text("{}")
    monkeypatch.setattr(m, "_download", lambda url, dest: _release_tar(dest, "1.1.0"))
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    m.state["latest"] = {"version": "1.1.0", "tag": "v1.1.0", "tarball_url": "x"}
    m._apply_worker(m.state["latest"])
    assert m.apply_state["state"] == "done", m.apply_state
    assert (inst / "src" / "new.py").exists() and not (inst / "src" / "old.py").exists()
    assert (inst / "run_allsky.py").read_text() == "# new\n" and (inst / "data" / "keep.json").exists()
    assert m.backups()
    m.rollback(restart=False)
    assert (inst / "src" / "old.py").exists() and not (inst / "src" / "new.py").exists()


def test_wrong_version_in_archive_is_refused(tmp_path, monkeypatch):
    m, inst = _mgr(tmp_path)
    monkeypatch.setattr(m, "_download", lambda url, dest: _release_tar(dest, "6.6.6"))
    m._apply_worker({"version": "1.1.0", "tag": "v1.1.0", "tarball_url": "x"})
    assert m.apply_state["state"] == "error" and (inst / "src" / "old.py").exists()


def test_unsafe_archive_path_is_refused(tmp_path):
    bad = tmp_path / "bad.tgz"
    with tarfile.open(bad, "w:gz") as t:
        ti = tarfile.TarInfo("top/../../evil")
        ti.size = 0
        t.addfile(ti, io.BytesIO(b""))
    with pytest.raises(RuntimeError):
        UpdateManager._extract(str(bad), str(tmp_path / "out"))
    shutil.rmtree(tmp_path / "out", ignore_errors=True)


def test_status_reports_available_only_for_a_newer_release(tmp_path, monkeypatch):
    m, _ = _mgr(tmp_path)
    monkeypatch.setattr(updater, "__version__", "1.0.0")
    m.state["latest"] = {"version": "1.0.0"}
    assert not m.status()["available"]
    m.state["latest"] = {"version": "1.2.0"}
    assert m.status()["available"] and m.status()["auto_check"] is True
    with pytest.raises(RuntimeError):
        m.state["latest"] = {"version": "1.0.0"}
        m.apply()


def test_restart_uses_systemd_or_reexec_without_acting_now(monkeypatch):
    started = []

    class _T:
        def __init__(self, delay, fn):
            self.fn = fn

        def start(self):
            started.append(self.fn)

    monkeypatch.setattr(updater.threading, "Timer", _T)
    monkeypatch.setenv("INVOCATION_ID", "x")
    assert updater.restart_process(1.0) == "systemd"
    monkeypatch.delenv("INVOCATION_ID")
    assert updater.restart_process(1.0) == "reexec" and len(started) == 2
