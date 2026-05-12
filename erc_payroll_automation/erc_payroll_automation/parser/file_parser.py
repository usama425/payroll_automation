"""
file_parser — opens the customer Excel, applies the template's column map,
runs employee matching + reconciliation, persists results onto the Run.

This is a stub. Full implementation arrives in the next chunk per spec:
- read source_file via openpyxl
- pick sheet (template.sheet_name_override or first non-empty)
- read header row at template.header_row_index
- map columns using template.column_map (header → fallback index)
- apply per-cell transforms from transforms.py
- detect Excel formula errors / broken XLOOKUP
- skip blank-ID rows if template.skip_blank_id_rows
- delegate matching to employee_matcher.match_row
- delegate three-way diff to reconciliation.reconcile
- write parsed_rows / unmatched_source_rows / unaccounted_employees onto the Run
- update counters and transition status to "Parsed" / "Reconciliation Pending"
"""

import frappe
from frappe import _


def run_parse(run_name: str, user: str | None = None):
    """
    Background-job entry point. Called from PayrollImportRun.trigger_parse via frappe.enqueue.

    Args:
        run_name: name of the Payroll Import Run
        user: user who triggered the parse (used for ownership / audit)
    """
    run = frappe.get_doc("Payroll Import Run", run_name)

    try:
        # TODO: full parse implementation — coming in next chunk
        run.db_set("parse_error_log", _(
            "Parser not yet implemented. The file_parser.run_parse stub was called.\n"
            "Drop the parser module from the next chunk into "
            "erc_payroll_automation/erc_payroll_automation/parser/file_parser.py"
        ), update_modified=False)
        run.db_set("status", "Draft", update_modified=False)
        raise NotImplementedError(
            "file_parser.run_parse is not implemented yet. "
            "Awaiting parser chunk from spec."
        )
    except NotImplementedError:
        # Re-raise so the worker job is marked as failed in the queue
        raise
    except Exception:
        frappe.log_error(
            title=f"Payroll Import parse failed: {run_name}",
            message=frappe.get_traceback(),
        )
        run.db_set(
            "parse_error_log",
            frappe.get_traceback(),
            update_modified=False,
        )
        run.db_set("status", "Draft", update_modified=False)
        raise
