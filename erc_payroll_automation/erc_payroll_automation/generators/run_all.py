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
    run = frappe.get_doc("Payroll Import Run", run_name)
    template = frappe.get_doc("Payroll Import Template", run.template)

    try:
        # Step 1: Re-run validators on current parsed_rows
        validators.validate_run(run, template, indexes=None)
        run.save(ignore_permissions=True)
        frappe.db.commit()

        # Step 2 + 3: build both files
        internal_sheet.generate(run, template)
        additional_salary.generate(run, template)

        # Step 4: stamp + transition
        run.db_set("outputs_generated_at", now_datetime(), update_modified=False)
        run.db_set("outputs_generated_by", user or frappe.session.user, update_modified=False)
        run.db_set("status", "Outputs Generated", update_modified=False)
        run.db_set("parse_error_log", "")

    except Exception:
        frappe.log_error(
            title=f"Payroll Import generate-outputs failed: {run_name}",
            message=frappe.get_traceback(),
        )
        run.db_set("parse_error_log", frappe.get_traceback())
        run.db_set("status", "Reconciled", update_modified=False)
        raise
