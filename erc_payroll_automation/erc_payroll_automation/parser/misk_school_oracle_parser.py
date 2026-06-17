"""Misk School Oracle parser — reads Misk's Oracle Fusion "RSMC Payroll Register
Report" export and turns it into the standard Payroll Import Rows so the
existing Generate Outputs step renders the normal Elite internal sheet.

Why this needs a dedicated reader (the generic column-map can't do it):

  * The sheet stacks TWO tables. Section 1 ("1-Earning / 2-Deduction / Net") is
    the per-employee payroll register; below a gap, Section 2 ("RSMC Information
    Elements": PERSON_NUMBER / TOTAL_SALARY / GOSI / ERC_FEE / BANK / TOTAL) is
    Misk's own billing summary in DIFFERENT columns. We read Section 1 only and
    stop at its "Totals" row, so Section 2 never double-counts employees.
  * Employee GOSI is split across two columns (Employee GOSI Annuities +
    Employee GOSI Saned) and the deduction total across four (Absence, Housing
    Allowance Deduction, Late Minutes, Unpaid Leave). The generic map is 1
    source column -> 1 field; it can't sum. We sum them here.
  * The identity columns (person number, name, nationality, national id, IBAN)
    have NO header text in Section 1 (the header is the merged "1-Earning"
    block), so they're located by position + content, not by header label.

Field mapping (Section 1 -> Payroll Import Row), validated against the file's
own Section 2 billing block:

    NATIONAL_IDENTIFIER_NUMBER  -> raw_id_value      (Iqama; the match key)
    FULL_NAME                   -> raw_name
    NATIONALITY                 -> raw_nationality
    IBAN                        -> raw_iban
    Basic Salary                -> basic_per_contract / basic_per_working_days
    Housing Allowance           -> housing_per_contract / housing_per_working_days
    Transportation Allowance    -> transportation_per_contract / _working_days
    Total Other Allowances      -> other_allowance_per_contract / _working_days
    Others Earnings             -> other_income
    Overtime                    -> overtime
    GOSI Annuities + GOSI Saned -> gosi_from_file            (abs, employee GOSI)
    Absence + Housing Ded +
      Late Minutes + Unpaid Lv  -> deductions                (abs)
    Net                         -> net_salary_from_file
    (basic+housing+transp+other)-> total_per_contract

The file carries no "working days" column (Oracle already prorated the amounts,
booking absence/unpaid-leave as separate deduction lines while Basic/Housing
stay at full contract value), so working_days is set to the period length and
the per-contract / per-working-day fields are the same file value. Net is taken
straight from the file's Net column — the Elite sheet treats it as the source
of truth, exactly like the legacy Misk Elite format.
"""

import frappe
from frappe import _
from frappe.utils import getdate

from .file_parser import (
    _open_workbook,
    _resolve_attached_file_path,
    _resolve_sheet,
    match_validate_persist,
)


MISK_SCHOOL_ORACLE = "Misk School Oracle"

# Identity columns are unlabelled in Section 1 — anchor by position, with a
# content fallback for the two unambiguous ones (IBAN starts "SA"; the national
# id is a long pure-digit run). 0-based indexes matching the Oracle layout.
PERSON_COL = 0       # A  PERSON_NUMBER (Oracle person number, ~5 digits)
NAME_COL = 1         # B  FULL_NAME
NATIONALITY_COL = 3  # D  NATIONALITY
NID_COL_FALLBACK = 4   # E  NATIONAL_IDENTIFIER_NUMBER (Iqama)
IBAN_COL_FALLBACK = 6  # G  IBAN


def is_misk_school_oracle_mode(template):
    return (getattr(template, "input_mode", None) or "Full from File") == MISK_SCHOOL_ORACLE


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_misk_school_oracle_parse(run, template):
    """Read Section 1 of the RSMC report into parsed rows, then hand off to the
    shared match/validate/persist tail. Sets status -> Reconciliation Pending."""
    path = _resolve_attached_file_path(run.source_file)
    wb = _open_workbook(path)
    try:
        sheet_name = _resolve_sheet(wb, template.sheet_name_override)
        rows = list(wb[sheet_name].iter_rows(values_only=True))
    finally:
        try:
            wb.close()
        except Exception:
            pass

    run.db_set("source_file_sheet_used", sheet_name)

    parsed_rows = _read_register(rows, _period_days(run))
    if not parsed_rows:
        raise ValueError(_(
            "No employee rows found in the Misk Oracle register. Expected a "
            "'Basic Salary' header row followed by employee rows ending at "
            "'Totals' (sheet '{0}')."
        ).format(sheet_name))

    run.db_set("source_file_rows_total", len(parsed_rows))
    frappe.log_error(
        title=f"Payroll Import Misk-School-Oracle read {run.name}",
        message=f"Read {len(parsed_rows)} register rows from sheet '{sheet_name}'.",
    )

    match_validate_persist(run, template, parsed_rows)


# ---------------------------------------------------------------------------
# Section-1 register reader
# ---------------------------------------------------------------------------

def _read_register(rows, period_days):
    """Parse the Section-1 earnings/deduction register into row dicts."""
    hdr_idx = _find_header_row(rows)
    if hdr_idx is None:
        return []
    headers = [_norm(c) for c in rows[hdr_idx]]

    cols = {
        "basic": _col(headers, exact="basic salary"),
        "housing": _col(headers, exact="housing allowance"),
        "transport": _col(headers, exact="transportation allowance",
                          contains=("transportation",)),
        "other_allow": _col(headers, exact="total other allowances",
                            contains=("other allowance",)),
        "others_earn": _col(headers, exact="others earnings",
                            contains=("others earning",)),
        "overtime": _col(headers, contains=("overtime",)),
        "absence": _col(headers, contains=("absence",)),
        "housing_ded": _col(headers, contains=("housing", "deduction")),
        "gosi_ann": _col(headers, contains=("annuit",)),
        "gosi_san": _col(headers, contains=("saned",)),
        "late": _col(headers, contains=("late",)),
        "unpaid": _col(headers, contains=("unpaid",)),
        # "Net" sits in the row ABOVE the earning/deduction labels (merged block)
        "net": _col_in_rows(rows, hdr_idx, exact="net"),
    }
    nid_idx, iban_idx = _detect_identity(rows, hdr_idx)

    out = []
    for excel_row, raw in enumerate(rows[hdr_idx + 1:], start=hdr_idx + 2):
        person = _str(_at(raw, PERSON_COL))
        if person.lower() in ("totals", "total"):
            break  # end of Section 1 — never fall through into Section 2
        if all(c in (None, "") for c in raw):
            break  # blank gap before Section 2
        nid = _at(raw, nid_idx)
        if nid in (None, ""):
            continue  # title / signature / stray row

        basic = _f(_at(raw, cols["basic"]))
        housing = _f(_at(raw, cols["housing"]))
        transport = _f(_at(raw, cols["transport"]))
        other_allow = _f(_at(raw, cols["other_allow"]))
        gosi = abs(_f(_at(raw, cols["gosi_ann"]))) + abs(_f(_at(raw, cols["gosi_san"])))
        deductions = (
            abs(_f(_at(raw, cols["absence"])))
            + abs(_f(_at(raw, cols["housing_ded"])))
            + abs(_f(_at(raw, cols["late"])))
            + abs(_f(_at(raw, cols["unpaid"])))
        )
        total_contract = basic + housing + transport + other_allow

        out.append({
            "_row_index": excel_row,
            "raw_id_value": _norm_id(nid),
            "raw_name": _str(_at(raw, NAME_COL)),
            "raw_iban": _str(_at(raw, iban_idx)),
            "raw_nationality": _str(_at(raw, NATIONALITY_COL)),
            "basic_per_contract": basic,
            "housing_per_contract": housing,
            "transportation_per_contract": transport,
            "other_allowance_per_contract": other_allow,
            "total_per_contract": total_contract,
            "working_days": period_days,
            "basic_per_working_days": basic,
            "housing_per_working_days": housing,
            "transportation_per_working_days": transport,
            "other_allowance_per_working_days": other_allow,
            "overtime": _f(_at(raw, cols["overtime"])),
            "other_income": _f(_at(raw, cols["others_earn"])),
            "deductions": deductions,
            "hq_deductions": 0.0,
            "gosi_from_file": gosi,
            "net_salary_from_file": _f(_at(raw, cols["net"])),
        })
    return out


def _find_header_row(rows, max_scan=12):
    """Section-1 label row = the first row carrying a 'Basic Salary' cell."""
    for i, r in enumerate(rows[:max_scan]):
        if any(_norm(c) == "basic salary" for c in r):
            return i
    return None


def _detect_identity(rows, hdr_idx, scan=8):
    """Locate the national-id and IBAN columns by content (their headers are
    blank in Section 1). Falls back to the fixed Oracle positions."""
    nid_idx = iban_idx = None
    for raw in rows[hdr_idx + 1: hdr_idx + 1 + scan]:
        for ci, val in enumerate(raw):
            s = _str(val).replace(" ", "")
            if not s:
                continue
            if iban_idx is None and s[:2].upper() == "SA" and s[2:].isalnum() \
                    and 18 <= len(s) <= 30:
                iban_idx = ci
            if nid_idx is None and ci != PERSON_COL and s.isdigit() \
                    and 9 <= len(s) <= 12:
                nid_idx = ci
        if nid_idx is not None and iban_idx is not None:
            break
    return (nid_idx if nid_idx is not None else NID_COL_FALLBACK,
            iban_idx if iban_idx is not None else IBAN_COL_FALLBACK)


# ---------------------------------------------------------------------------
# Header / cell helpers
# ---------------------------------------------------------------------------

def _col(headers, exact=None, contains=()):
    """Index of the header matching ``exact`` (preferred) else containing all
    terms in ``contains``. None if not found."""
    if exact:
        for i, h in enumerate(headers):
            if h == exact:
                return i
    for i, h in enumerate(headers):
        if contains and all(t in h for t in contains):
            return i
    return None


def _col_in_rows(rows, hdr_idx, exact):
    """Find a header label (e.g. 'Net') that may live in a row above the main
    label row because of the merged earning/deduction header block."""
    for r in range(max(0, hdr_idx - 2), hdr_idx + 1):
        for i, c in enumerate(rows[r]):
            if _norm(c) == exact:
                return i
    return None


def _at(row, idx):
    if idx is None or idx >= len(row) or idx < 0:
        return None
    return row[idx]


def _norm(v):
    return " ".join(str(v).strip().lower().split()) if v is not None else ""


def _str(v):
    return "" if v is None else str(v).strip()


def _norm_id(value):
    """Numeric-ish id -> clean string: 2154513648.0 / ' 2154513648 ' -> '2154513648'."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, int):
        return str(value)
    return str(value).strip()


def _f(value):
    if value in (None, "") or isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip().replace(",", ""))
    except ValueError:
        return 0.0


def _period_days(run):
    try:
        d = (getdate(run.payroll_period_end) - getdate(run.payroll_period_start)).days + 1
        return d if d > 0 else 30
    except Exception:
        return 30
