"""Build the ERC internal Excel ("RSG-<Project> Payroll- <Month> <Year>.xlsx").

Layout (matches historical files like RSG- Misk School Payroll- February 2026.xlsx):
    Row 1: "Elite - RSP- <Project Name> Payroll" banner
    Row 2: "For the month of <Month> <Year>" banner
    Row 3: blank
    Row 4: column headers (33 columns)
    Row 5+: one row per matched employee, cells highlighted by validation status
"""

from datetime import datetime

import frappe
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill, Border, Side

from ..parser.bank_codes import bank_display_for
from .file_attach import save_excel_and_link


# Column spec — (header, fieldname_on_payroll_import_row, kind)
# Kind: "data" reads from row, "emp" reads from Employee, "computed" calls function below.
COLUMNS = [
    ("S/N",                  None,                            "computed:serial"),
    ("ERP ID No.",           "employee",                      "data"),
    ("Name",                 None,                            "emp:employee_name"),
    ("Contract",             None,                            "emp:employment_type"),
    ("Company",              None,                            "computed:company_label"),
    ("Nationality",          "raw_nationality",               "data"),
    ("ID/Iqama/ passprot",   "raw_id_value",                  "data"),
    ("Bank Name",            None,                            "computed:bank_name"),
    ("IBAN/ Account number", "raw_iban",                      "data"),
    ("Basic per contract",   "basic_per_contract",            "data"),
    ("Working Days",         "working_days",                  "data"),
    ("Baisc",                "basic_per_working_days",        "data"),
    ("Housing",              "housing_per_working_days",      "data"),
    ("Transportaion",        "transportation_per_working_days","data"),
    ("Other Allowance",      "other_allowance_per_working_days","data"),
    ("Total Salary",         "total_per_contract",            "data"),
    ("Overtime",             "overtime",                      "data"),
    ("Other Income",         "other_income",                  "data"),
    ("GOSI Contribution",    "gosi_from_file",                "data"),
    ("HQ Ded",               "hq_deductions",                 "data"),
    ("Deductions",           "deductions",                    "data"),
    ("Total Deductions",     None,                            "computed:total_deductions"),
    ("Net Salary",           "net_salary_from_file",          "data"),
    ("Charge Base",          None,                            "computed:charge_base"),
    ("Hiring Date",          None,                            "emp:date_of_joining"),
    (None,                   None,                            "blank"),
    # Right-side billing block
    ("Total Salary",         "total_per_contract",            "data"),
    ("Gosi",                 None,                            "computed:gosi_billing"),
    ("ERC Fee",              None,                            "constant:erc_fee"),
    ("Bank Charges",         None,                            "constant:bank_charges"),
    ("Total",                None,                            "computed:billing_total"),
]


# Constants — until Template gets these fields wired (next chunk)
DEFAULT_ERC_FEE = 600
DEFAULT_BANK_CHARGES = 5
DEFAULT_GOSI_BILLING_RATE_SAUDI = 11.75      # employer share for Saudis
DEFAULT_GOSI_BILLING_RATE_NON_SAUDI = 2.0    # occupational hazard for non-Saudis

FILL_ERROR = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
FILL_WARNING = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
FILL_HEADER = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
THIN = Side(border_style="thin", color="999999")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def generate(run, template) -> str:
    """Build the internal sheet, attach to the Run as `internal_sheet_file`. Returns file URL."""
    project_name = _project_display_name(template)
    month_label = _month_label(run.payroll_period_end or run.posting_date)

    wb = Workbook()
    ws = wb.active
    ws.title = f"RSG- {project_name.upper()}"[:31]  # Excel sheet name max 31 chars

    # Row 1-2 banners
    ws.cell(row=1, column=1, value=f"Elite - RSP- {project_name} Payroll").font = Font(bold=True, size=12)
    ws.cell(row=2, column=1, value=f"For the month of {month_label}").font = Font(italic=True)
    # Row 3 blank

    # Row 4 headers
    for col_idx, (header, _, _) in enumerate(COLUMNS, start=1):
        if header is None:
            continue
        c = ws.cell(row=4, column=col_idx, value=header)
        c.font = Font(bold=True)
        c.fill = FILL_HEADER
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = BORDER

    # Data rows
    emp_cache = {}
    for serial, row in enumerate(run.parsed_rows, start=1):
        emp = _get_employee(row.employee, emp_cache)
        excel_row = 4 + serial
        for col_idx, (_, field, kind) in enumerate(COLUMNS, start=1):
            value = _resolve(row, emp, template, kind, field, serial)
            if value is None:
                continue
            cell = ws.cell(row=excel_row, column=col_idx, value=value)
            if isinstance(value, float):
                cell.number_format = "#,##0.00"
            cell.border = BORDER

        # Whole-row highlight by validation status (overrides nothing data-wise)
        fill = None
        if row.validation_status == "Error":
            fill = FILL_ERROR
        elif row.validation_status == "Warning":
            fill = FILL_WARNING
        if fill:
            for col_idx in range(1, len(COLUMNS) + 1):
                ws.cell(row=excel_row, column=col_idx).fill = fill
            # Add validation message as a comment on the first cell
            if row.validation_messages:
                from openpyxl.comments import Comment
                ws.cell(row=excel_row, column=1).comment = Comment(
                    row.validation_messages[:2000], "ERC Payroll Automation"
                )

    # Column widths — sensible defaults
    for col_idx, (header, _, _) in enumerate(COLUMNS, start=1):
        if header and len(header) > 10:
            ws.column_dimensions[ws.cell(row=4, column=col_idx).column_letter].width = max(15, len(header) + 2)
        else:
            ws.column_dimensions[ws.cell(row=4, column=col_idx).column_letter].width = 14

    filename = f"RSG- {project_name} Payroll - {month_label}.xlsx".replace("  ", " ")
    return save_excel_and_link(
        wb, run.name, "internal_sheet_file", filename, "Internal Sheet",
    )


def _resolve(row, emp, template, kind, field, serial):
    if kind == "blank":
        return None
    if kind == "data":
        return getattr(row, field, None)
    if kind.startswith("emp:"):
        fname = kind.split(":", 1)[1]
        return (emp or {}).get(fname) if isinstance(emp, dict) else getattr(emp, fname, None)
    if kind == "computed:serial":
        return serial
    if kind == "computed:company_label":
        return _project_display_name(template)
    if kind == "computed:bank_name":
        emp_bank = (emp or {}).get("bank_name") if isinstance(emp, dict) else getattr(emp, "bank_name", None)
        return bank_display_for(emp_bank, row.raw_iban) or ""
    if kind == "computed:total_deductions":
        g = _f(row.gosi_from_file); h = _f(row.hq_deductions); d = _f(row.deductions)
        return round(g + h + d, 2)
    if kind == "computed:charge_base":
        return _f(row.total_per_contract)
    if kind == "computed:gosi_billing":
        # employer-side GOSI for the billing block
        nationality = (emp or {}).get("nationality") if isinstance(emp, dict) else getattr(emp, "nationality", None)
        is_saudi = (nationality or "").strip().lower() in ("saudi arabia", "saudi", "ksa", "sa")
        rate = DEFAULT_GOSI_BILLING_RATE_SAUDI if is_saudi else DEFAULT_GOSI_BILLING_RATE_NON_SAUDI
        base = _f(row.basic_per_working_days) + _f(row.housing_per_working_days)
        return round(base * rate / 100, 2)
    if kind == "constant:erc_fee":
        return DEFAULT_ERC_FEE
    if kind == "constant:bank_charges":
        return DEFAULT_BANK_CHARGES
    if kind == "computed:billing_total":
        total = _f(row.total_per_contract)
        nationality = (emp or {}).get("nationality") if isinstance(emp, dict) else getattr(emp, "nationality", None)
        is_saudi = (nationality or "").strip().lower() in ("saudi arabia", "saudi", "ksa", "sa")
        rate = DEFAULT_GOSI_BILLING_RATE_SAUDI if is_saudi else DEFAULT_GOSI_BILLING_RATE_NON_SAUDI
        base = _f(row.basic_per_working_days) + _f(row.housing_per_working_days)
        gosi = round(base * rate / 100, 2)
        return round(total + gosi + DEFAULT_ERC_FEE + DEFAULT_BANK_CHARGES, 2)
    return None


def _f(v):
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _get_employee(emp_id, cache):
    if not emp_id:
        return None
    if emp_id in cache:
        return cache[emp_id]
    e = frappe.db.get_value(
        "Employee", emp_id,
        ["employee_name", "employment_type", "date_of_joining",
         "nationality", "bank_name", "bank_ac_no"],
        as_dict=True,
    )
    cache[emp_id] = e
    return e


def _project_display_name(template):
    """Use Project.project_name if available, else template_name."""
    if template.project:
        pn = frappe.db.get_value("Project", template.project, "project_name")
        if pn:
            return pn
    return template.template_name or "Project"


def _month_label(date_value):
    if not date_value:
        date_value = datetime.now()
    if isinstance(date_value, str):
        try:
            date_value = datetime.fromisoformat(date_value)
        except ValueError:
            return date_value
    return date_value.strftime("%B %Y")


