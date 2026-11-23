import importlib.util
from pathlib import Path

_p = Path(__file__).resolve().parents[2] / "scripts" / "ds_lint.py"
_s = importlib.util.spec_from_file_location("ds_lint", _p)
ds_lint = importlib.util.module_from_spec(_s)
_s.loader.exec_module(ds_lint)


def test_ds_files_are_clean():
    assert ds_lint.lint_ds() == []


def test_rules_detect_violations():
    css = ".a{color:#fff;font-size:10px;border-radius:6px;z-index:999}"
    msgs = " ".join(m for _, _, m in ds_lint.lint_text(css, "x.css"))
    for key in ("hex", "font-size", "radius", "z-index"):
        assert key in msgs
