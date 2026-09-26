from datetime import datetime

from app import scheduler, settings


def test_in_window():
    assert scheduler.in_window(datetime(2026, 1, 1, 4, 0), "03:00-05:00")
    assert not scheduler.in_window(datetime(2026, 1, 1, 5, 0), "03:00-05:00")
    assert scheduler.in_window(datetime(2026, 1, 1, 23, 30), "23:00-02:00")
    assert scheduler.in_window(datetime(2026, 1, 1, 1, 0), "23:00-02:00")
    assert not scheduler.in_window(datetime(2026, 1, 1, 12, 0), "23:00-02:00")
    assert scheduler.in_window(datetime(2026, 1, 1, 12, 0), None)


def test_next_run(env):
    assert scheduler.next_run() is None  # mode off
    settings.update({"auto_update": {"mode": "notify", "window": "03:00-05:00"}})
    now = datetime(2026, 1, 1, 12, 0)
    assert scheduler.next_run(now) == datetime(2026, 1, 2, 3, 0)
    settings.update({"auto_update": {"window": None}})
    assert scheduler.next_run(now) == now


def test_settings_validation(env):
    import pytest
    for bad in ({"auto_update": {"mode": "yolo"}}, {"auto_update": {"window": "25:00-01:00"}},
                {"source_map": {"bukkit:x": {"kind": "github", "id": "../../x"}}},
                {"pins": {"not a key": "1"}}, {"default_source": "nope"}):
        with pytest.raises(settings.SettingsError):
            settings.update(bad)
