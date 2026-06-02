"""Delta-mode parser.

For projects whose customer file carries only *deltas* (variable earnings /
deductions) rather than the full salary breakdown — currently Datavolt and
Airproducts. The base salary for EVERY active project employee comes from
system Employee data (same fields the No-File Run uses); the file only adjusts
overtime / other income / deductions for the few employees it lists.

Output is a normal set of Payroll Import Row records, so the existing
Generate Outputs step renders the standard Elite internal sheet unchanged.

Mapping (confirmed with the client):
    Datavolt   — match by Employee ID (fallback employee_id_from_client)
                 'Variable Earnings'.Amount   -> Other Income
                 'Variable Deductions'.Amount -> Deductions (abs)
    Airproducts — match by IQAMA (iqama_national_id)
                 'Overtime Amount'            -> Overtime
                 other allowance columns sum  -> Other Income
                 'DEDUCTION AMOUNT'           -> Deductions (abs)
"""

import json

import frappe
from frappe import _
from frappe.utils import getdate
from openpyxl import load_workbook

from ..parser_constants import NUMERIC_FIELDS
from .file_parser import _resolve_attached_file_path, _trunc, _json_safe


DELTA_DATAVOLT = "Deltas (Datavolt)"
DELTA_AIRPRODUCTS = "Deltas (Airproducts)"

EMP_FIELDS = [
    "name", "employee_name", "employment_type", "date_of_joining",
    "nationality", "added_to_gosi", "bank_name", "bank_ac_no",
    "iqama_national_id", "passport_number", "employee_id_from_client", "status",
]


def is_delta_mode(template):
    mode = (getattr(template, "input_mode", None) or "Full from File")
    return mode.startswith("Deltas")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_delta_parse(run, template):
    """Build one Payroll Import Row per active project employee, base from
    system, deltas overlaid from the file. Sets status -> Reconciliation Pending."""
    employees = _scope_employees(template)
    if not employees:
        raise ValueError(_(
            "No active employees found for project {0}. Delta mode needs the "
            "project's employees to exist in the system."
        ).format(template.project))

    deltas, unmatched = _read_deltas(run, template, employees)

    period_days = _period_days(run)
    rows = []
    for emp in employees:
        base = _base_amounts(emp, template)
        d = deltas.get(emp.name) or _zero()
        rows.append(_build_row(emp, base, d, period_days))

    _persist(run, rows, unmatched)

    frappe.log_error(
        title=f"Payroll Import delta parse COMPLETE {run.name}",
        message=(
            f"mode={template.input_mode} employees={len(rows)} "
            f"with_deltas={len(deltas)} unmatched_file_rows={len(unmatched)}"
        ),
    )


# ---------------------------------------------------------------------------
# Employee scope + base salary
# ---------------------------------------------------------------------------

def _scope_employees(template):
    filters = {"status": "Active"}
    if template.project:
        filters["project"] = template.project
    if (template.location_strategy in ("Custom Field on Employee", "Mix")
            and template.filter_by_work_location):
        field = template.location_field_on_employee or "custom_location"
        filters[field] = template.filter_by_work_location

    fields = EMP_FIELDS[:]
    for cf in _base_fieldnames(template):
        if cf not in fields and _field_exists("Employee", cf):
            fields.append(cf)

    return frappe.get_all("Employee", filters=filters, fields=fields)


def _base_fieldnames(template):
    return [
        template.no_file_emp_field_basic or "basic_salary",
        template.no_file_emp_field_housing or "housing_allowance",
        template.no_file_emp_field_transportation or "transport_allowance",
        template.no_file_emp_field_other_allowance or "food_allowance",
    ]


def _base_amounts(emp, template):
    fb, fh, ft, fo = _base_fieldnames(template)
    return {
        "basic": _to_float(emp.get(fb)),
        "housing": _to_float(emp.get(fh)),
        "transport": _to_float(emp.get(ft)),
        "other": _to_float(emp.get(fo)),
    }


def _field_exists(doctype, fieldname):
    try:
        return bool(frappe.get_meta(doctype).get_field(fieldname))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Read deltas from the file
# ---------------------------------------------------------------------------

def _zero():
    return {"overtime": 0.0, "other_income": 0.0, "deductions": 0.0}


def _read_deltas(run, template, employees):
    """Return (deltas_by_employee, unmatched_file_rows).

    deltas_by_employee: {employee_name: {overtime, other_income, deductions}}
    unmatched_file_rows: [{"sheet":.., "raw_key":.., "amount":..}]
    """
    by_name = {str(e.name).strip(): e.name for e in employees}
    by_client = {
        str(e.employee_id_from_client).strip(): e.name
        for e in employees if e.get("employee_id_from_client")
    }
    by_iqama = {
        str(e.iqama_national_id).strip(): e.name
        for e in employees if e.get("iqama_national_id")
    }

    path = _resolve_attached_file_path(run.source_file)
    wb = load_workbook(path, data_only=True, read_only=True)
    try:
        if template.input_mode == DELTA_DATAVOLT:
            return _read_datavolt(wb, by_name, by_client)
        if template.input_mode == DELTA_AIRPRODUCTS:
            return _read_airproducts(wb, by_iqama)
        raise ValueError(f"Unsupported delta input_mode: {template.input_mode!r}")
    finally:
        wb.close()


def _read_datavolt(wb, by_name, by_client):
    deltas, unmatched = {}, []

    for raw_key, amount in _datavolt_sheet(wb, "Variable Earnings"):
        emp = _resolve_code(raw_key, by_name, by_client)
        if not emp:
            unmatched.append({"sheet": "Variable Earnings",
                              "raw_key": raw_key, "amount": amount})
            continue
        deltas.setdefault(emp, _zero())["other_income"] += amount

    for raw_key, amount in _datavolt_sheet(wb, "Variable Deductions"):
        emp = _resolve_code(raw_key, by_name, by_client)
        if not emp:
            unmatched.append({"sheet": "Variable Deductions",
                              "raw_key": raw_key, "amount": amount})
            continue
        deltas.setdefault(emp, _zero())["deductions"] += abs(amount)

    return deltas, unmatched


def _datavolt_sheet(wb, sheet_name):
    """Yield (raw_employee_code, amount) for a Datavolt delta sheet.

    Header row is the first row (within the top 8) containing 'employee code'.
    """
    if sheet_name not in wb.sheetnames:
        return []
    rows = list(wb[sheet_name].iter_rows(values_only=True))
    hidx, norm = _find_header(rows, ["employee code"])
    if hidx is None:
        return []
    key_c = _col(norm, "employee code")
    amt_c = _col(norm, "amount")
    out = []
    for r in rows[hidx + 1:]:
        if key_c is None or key_c >= len(r):
            continue
        raw_key = r[key_c]
        if raw_key in (None, ""):
            continue
        amount = _to_float(_cell(r, amt_c))
        out.append((raw_key, amount))
    return out


def _read_airproducts(wb, by_iqama):
    deltas, unmatched = {}, []
    sheet = _airproducts_sheet(wb)
    if not sheet:
        return deltas, unmatched
    ws_rows, hidx, norm = sheet

    iq_c = _col(norm, "iqama")
    ot_c = _col(norm, "overtime amount", "overtime")
    ded_c = _col(norm, "deduction amount", "deduction")
    add_cs = [
        _col(norm, h) for h in
        ("other allowance", "utility allowance", "trip allowance",
         "ticket allowance", "phone allowance")
    ]

    for r in ws_rows[hidx + 1:]:
        if iq_c is None or iq_c >= len(r):
            continue
        raw_iq = r[iq_c]
        if raw_iq in (None, ""):
            continue
        overtime = _to_float(_cell(r, ot_c))
        other = sum(_to_float(_cell(r, c)) for c in add_cs if c is not None)
        deductions = abs(_to_float(_cell(r, ded_c)))
        emp = by_iqama.get(_norm_id(raw_iq))
        if not emp:
            unmatched.append({"sheet": "Allowances",
                              "raw_key": raw_iq,
                              "amount": overtime + other - deductions})
            continue
        d = deltas.setdefault(emp, _zero())
        d["overtime"] += overtime
        d["other_income"] += other
        d["deductions"] += deductions

    return deltas, unmatched


def _airproducts_sheet(wb):
    """Find the data sheet (header containing 'IQAMA'). Returns (rows, hidx, norm)."""
    for sn in wb.sheetnames:
        rows = list(wb[sn].iter_rows(values_only=True))
        hidx, norm = _find_header(rows, ["iqama"])
        if hidx is not None:
            return rows, hidx, norm
    return None


# ---------------------------------------------------------------------------
# Header / cell helpers
# ---------------------------------------------------------------------------

def _norm(v):
    return " ".join(str(v).strip().lower().split()) if v is not None else ""


def _find_header(rows, must_have, max_scan=8):
    wanted = [_norm(m) for m in must_have]
    for i, r in enumerate(rows[:max_scan]):
        norm = [_norm(c) for c in r]
        if all(any(w == h or w in h for h in norm) for w in wanted):
            return i, norm
    return None, None


def _col(norm_headers, *candidates):
    for cand in candidates:
        c = _norm(cand)
        for idx, h in enumerate(norm_headers):
            if h == c:
                return idx
    for cand in candidates:
        c = _norm(cand)
        for idx, h in enumerate(norm_headers):
            if c and c in h:
                return idx
    return None


def _cell(row, idx):
    if idx is None or idx >= len(row):
        return None
    return row[idx]


def _resolve_code(raw_key, by_name, by_client):
    k = _norm_id(raw_key)
    return by_name.get(k) or by_client.get(k)


def _norm_id(value):
    """Normalize a numeric-ish code: 10019 / 10019.0 / '10019 ' -> '10019'."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, int):
        return str(value)
    return str(value).strip()


def _to_float(value):
    if value in (None, ""):
        return 0.0
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _period_days(run):
    try:
        d = (getdate(run.payroll_period_end) - getdate(run.payroll_period_start)).days + 1
        return d if d > 0 else 30
    except Exception:
        return 30


# ---------------------------------------------------------------------------
# Build + persist rows
# ---------------------------------------------------------------------------

def _build_row(emp, base, d, period_days):
    total = base["basic"] + base["housing"] + base["transport"] + base["other"]
    net = total + d["overtime"] + d["other_income"] - d["deductions"]
    return {
        "employee": emp.name,
        "match_method": "system_base",
        "match_confidence": 1.0,
        "raw_id_value": emp.get("iqama_national_id") or emp.get("passport_number") or "",
        "raw_name": emp.get("employee_name") or "",
        "raw_iban": emp.get("bank_ac_no") or "",
        "raw_nationality": emp.get("nationality") or "",
        "basic_per_contract": base["basic"],
        "housing_per_contract": base["housing"],
        "transportation_per_contract": base["transport"],
        "other_allowance_per_contract": base["other"],
        "total_per_contract": total,
        "working_days": period_days,
        "basic_per_working_days": base["basic"],
        "housing_per_working_days": base["housing"],
        "transportation_per_working_days": base["transport"],
        "other_allowance_per_working_days": base["other"],
        "overtime": d["overtime"],
        "other_income": d["other_income"],
        "deductions": d["deductions"],
        "hq_deductions": 0.0,
        "gosi_from_file": None,
        "net_salary_from_file": net,
    }


def _persist(run, rows, unmatched):
    frappe.db.delete("Payroll Import Row", {"parent": run.name})
    frappe.db.delete("Payroll Unmatched Source Row", {"parent": run.name})
    frappe.db.delete("Payroll Unaccounted Employee", {"parent": run.name})

    for i, p in enumerate(rows, start=1):
        doc = frappe.new_doc("Payroll Import Row")
        doc.parent = run.name
        doc.parenttype = "Payroll Import Run"
        doc.parentfield = "parsed_rows"
        doc.idx = i
        doc.row_number_in_file = 0
        doc.employee = p["employee"]
        doc.match_method = p["match_method"]
        doc.match_confidence = p["match_confidence"]
        doc.raw_id_value = _trunc(p.get("raw_id_value"), 140)
        doc.raw_name = _trunc(p.get("raw_name"), 140)
        doc.raw_iban = _trunc(p.get("raw_iban"), 140)
        doc.raw_nationality = _trunc(p.get("raw_nationality"), 140)
        for fname in NUMERIC_FIELDS:
            v = p.get(fname)
            if v is not None:
                setattr(doc, fname, v)
        doc.raw_data_json = json.dumps(
            {k: _json_safe(v) for k, v in p.items()}, default=str)
        doc.validation_status = "OK"
        doc.validation_messages = ""
        doc.flags.ignore_permissions = True
        doc.flags.ignore_mandatory = True
        doc.db_insert()

    for i, u in enumerate(unmatched, start=1):
        doc = frappe.new_doc("Payroll Unmatched Source Row")
        doc.parent = run.name
        doc.parenttype = "Payroll Import Run"
        doc.parentfield = "unmatched_source_rows"
        doc.idx = i
        doc.row_number_in_file = 0
        doc.raw_id_value = _trunc(str(u.get("raw_key")), 140)
        doc.raw_name = ""
        doc.reason_unmatched = f"code/iqama not found in system ({u.get('sheet')})"
        doc.raw_data_json = json.dumps(
            {k: _json_safe(v) for k, v in u.items()}, default=str)
        doc.flags.ignore_permissions = True
        doc.flags.ignore_mandatory = True
        doc.db_insert()

    total = len(rows)
    frappe.db.set_value("Payroll Import Run", run.name, {
        "rows_matched": total,
        "rows_unmatched_in_file": len(unmatched),
        "rows_unaccounted_in_system": 0,
        "rows_with_warnings": 0,
        "rows_with_errors": 0,
        "validation_pass_rate": 100.0 if total else 0.0,
        "status": "Reconciliation Pending",
        "parse_error_log": "",
    }, update_modified=False)
    frappe.db.commit()
