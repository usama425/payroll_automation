"""Generator for Payroll No File Run.

Builds the same RSG internal sheet as the file-based flow, but pulls salary
amounts from configurable Employee custom fields instead of a customer Excel.
Used when the customer didn't send a file for a given month.
"""

import frappe
from frappe.utils import now_datetime

from . import internal_sheet
from .file_attach import save_excel_and_link


def generate_no_file_outputs(run_name: str, user: str = None):
    frappe.log_error(
        title=f"Payroll No File generate STARTED: {run_name}",
        message=f"Worker picked up no-file generate job (user={user})",
    )

    effective_user = user or frappe.session.user or "Administrator"
    if effective_user in ("Guest", "", None):
        effective_user = "Administrator"
    frappe.set_user(effective_user)

    run = frappe.get_doc("Payroll No File Run", run_name)
    template = frappe.get_doc("Payroll Import Template", run.template)

    try:
        # Step 1: gather employees scoped by template's project + work location
        employees = _scope_employees(template)
        frappe.log_error(
            title=f"Payroll No File generate: employees fetched {run_name}",
            message=f"count={len(employees)} project={template.project!r} location={template.filter_by_work_location!r}",
        )

        # Step 2: build row-like objects from Employee custom fields
        rows = [_employee_to_row(e, template, run.working_days or 30) for e in employees]

        # Step 3: build the workbook (reuse internal_sheet code paths)
        project_name = internal_sheet._project_display_name(template)
        month_label = internal_sheet._month_label(
            run.payroll_period_end or run.posting_date
        )
        wb = internal_sheet.build_workbook(rows, template, project_name, month_label)

        # Step 4: save + attach
        filename = (
            f"RSG- {project_name} Payroll (No File) - {month_label}.xlsx"
        ).replace("  ", " ")
        file_url = save_excel_and_link(
            wb, run.name, "internal_sheet_file", filename, "No File Internal Sheet",
        )

        # Step 5: stamp + transition
        frappe.db.set_value(
            "Payroll No File Run", run.name,
            {
                "status": "Generated",
                "employees_included": len(rows),
                "generated_at": now_datetime(),
                "generated_by": effective_user,
                "generation_log": "",
            },
            update_modified=False,
        )
        frappe.db.commit()

        frappe.log_error(
            title=f"Payroll No File generate COMPLETE: {run_name}",
            message=f"employees={len(rows)} file_url={file_url!r}",
        )

    except Exception:
        frappe.log_error(
            title=f"Payroll No File generate FAILED: {run_name}",
            message=frappe.get_traceback(),
        )
        try:
            frappe.db.set_value(
                "Payroll No File Run", run.name,
                {
                    "status": "Failed",
                    "generation_log": frappe.get_traceback(),
                },
                update_modified=False,
            )
            frappe.db.commit()
        except Exception:
            pass
        raise


def _scope_employees(template):
    """Mirror the scoping used by the file-based matcher: Active + project + location."""
    filters = {"status": "Active"}
    if template.project:
        filters["project"] = template.project
    if (template.location_strategy in ("Custom Field on Employee", "Mix")
            and template.filter_by_work_location):
        field = template.location_field_on_employee or "custom_location"
        filters[field] = template.filter_by_work_location

    # Fetch a generous field list — we need everything the internal sheet's
    # `_resolve` / `_get_employee` would normally pull from Employee.
    base_fields = [
        "name", "employee_name", "employment_type", "date_of_joining",
        "nationality", "added_to_gosi", "bank_name", "bank_ac_no",
        "iqama_national_id", "passport_number", "status",
    ]
    custom_fields = [f for f in (
        template.no_file_emp_field_basic,
        template.no_file_emp_field_housing,
        template.no_file_emp_field_transportation,
        template.no_file_emp_field_other_allowance,
    ) if f]

    # Some custom fields might not exist on this site — fetch defensively.
    safe_fields = base_fields[:]
    for cf in custom_fields:
        if _field_exists("Employee", cf):
            safe_fields.append(cf)
        else:
            frappe.log_error(
                title="Payroll No File: custom field missing",
                message=f"Employee.{cf!r} not found — that component will be 0.",
            )

    return frappe.get_all("Employee", filters=filters, fields=safe_fields)


def _field_exists(doctype, fieldname):
    try:
        meta = frappe.get_meta(doctype)
        return bool(meta.get_field(fieldname))
    except Exception:
        return False


class _NoFileRow:
    """Row-like object compatible with internal_sheet.build_workbook."""

    def __init__(self, emp, template, working_days):
        self.employee = emp.name
        self.validation_status = "OK"
        self.validation_messages = None

        basic = _read(emp, template.no_file_emp_field_basic)
        housing = _read(emp, template.no_file_emp_field_housing)
        transp = _read(emp, template.no_file_emp_field_transportation)
        other = _read(emp, template.no_file_emp_field_other_allowance)
        total = basic + housing + transp + other

        # Customer-file columns — set both per_contract and per_working_days
        # to the same value (since there's no separate file to split them).
        self.basic_per_contract = basic
        self.housing_per_contract = housing
        self.transportation_per_contract = transp
        self.other_allowance_per_contract = other
        self.total_per_contract = total

        self.working_days = working_days
        self.basic_per_working_days = basic
        self.housing_per_working_days = housing
        self.transportation_per_working_days = transp
        self.other_allowance_per_working_days = other

        # No-file scenario has no overtime / deductions / GOSI from a customer
        self.overtime = 0
        self.other_income = 0
        self.deductions = 0
        self.hq_deductions = 0
        self.gosi_from_file = None
        self.net_salary_from_file = total  # placeholder; output computes Net itself

        # Raw fields used by internal_sheet
        self.raw_id_value = emp.get("iqama_national_id") or emp.get("passport_number") or ""
        self.raw_name = emp.get("employee_name") or ""
        self.raw_iban = emp.get("bank_ac_no") or ""
        self.raw_nationality = emp.get("nationality") or ""


def _employee_to_row(emp, template, working_days):
    return _NoFileRow(emp, template, working_days)


def _read(emp, fieldname):
    if not fieldname:
        return 0.0
    v = emp.get(fieldname)
    if v in (None, ""):
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0
