"""Orchestrator for the Generate Outputs button.

Pipeline:
    1. Re-run validators (in case user edited rows after parse)
    2. Build the internal ERC sheet (RSG-<Project>-<Month>.xlsx) with highlights
    3. Build the Additional Salary import file (Additional_Salary_RSG_<run>.xlsx)
    4. Stamp outputs_generated_at / by, transition status to Outputs Generated
"""

import frappe
from frappe.utils import now_datetime

from ..parser import validators
from . import internal_sheet, additional_salary


def generate_outputs(run_name: str, user: str = None):
    frappe.log_error(
        title=f"Payroll Import generate STARTED: {run_name}",
        message=f"Worker picked up generate job for {run_name} (user={user})",
    )

    # Ensure proper user context for file operations in background worker
    effective_user = user or frappe.session.user or "Administrator"
    if effective_user in ("Guest", "", None):
        effective_user = "Administrator"
    frappe.set_user(effective_user)

    run = frappe.get_doc("Payroll Import Run", run_name)
    template = frappe.get_doc("Payroll Import Template", run.template)

    try:
        # Step 1: Re-run validators on current parsed_rows
        validators.validate_run(run, template, indexes=None)

        # Update only validation fields directly — avoids slow run.save() with N child rows
        for row in run.parsed_rows:
            frappe.db.set_value(
                "Payroll Import Row", row.name,
                {
                    "validation_status": row.validation_status,
                    "validation_messages": row.validation_messages or "",
                },
                update_modified=False,
            )

        frappe.db.set_value(
            "Payroll Import Run", run.name,
            {
                "rows_with_warnings": run.rows_with_warnings,
                "rows_with_errors": run.rows_with_errors,
            },
            update_modified=False,
        )
        frappe.db.commit()

        frappe.log_error(
            title=f"Payroll Import generate: validators done {run_name}",
            message=(
                f"warnings={run.rows_with_warnings} errors={run.rows_with_errors}. "
                f"parsed_rows={len(run.parsed_rows)} "
                f"unmatched={len(run.unmatched_source_rows)} "
                f"unaccounted={len(run.unaccounted_employees)}"
            ),
        )

        # Step 2: Build internal ERC sheet
        internal_url = internal_sheet.generate(run, template)
        frappe.log_error(
            title=f"Payroll Import generate: internal sheet done {run_name}",
            message=f"internal_sheet_file={internal_url!r}",
        )

        # Step 3: Build additional salary import file
        additional_url = additional_salary.generate(run, template)
        frappe.log_error(
            title=f"Payroll Import generate: additional salary done {run_name}",
            message=f"additional_salary_file={additional_url!r}",
        )

        # Step 4: Stamp + transition
        frappe.db.set_value(
            "Payroll Import Run", run.name,
            {
                "outputs_generated_at": now_datetime(),
                "outputs_generated_by": effective_user,
                "status": "Outputs Generated",
                "parse_error_log": "",
            },
            update_modified=False,
        )
        frappe.db.commit()

        frappe.log_error(
            title=f"Payroll Import generate COMPLETE: {run_name}",
            message=(
                f"Status=Outputs Generated. "
                f"internal_sheet_file={internal_url!r} "
                f"additional_salary_file={additional_url!r}"
            ),
        )

    except Exception:
        frappe.log_error(
            title=f"Payroll Import generate-outputs failed: {run_name}",
            message=frappe.get_traceback(),
        )
        try:
            frappe.db.set_value(
                "Payroll Import Run", run.name,
                {
                    "parse_error_log": frappe.get_traceback(),
                    "status": "Reconciled",
                },
                update_modified=False,
            )
            frappe.db.commit()
        except Exception:
            pass
        raise
