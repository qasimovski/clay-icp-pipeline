"""A delivery plan written in WSL must still import from Windows.

The splitters and the importer sit on opposite sides of the win32/WSL split:
`split_ce_event_tables.py` ran on Windows and wrote `C:\\...` into its plan,
while `split_ps_delivery_tables.py` ran in WSL and wrote `/mnt/c/...`. The
importer is always the Windows Playwright process, and Chrome's file chooser
cannot open a `/mnt/c` path.

Why this is worth a test rather than a convention: the failure is not a crash.
Clay creates the workbook, then the table, and only the ROWS are missing — so
the run reports success and leaves an empty table behind, which is exactly the
damage that cost a repair + re-import cycle on the Competitive Events delivery.
The `native_path` normaliser plus the up-front existence check turn that into a
refusal before anything is created.
"""

import importlib.util
import io
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOD = os.path.join(ROOT, "automation", "cleanup", "import_ce_delivery_tables.py")


def _load():
    """Import the module without running its argparse main().

    The module rebinds `sys.stdout` to a UTF-8 TextIOWrapper over
    `sys.stdout.buffer` at import time (so event names with non-ASCII characters
    print on a cp1252 console). Restoring `sys.stdout` afterwards is NOT enough:
    that wrapper closes the buffer it wrapped when it is garbage-collected, and
    pytest's captured stdout would go with it — every later test then dies with
    "I/O operation on closed file". So hand the module a throwaway stream to
    wrap and let it close that instead.
    """
    for p in ("cleanup", "clay_sync", "build_automation"):
        sys.path.insert(0, os.path.join(ROOT, "automation", p))
    saved = sys.stdout
    sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    try:
        spec = importlib.util.spec_from_file_location("import_ce_delivery", MOD)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.stdout = saved
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load()


@pytest.mark.skipif(os.name != "nt", reason="normalisation only applies on win32")
@pytest.mark.parametrize("wsl_path,expected", [
    ("/mnt/c/Users/q/ps_delivery_tables/Construction/Companies.csv",
     r"C:\Users\q\ps_delivery_tables\Construction\Companies.csv"),
    # Spaces and '&' are ordinary here — category names carry both
    # ("Asset Management & Maintenance"), so they must survive untouched.
    ("/mnt/c/a b/Asset Management & Maintenance/People.csv",
     r"C:\a b\Asset Management & Maintenance\People.csv"),
    ("/mnt/d/x/y.csv", r"D:\x\y.csv"),
])
def test_wsl_paths_become_windows_paths(mod, wsl_path, expected):
    assert mod.native_path(wsl_path) == expected


@pytest.mark.skipif(os.name != "nt", reason="normalisation only applies on win32")
def test_windows_paths_pass_through_unchanged(mod):
    """The CE plan is already native; normalising twice must be a no-op."""
    p = r"C:\Users\q\ce_event_tables\AfPA Conference\Sponsors.csv"
    assert mod.native_path(p) == p
    assert mod.native_path(mod.native_path(p)) == p


def test_short_and_unusual_inputs_are_left_alone(mod):
    """Never index past the end of a string that merely starts with /mnt."""
    for p in ("/mnt", "/mnt/", "/mnt/c", "", "relative/path.csv"):
        mod.native_path(p)          # must not raise


def test_speaker_tables_are_never_imported(mod, capsys):
    """Speakers go to the Salesforce CSV only, never to the Clay review copies (user, 29 Oct 2026).
    Solar briefly got 71 per-event "Speakers" tables; the importer now refuses them whatever wrote the plan."""
    plan = [{"event": "A Expo", "table": "Exhibitors", "kind": "companies", "csv": "x", "rows": 3},
            {"event": "A Expo", "table": "Speakers", "kind": "people", "csv": "y", "rows": 9},
            {"event": "B Expo", "table": "Sponsors Speakers People", "kind": "people", "csv": "z", "rows": 1}]
    kept = mod.drop_speaker_tables(plan)
    assert [e["table"] for e in kept] == ["Exhibitors"]
    assert "refusing 2 speakers table(s)" in capsys.readouterr().out
