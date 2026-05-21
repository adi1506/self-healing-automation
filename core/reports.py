from __future__ import annotations
import os
from core.excel_manager import ExcelManager


def _all_urls(scans_dir: str) -> list[str]:
    em = ExcelManager(data_dir=scans_dir)
    return em.list_scanned_urls()


def aggregate_runs(scans_dir: str) -> list[dict]:
    em = ExcelManager(data_dir=scans_dir)
    out: list[dict] = []
    for url in em.list_scanned_urls():
        for row in em.read_run_results(url) or []:
            rec = {
                "url": url,
                "run_id": row.get("run_id", ""),
                "timestamp": row.get("timestamp", ""),
                "test_case_name": row.get("test_case_name", ""),
                "row_label": row.get("row_label", ""),
                "element_name": row.get("element_name", ""),
                "expected_value": row.get("expected_value", ""),
                "actual_value": row.get("actual_value", ""),
                "status": row.get("status", ""),
                "screenshot": row.get("screenshot", ""),
            }
            out.append(rec)
    out.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    return out


def aggregate_heal_events(scans_dir: str) -> list[dict]:
    em = ExcelManager(data_dir=scans_dir)
    out: list[dict] = []
    for url in em.list_scanned_urls():
        for row in em.read_heal_history(url) or []:
            out.append({
                "url": url,
                "heal_id": row.get("heal_id", ""),
                "timestamp": row.get("timestamp", ""),
                "element_name": row.get("element_name", ""),
                "change_type": row.get("change_type", ""),
                "change_details": row.get("change_details", ""),
                "healed_by": row.get("healed_by", ""),
            })
    out.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    return out


def aggregate_activity(scans_dir: str) -> list[dict]:
    em = ExcelManager(data_dir=scans_dir)
    out: list[dict] = []
    for url in em.list_scanned_urls():
        for row in em.read_scan_history(url) or []:
            out.append({
                "kind": "scan", "url": url, "timestamp": row.get("timestamp", ""),
                "summary": f"{row.get('total_elements', 0)} elements scanned",
            })
        for row in em.read_run_results(url) or []:
            out.append({
                "kind": "run", "url": url, "timestamp": row.get("timestamp", ""),
                "summary": f"{row.get('test_case_name', '')} → {row.get('status', '')}",
            })
        for row in em.read_heal_history(url) or []:
            out.append({
                "kind": "heal", "url": url, "timestamp": row.get("timestamp", ""),
                "summary": f"{row.get('element_name', '')} healed by {row.get('healed_by', '')}",
            })
    out.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    return out


def counters(scans_dir: str) -> dict:
    runs = aggregate_runs(scans_dir)
    heals = aggregate_heal_events(scans_dir)
    passing = sum(1 for r in runs if r["status"] == "PASS")
    failing = sum(1 for r in runs if r["status"] == "FAIL")
    return {"passing": passing, "failing": failing, "healed": len(heals)}
