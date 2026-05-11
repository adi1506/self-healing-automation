import os
from openpyxl import Workbook
from core.reports import aggregate_runs, aggregate_heal_events, aggregate_activity, counters


def _make_scan(path, sheets):
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    wb.save(path)


def _register_url(scans_dir, sanitized_name, url):
    map_path = os.path.join(scans_dir, "_url_map.txt")
    with open(map_path, "a") as f:
        f.write(f"{sanitized_name}|{url}\n")


def test_aggregate_runs_collects_from_all_scans(tmp_path):
    _make_scan(str(tmp_path / "a.xlsx"), {
        "Element Map": [["S.No", "Element Name", "Element Type"], [1, "email", "input"]],
        "Run Results": [
            ["Run ID", "Timestamp", "Test Case Name", "Element Name",
             "Expected Value", "Actual Value", "Status", "Screenshot"],
            ["R1", "2026-05-10 10:00:00", "happy", "email", "a@b.co", "a@b.co", "PASS", ""],
            ["R2", "2026-05-11 10:00:00", "bad", "email", "x", "y", "FAIL", ""],
        ],
    })
    _register_url(str(tmp_path), "a", "a")
    runs = aggregate_runs(str(tmp_path))
    assert len(runs) == 2
    assert runs[0]["timestamp"] >= runs[1]["timestamp"]  # newest first


def test_counters_reports_pass_fail_heal(tmp_path):
    _make_scan(str(tmp_path / "a.xlsx"), {
        "Element Map": [["S.No", "Element Name", "Element Type"]],
        "Run Results": [
            ["Run ID", "Timestamp", "Test Case Name", "Element Name",
             "Expected Value", "Actual Value", "Status", "Screenshot"],
            ["R1", "2026-05-11 10:00:00", "happy", "email", "x", "x", "PASS", ""],
            ["R2", "2026-05-11 10:00:00", "bad", "email", "x", "y", "FAIL", ""],
        ],
        "Heal History": [
            ["Heal ID", "Timestamp", "Element Name", "Change Type", "Change Details", "Healed By"],
            ["H1", "2026-05-11 10:00:00", "email", "CHANGED", "id changed", "Fingerprint"],
        ],
    })
    _register_url(str(tmp_path), "a", "a")
    c = counters(str(tmp_path))
    assert c["passing"] == 1
    assert c["failing"] == 1
    assert c["healed"] == 1


def test_aggregate_activity_combines_scans_runs_heals(tmp_path):
    _make_scan(str(tmp_path / "a.xlsx"), {
        "Element Map": [["S.No", "Element Name", "Element Type"]],
        "Scan History": [
            ["Scan ID", "Timestamp", "Total Elements", "New", "Changed", "Removed", "Unchanged"],
            ["S1", "2026-05-09 09:00:00", 3, 3, 0, 0, 0],
        ],
        "Run Results": [
            ["Run ID", "Timestamp", "Test Case Name", "Element Name",
             "Expected Value", "Actual Value", "Status", "Screenshot"],
            ["R1", "2026-05-11 10:00:00", "happy", "email", "x", "x", "PASS", ""],
        ],
        "Heal History": [
            ["Heal ID", "Timestamp", "Element Name", "Change Type", "Change Details", "Healed By"],
            ["H1", "2026-05-10 08:00:00", "email", "CHANGED", "id changed", "Fingerprint"],
        ],
    })
    _register_url(str(tmp_path), "a", "a")
    act = aggregate_activity(str(tmp_path))
    kinds = [a["kind"] for a in act]
    assert "scan" in kinds and "run" in kinds and "heal" in kinds
