"""file_parser — opens the customer Excel, applies the template's column map,
runs employee matching + reconciliation, and persists results onto the Run.

Entry point: run_parse(run_name, user) — called from PayrollImportRun.trigger_parse via frappe.enqueue.
"""

import json
import os

import frappe
from frappe import _
from openpyxl import load_workbook

from . import transforms
from . import employee_matcher
from . import reconciliation
from . import validators


# Canonical numeric fields parsed off the customer sheet (in order they appear on Row).
NUMERIC_FIELDS = (
    "basic_per_contract", "housing_per_contract", "transportation_per_contract",
    "other_allowance_per_contract", "total_per_contract",
    "working_days",
    "basic_per_working_days", "housing_per_working_days",
    "transportation_per_working_days", "other_allowance_per_working_days",
    "overtime", "other_income", "deductions", "hq_deductions",
    "gosi_from_file", "net_salary_from_file",
)


def run_parse(run_name, user=None):
    """Background-job entry point. Called from PayrollImportRun.trigger_parse via frappe.enqueue."""
    run = frappe.get_doc("Payroll Import Run", run_name)
    template = frappe.get_doc("Payroll Import Template", run.template)

    try:
        file_path = _resolve_attached_file_path(run.source_file)
        wb = load_workbook(file_path, data_only=True, read_only=True)
        sheet_name = template.sheet_name_override or _pick_first_data_sheet(wb)
        if sheet_name not in wb.sheetnames:
            raise ValueError(
                f"Sheet '{sheet_name}' not found in source file. "
                f"Available sheets: {wb.sheetnames}"
            )
        ws = wb[sheet_name]
        run.db_set("source_file_sheet_used", sheet_name)

        header_row_idx = int(template.header_row_index or 1)
        headers = _read_header_row(ws, header_row_idx)
        col_map = _build_column_index_map(template.column_map, headers)

        parsed_rows = []
        rows_read = 0
        for row_idx, row in enumerate(
            ws.iter_rows(min_row=header_row_idx + 1, values_only=True),
            start=header_row_idx + 1,
        ):
            if all(c in (None, "") for c in row):
                continue
            rows_read += 1
            parsed = _parse_row(row, col_map)
            parsed["_row_index"] = row_idx

            if template.skip_blank_id_rows and not parsed.get("raw_id_value"):
                continue
            parsed_rows.append(parsed)

        wb.close()
        run.db_set("source_file_rows_total", rows_read)

        # Match
        indexes = employee_matcher.build_indexes(template)
        matched, unmatched = [], []
        for p in parsed_rows:
            m = employee_matcher.match_row(p, indexes, template)
            p["_match"] = m
            (matched if m["employee"] else unmatched).append(p)

        # Reconcile: who's expected but absent?
        matched_emp_ids = {p["_match"]["employee"] for p in matched}
        expected_emp_ids = set(indexes["employees"].keys())
        unaccounted_emp_ids = expected_emp_ids - matched_emp_ids

        _persist_results(run, matched, unmatched, unaccounted_emp_ids, indexes)

        # Re-fetch with parsed_rows attached, then run validators
        run = frappe.get_doc("Payroll Import Run", run.name)
        validators.validate_run(run, template, indexes)
        run.save(ignore_permissions=True)
        frappe.db.commit()

        run.db_set("status", "Reconciliation Pending")
        run.db_set("parse_error_log", "")

    except Exception:
        frappe.log_error(
            title=f"Payroll Import parse failed: {run_name}",
            message=frappe.get_traceback(),
        )
        run.db_set("parse_error_log", frappe.get_traceback())
        run.db_set("status", "Draft")
        raise


def _resolve_attached_file_path(file_url):
    """Convert a Frappe File URL (/files/foo.xlsx or /private/files/foo.xlsx)
    into an absolute filesystem path. Also tolerates absolute paths."""
    if not file_url:
        raise ValueError(_("Source file is empty"))
    if file_url.startswith("/files/"):
        return os.path.join(frappe.get_site_path("public"), file_url.lstrip("/"))
    if file_url.startswith("/private/files/"):
        return os.path.join(frappe.get_site_path(), file_url.lstrip("/"))
    if file_url.startswith("/"):
        return os.path.join(frappe.get_site_path(), file_url.lstrip("/"))
    return file_url


def _pick_first_data_sheet(wb):
    """Pick the first sheet with at least 2 non-empty rows in its first 10 rows."""
    for sn in wb.sheetnames:
        ws = wb[sn]
        non_empty = 0
        for row in ws.iter_rows(min_row=1, max_row=10, values_only=True):
            if any(c not in (None, "") for c in row):
                non_empty += 1
                if non_empty >= 2:
                    return sn
    return wb.sheetnames[0]


def _read_header_row(ws, row_idx):
    return list(next(ws.iter_rows(min_row=row_idx, max_row=row_idx, values_only=True)))


def _normalize_header(h):
    return str(h).strip().lower() if h is not None else ""


def _build_column_index_map(column_map_rows, headers):
    """Returns {target_field: {"index": 0-based int or None, "transform": str, "required": bool}}."""
    normalized = [_normalize_header(h) for h in headers]
    result = {}
    for r in column_map_rows or []:
        if not r.target_field:
            continue
        idx = None
        if r.source_column_header:
            wanted = _normalize_header(r.source_column_header)
            try:
                idx = normalized.index(wanted)
            except ValueError:
                idx = None
        if idx is None and r.column_index_fallback:
            try:
                idx = int(r.column_index_fallback) - 1
            except (TypeError, ValueError):
                idx = None
        result[r.target_field] = {
            "index": idx,
            "transform": r.transform,
            "required": bool(r.is_required),
        }
    return result


def _parse_row(row, col_map):
    result = {}
    for target, spec in col_map.items():
        idx = spec["index"]
        if idx is None or idx >= len(row) or idx < 0:
            result[target] = None
            continue
        raw = row[idx]
        result[target] = transforms.apply(spec["transform"], raw)
    return result


def _persist_results(run, matched, unmatched, unaccounted_emp_ids, indexes):
    # Be defensive — also clear here in case run.save() rehydrated child rows.
    frappe.db.delete("Payroll Import Row", {"parent": run.name})
    frappe.db.delete("Payroll Unmatched Source Row", {"parent": run.name})
    frappe.db.delete("Payroll Unaccounted Employee", {"parent": run.name})

    run.set("parsed_rows", [])
    run.set("unmatched_source_rows", [])
    run.set("unaccounted_employees", [])

    for p in matched:
        row = run.append("parsed_rows", {})
        row.row_number_in_file = p["_row_index"]
        row.employee = p["_match"]["employee"]
        row.match_method = p["_match"]["method"]
        row.match_confidence = p["_match"]["confidence"]
        row.raw_id_value = _trunc(p.get("raw_id_value"), 140)
        row.raw_name = _trunc(p.get("raw_name"), 140)
        row.raw_iban = _trunc(p.get("raw_iban"), 140)
        row.raw_nationality = _trunc(p.get("raw_nationality"), 140)
        for fname in NUMERIC_FIELDS:
            v = p.get(fname)
            if v is not None:
                setattr(row, fname, v)
        row.raw_data_json = json.dumps(
            {k: _json_safe(v) for k, v in p.items() if not k.startswith("_")},
            default=str,
        )
        row.validation_status = "OK"

    for p in unmatched:
        row = run.append("unmatched_source_rows", {})
        row.row_number_in_file = p["_row_index"]
        row.raw_name = _trunc(p.get("raw_name"), 140)
        row.raw_id_value = _trunc(p.get("raw_id_value"), 140)
        row.raw_iban = _trunc(p.get("raw_iban"), 140)
        row.raw_nationality = _trunc(p.get("raw_nationality"), 140)
        row.reason_unmatched = p["_match"].get("reason", "no_match")
        suggestions = p["_match"].get("suggestions") or []
        if suggestions:
            row.suggested_employees = "\n".join(
                f"{s['employee']} - {s['name']} (score: {s['score']})"
                for s in suggestions[:3]
            )
        row.raw_data_json = json.dumps(
            {k: _json_safe(v) for k, v in p.items() if not k.startswith("_")},
            default=str,
        )

    for u in reconciliation.classify_unaccounted(unaccounted_emp_ids, indexes, run):
        row = run.append("unaccounted_employees", {})
        row.employee = u["employee"]
        row.auto_reason = u.get("auto_reason") or ""
        if u.get("auto_category"):
            row.category = u["auto_category"]

    run.rows_matched = len(matched)
    run.rows_unmatched_in_file = len(unmatched)
    run.rows_unaccounted_in_system = len(unaccounted_emp_ids)
    run.rows_with_warnings = 0
    run.rows_with_errors = 0
    run.save(ignore_permissions=True)
    frappe.db.commit()


def _trunc(value, n):
    if value is None:
        return None
    s = str(value)
    return s[:n]


def _json_safe(v):
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    return str(v)
