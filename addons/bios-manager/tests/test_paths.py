"""The addon writes under GAMECORE_DATA, never under GAMECORE_PATH.

conftest.py sets the two roots to different directories: with the default
(DATA = PATH) a path joined onto the wrong root would be invisible.
"""
import importlib
import os

from conftest import CODE, DATA

import targets


def test_roots_are_distinct_and_honoured():
    assert CODE.resolve() != DATA.resolve()
    assert targets.GAMECORE_PATH == CODE and targets.GAMECORE_DATA == DATA


def test_every_write_root_is_under_data_not_code():
    import place
    import server
    for path in (targets.ADDON_DATA_DIR, targets.STAGING, place.BACKUPS, server.WORK_ROOT):
        assert DATA in path.parents and CODE not in path.parents, path
    assert targets.SYSTEMS_FILE == DATA / "config/systems.json"


def test_manager_supplied_addon_data_dir_wins(monkeypatch):
    monkeypatch.setenv("ADDON_DATA_DIR", str(DATA / "custom"))
    try:
        assert importlib.reload(targets).ADDON_DATA_DIR == DATA / "custom"
    finally:
        monkeypatch.delenv("ADDON_DATA_DIR")
        importlib.reload(targets)


def test_empty_data_falls_back_to_path(monkeypatch):
    monkeypatch.setenv("GAMECORE_DATA", "")
    try:
        assert importlib.reload(targets).GAMECORE_DATA == CODE
    finally:
        monkeypatch.setenv("GAMECORE_DATA", os.fspath(DATA))
        importlib.reload(targets)
