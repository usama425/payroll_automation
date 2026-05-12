"""
generators.run_all — entry point for the Generate Outputs button.

Produces:
    - internal_sheet_file: 37-column RSG-Malqa Excel (universal output format)
    - validation_report_file: per-row validation log

Stub. Full implementation in the chunk after the parser.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime


def generate_outputs(run_name: str, user: str | None = None):
    """
    Background-job entry point. Called from PayrollImportRun.trigger_generate_outputs.
    """
    run = frappe.get_doc("Payroll Import Run", run_name)

    try:
        # TODO: full implementation — runs validators, GOSI calc, emits Excels
        run.db_set("parse_error_log", _(
            "Output generator not yet implemented. The generators.run_all.generate_outputs "
            "stub was called. Drop the generator chunk into "
            "erc_payroll_automation/erc_payroll_automation/generators/."
        ), update_modified=False)
        run.db_set("status", "Reconciled", update_modified=False)
        raise NotImplementedError(
            "generators.run_all.generate_outputs is not implemented yet. "
            "Awaiting generator chunk from spec."
        )
    except NotImplementedError:
        raise
    except Exception:
        frappe.log_error(
            title=f"Payroll Import generate-outputs failed: {run_name}",
            message=frappe.get_traceback(),
        )
        run.db_set("status", "Reconciled", update_modified=False)
        raise
    else:
        run.db_set("outputs_generated_at", now_datetime(), update_modified=False)
        run.db_set("outputs_generated_by", user or frappe.session.user, update_modified=False)
        run.db_set("status", "Outputs Generated", update_modified=False)
