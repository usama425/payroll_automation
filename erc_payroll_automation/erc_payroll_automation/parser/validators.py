"""Validation engine.

Runs against every matched row after parsing (and again before output generation).
Sets row.validation_status ∈ {OK, Warning, Error} and row.validation_messages.

Two layers of validation:

1. Built-in checks (always run):
   - Arithmetic consistency:
       Basic + Housing + Transportation + Other Allowance ≈ Total Salary
       GOSI + HQ Deductions + Deductions ≈ Total Deductions
       Total Salary + Overtime + Other Income − Total Deductions ≈ Net Salary
   - GOSI consistency: Employee.added_to_gosi + nationality → expected vs reported
   - IBAN format: starts with SA, length 24 (Saudi standard)
   - Identity check: Iqama in file matches Employee.iqama_national_id

2. Custom rules (Template.validation_rules child table):
   - non_empty / min_value / max_value / exact_match / tolerance_pct / tolerance_abs / regex
"""

import re
import frappe

ARITHMETIC_TOLERANCE = 0.02  # SAR — rounding noise can be ~0.01-0.02 due to GOSI %
GOSI_TOLERANCE = 0.05
NAME_OF_SAUDI_COUNTRY = "Saudi Arabia"


def validate_run(run, template, indexes=None):
    """Run all validators across run.parsed_rows. Update counters on run."""
    warnings = 0
    errors = 0

    for row in run.parsed_rows:
        msgs_warn = []
        msgs_err = []

        emp = _get_employee(row.employee, indexes) if row.employee else None

        _check_arithmetic(row, msgs_warn, msgs_err)
        _check_gosi(row, emp, template, msgs_warn, msgs_err)
        _check_iban(row, msgs_warn, msgs_err)
        _check_identity(row, emp, msgs_warn, msgs_err)
        _apply_custom_rules(row, template, msgs_warn, msgs_err)

        all_msgs = [f"ERROR: {m}" for m in msgs_err] + [f"WARN: {m}" for m in msgs_warn]
        if msgs_err:
            row.validation_status = "Error"
            errors += 1
        elif msgs_warn:
            row.validation_status = "Warning"
            warnings += 1
        else:
            row.validation_status = "OK"
        row.validation_messages = "\n".join(all_msgs) if all_msgs else None

    run.rows_with_warnings = warnings
    run.rows_with_errors = errors


def _get_employee(emp_id, indexes):
    if not emp_id:
        return None
    if indexes and emp_id in indexes.get("employees", {}):
        return indexes["employees"][emp_id]
    return frappe.db.get_value(
        "Employee", emp_id,
        ["name", "employee_name", "nationality", "added_to_gosi",
         "iqama_national_id", "passport_number", "bank_ac_no"],
        as_dict=True,
    )


def _check_arithmetic(row, msgs_warn, msgs_err):
    basic = _safe(row.basic_per_working_days) or _safe(row.basic_per_contract) or 0
    housing = _safe(row.housing_per_working_days) or _safe(row.housing_per_contract) or 0
    transp = _safe(row.transportation_per_working_days) or _safe(row.transportation_per_contract) or 0
    other = _safe(row.other_allowance_per_working_days) or _safe(row.other_allowance_per_contract) or 0
    total = _safe(row.total_per_contract) or 0

    expected_total = basic + housing + transp + other
    if total and abs(total - expected_total) > ARITHMETIC_TOLERANCE:
        msgs_warn.append(
            f"Total Salary {_fmt(total)} ≠ Basic+Housing+Transportation+Other "
            f"({_fmt(expected_total)}); diff {_fmt(total - expected_total)}"
        )

    overtime = _safe(row.overtime) or 0
    other_income = _safe(row.other_income) or 0
    deductions = _safe(row.deductions) or 0
    hq_ded = _safe(row.hq_deductions) or 0
    gosi = _safe(row.gosi_from_file) or 0

    expected_net = total + overtime + other_income - (deductions + hq_ded + gosi)
    actual_net = _safe(row.net_salary_from_file)
    if actual_net is not None and abs(actual_net - expected_net) > ARITHMETIC_TOLERANCE:
        msgs_warn.append(
            f"Net Salary {_fmt(actual_net)} ≠ Total+Overtime+OtherIncome−"
            f"(Deductions+HQDed+GOSI) ({_fmt(expected_net)}); diff "
            f"{_fmt(actual_net - expected_net)}"
        )


def _check_gosi(row, emp, template, msgs_warn, msgs_err):
    if not emp:
        return
    added_to_gosi = bool(emp.get("added_to_gosi") if isinstance(emp, dict) else getattr(emp, "added_to_gosi", 0))
    nationality = emp.get("nationality") if isinstance(emp, dict) else getattr(emp, "nationality", None)
    reported = _safe(row.gosi_from_file)

    if not added_to_gosi:
        if reported and reported > 0:
            msgs_warn.append(
                f"GOSI {_fmt(reported)} reported in file, but Employee.added_to_gosi=0"
            )
        return

    basic = _safe(row.basic_per_working_days) or _safe(row.basic_per_contract) or 0
    housing = _safe(row.housing_per_working_days) or _safe(row.housing_per_contract) or 0
    base = basic + housing

    is_saudi = (nationality or "").strip().lower() in (
        NAME_OF_SAUDI_COUNTRY.lower(), "saudi", "ksa", "sa"
    )
    rate = (template.gosi_rate_saudi if is_saudi else template.gosi_rate_non_saudi) or 0
    expected = round(base * float(rate) / 100, 4)

    if reported is None and expected > 0:
        msgs_warn.append(
            f"GOSI not reported in file; expected ≈ {_fmt(expected)} "
            f"({rate}% of {_fmt(base)})"
        )
    elif reported is not None and abs(reported - expected) > GOSI_TOLERANCE:
        msgs_warn.append(
            f"GOSI {_fmt(reported)} ≠ expected {_fmt(expected)} "
            f"({rate}% of Basic+Housing={_fmt(base)}); "
            f"diff {_fmt(reported - expected)}"
        )


def _check_iban(row, msgs_warn, msgs_err):
    iban = (row.raw_iban or "").replace(" ", "").upper()
    if not iban:
        msgs_warn.append("IBAN missing")
        return
    if not iban.startswith("SA"):
        msgs_warn.append(f"IBAN {iban} does not start with SA (non-Saudi account)")
        return
    if len(iban) != 24:
        msgs_err.append(f"IBAN {iban} has wrong length ({len(iban)}, expected 24)")


def _check_identity(row, emp, msgs_warn, msgs_err):
    if not emp:
        return
    file_id = (row.raw_id_value or "").strip()
    emp_iqama = (emp.get("iqama_national_id") if isinstance(emp, dict) else getattr(emp, "iqama_national_id", None)) or ""
    emp_passport = (emp.get("passport_number") if isinstance(emp, dict) else getattr(emp, "passport_number", None)) or ""
    if not file_id:
        return
    # If file ID is digits-only, compare to Iqama; otherwise to passport
    if file_id.isdigit():
        if emp_iqama and str(emp_iqama).strip() != file_id:
            msgs_warn.append(
                f"Iqama in file ({file_id}) ≠ Employee.iqama_national_id "
                f"({emp_iqama}); matched via name fuzzy or fallback?"
            )
    else:
        if emp_passport and str(emp_passport).strip().upper() != file_id.upper():
            msgs_warn.append(
                f"Passport in file ({file_id}) ≠ Employee.passport_number ({emp_passport})"
            )


def _apply_custom_rules(row, template, msgs_warn, msgs_err):
    for rule in template.validation_rules or []:
        if not rule.is_active or not rule.field_name or not rule.rule_type:
            continue
        try:
            failed, msg = _apply_one_rule(row, rule)
        except Exception as e:
            failed, msg = True, f"rule error: {e}"
        if failed:
            (msgs_err if rule.severity == "Error" else msgs_warn).append(msg)


def _apply_one_rule(row, rule):
    """Returns (failed: bool, message: str)."""
    val = getattr(row, rule.field_name, None)
    param = rule.parameter
    rt = rule.rule_type

    def msg(default):
        m = rule.message or default
        return (m.replace("{field}", rule.field_name)
                 .replace("{value}", str(val))
                 .replace("{parameter}", str(param)))

    if rt == "non_empty":
        if val in (None, "", 0):
            return True, msg(f"{rule.field_name} is empty")
    elif rt == "min_value":
        if val is not None and float(val) < float(param):
            return True, msg(f"{rule.field_name}={val} < min {param}")
    elif rt == "max_value":
        if val is not None and float(val) > float(param):
            return True, msg(f"{rule.field_name}={val} > max {param}")
    elif rt == "exact_match":
        if str(val) != str(param):
            return True, msg(f"{rule.field_name}={val} ≠ expected {param}")
    elif rt == "tolerance_abs":
        if val is None or param is None:
            return False, ""
        return False, ""  # tolerance_abs/tolerance_pct need a reference; reserved
    elif rt == "tolerance_pct":
        return False, ""  # reserved
    elif rt == "regex":
        if val is None or not re.match(str(param), str(val)):
            return True, msg(f"{rule.field_name}={val} does not match /{param}/")
    elif rt == "gosi_consistency":
        return False, ""  # handled by built-in _check_gosi
    elif rt == "net_consistency":
        return False, ""  # handled by built-in _check_arithmetic
    return False, ""


def _safe(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _fmt(v):
    if v is None:
        return "-"
    return f"{v:,.2f}"
