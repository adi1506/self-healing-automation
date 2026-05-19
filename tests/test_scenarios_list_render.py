from types import SimpleNamespace

from ui.scenarios.list import partition_and_sort_scenarios


def _sc(id_: str, name: str = "") -> SimpleNamespace:
    return SimpleNamespace(id=id_, name=name or id_)


def test_partition_separates_run_from_never_run():
    scs = [_sc("a"), _sc("b"), _sc("c")]
    last_status_by_name = {
        "a": ("PASS", "2026-05-18 10:00:00"),
        "b": ("", ""),
        "c": ("FAIL", "2026-05-19 09:00:00"),
    }
    run_group, never_run = partition_and_sort_scenarios(scs, last_status_by_name)
    assert [s.id for s in run_group] == ["c", "a"]  # newest first
    assert [s.id for s in never_run] == ["b"]


def test_partition_name_breaks_timestamp_tie():
    scs = [_sc("z"), _sc("a")]
    last_status_by_name = {
        "z": ("PASS", "2026-05-18 10:00:00"),
        "a": ("PASS", "2026-05-18 10:00:00"),
    }
    run_group, never_run = partition_and_sort_scenarios(scs, last_status_by_name)
    assert [s.id for s in run_group] == ["a", "z"]
    assert never_run == []


def test_partition_handles_all_never_run():
    scs = [_sc("b"), _sc("a")]
    run_group, never_run = partition_and_sort_scenarios(scs, {})
    assert run_group == []
    assert [s.id for s in never_run] == ["a", "b"]  # alphabetical
