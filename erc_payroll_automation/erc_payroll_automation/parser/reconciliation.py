"""Three-way reconciliation + auto-categorization of unaccounted employees.

Logic:
    A = expected_employees(template) — Active employees scoped by project + work_location
    B = set of employees matched in this run
    Unaccounted = A − B  (employee exists in system, but not in customer's file)

For each Unaccounted, we look up their current state and propose a category:
    - Already Inactive: status != Active OR relieving_date < period_start
    - Left Mid-Month (EOS Required): relieving_date within [period_start, period_end]
    - On Unpaid Leave / On Suspended Leave: approved Leave Application overlapping period
    - (blank): no rule matched → the user must categorize manually
"""

import frappe
from frappe.utils import getdate


def expected_employees(template):
    """Return set of Employee names expected in this template's file."""
    filters = {"status": "Active"}
    if template.project:
        filters["project"] = template.project
    if template.location_strategy in ("Custom Field on Employee", "Mix") and template.filter_by_work_location:
        field = template.location_field_on_employee or "custom_location"
        filters[field] = template.filter_by_work_location
    return {e.name for e in frappe.get_all("Employee", filters=filters, fields=["name"])}


def classify_unaccounted(unaccounted_emp_ids, indexes, run):
    """For each emp in unaccounted, return list of dicts:
        {"employee": ..., "auto_reason": str, "auto_category": str|None}
    """
    if not unaccounted_emp_ids:
        return []

    period_start = getdate(run.payroll_period_start) if run.payroll_period_start else None
    period_end = getdate(run.payroll_period_end) if run.payroll_period_end else None

    results = []
    for emp_id in sorted(unaccounted_emp_ids):
        e = indexes["employees"].get(emp_id)
        if not e:
            # Indexes were filtered; fall back to direct lookup
            e = frappe.db.get_value(
                "Employee", emp_id,
                ["status", "relieving_date"],
                as_dict=True,
            ) or {}

        status = e.get("status") if isinstance(e, dict) else getattr(e, "status", None)
        relieving = e.get("relieving_date") if isinstance(e, dict) else getattr(e, "relieving_date", None)

        auto_reason = "missing_from_file"
        auto_category = None

        if status and status != "Active":
            auto_reason = f"employee status = {status}"
            auto_category = "Already Inactive"
        elif relieving:
            rd = getdate(relieving)
            if period_start and rd < period_start:
                auto_reason = f"relieved on {rd}"
                auto_category = "Already Inactive"
            elif period_start and period_end and period_start <= rd <= period_end:
                auto_reason = f"relieved mid-period on {rd}"
                auto_category = "Left Mid-Month (EOS Required)"
        elif period_start and period_end:
            # Check leave applications overlapping the period
            leaves = frappe.get_all(
                "Leave Application",
                filters={
                    "employee": emp_id,
                    "status": "Approved",
                    "from_date": ["<=", period_end],
                    "to_date": [">=", period_start],
                },
                fields=["name", "leave_type"],
                limit=1,
            )
            if leaves:
                leave_type = (leaves[0].leave_type or "Unknown").strip()
                auto_reason = f"on leave: {leave_type}"
                if "unpaid" in leave_type.lower():
                    auto_category = "On Unpaid Leave"
                else:
                    auto_category = "On Suspended Leave"

        results.append({
            "employee": emp_id,
            "auto_reason": auto_reason,
            "auto_category": auto_category,
        })
    return results
