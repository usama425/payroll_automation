import json

import frappe
from frappe.model.document import Document
from frappe import _
from frappe.desk.search import sanitize_searchfield
from frappe.utils import getdate

from ...parser_constants import NUMERIC_FIELDS


class PayrollImportRun(Document):
    def validate(self):
        self.validate_template_complete()
        self.validate_period()
        self.set_default_posting_date()
        self.compute_validation_pass_rate()

    def before_save(self):
        """Process any user-supplied resolutions on unmatched rows.
        Selecting Action='Link to Existing Employee' + Resolved Employee and
        saving will promote the row into parsed_rows with its full salary
        data (parsed from raw_data_json)."""
        if self.docstatus == 1:
            return
        if not self.unmatched_source_rows:
            return

        to_remove = []
        promoted = 0
        for row in self.unmatched_source_rows:
            action = (row.resolution_action or "").strip()
            if not action:
                continue
            if action == "Link to Existing Employee":
                if not row.resolved_employee:
                    continue  # employee not picked yet — leave row in place
                self._promote_unmatched_to_parsed(row)
                to_remove.append(row)
                promoted += 1
            elif action in ("Mark as New Hire (HR will create)",
                            "Ignore (Customer Error)"):
                to_remove.append(row)

        if not to_remove:
            return

        # Remove resolved rows
        self.unmatched_source_rows = [
            r for r in self.unmatched_source_rows if r not in to_remove
        ]
        # Re-number
        for i, r in enumerate(self.unmatched_source_rows, start=1):
            r.idx = i

        self.rows_unmatched_in_file = len(self.unmatched_source_rows)
        if promoted:
            self.rows_matched = (self.rows_matched or 0) + promoted

    def _promote_unmatched_to_parsed(self, unmatched_row):
        """Append a new Payroll Import Row with full salary data parsed from
        the unmatched row's raw_data_json."""
        raw_data = {}
        if unmatched_row.raw_data_json:
            try:
                raw_data = json.loads(unmatched_row.raw_data_json)
            except (ValueError, json.JSONDecodeError):
                raw_data = {}

        new_row = self.append("parsed_rows", {
            "row_number_in_file": unmatched_row.row_number_in_file,
            "employee": unmatched_row.resolved_employee,
            "match_method": "manual_resolution",
            "match_confidence": 1.0,
            "raw_id_value": unmatched_row.raw_id_value,
            "raw_name": unmatched_row.raw_name,
            "raw_iban": unmatched_row.raw_iban,
            "raw_nationality": unmatched_row.raw_nationality,
            "raw_data_json": unmatched_row.raw_data_json,
            "validation_status": "OK",
        })
        for fname in NUMERIC_FIELDS:
            v = raw_data.get(fname)
            if v is None or v == "":
                continue
            try:
                setattr(new_row, fname, float(v))
            except (TypeError, ValueError):
                pass

    def validate_template_complete(self):
        """Customer/Project are optional on Template (so fixtures can install),
        but a Run cannot proceed without them. Enforce at Run-creation time."""
        if not self.template:
            return
        t = frappe.db.get_value(
            "Payroll Import Template", self.template,
            ["customer", "project"], as_dict=True,
        ) or {}
        missing = []
        if not t.get("customer"):
            missing.append("Customer")
        if not t.get("project"):
            missing.append("Project")
        if missing:
            frappe.throw(_(
                "Template '{0}' is missing: {1}. "
                "Open the Template and fill these in before creating a Run."
            ).format(self.template, ", ".join(missing)))

    def validate_period(self):
        if self.payroll_period_start and self.payroll_period_end:
            if getdate(self.payroll_period_end) < getdate(self.payroll_period_start):
                frappe.throw(_("Payroll Period End cannot be before Period Start"))

    def set_default_posting_date(self):
        if not self.posting_date and self.payroll_period_end:
            self.posting_date = self.payroll_period_end

    def compute_validation_pass_rate(self):
        total = (self.rows_matched or 0)
        if total > 0:
            errors = self.rows_with_errors or 0
            self.validation_pass_rate = round(((total - errors) / total) * 100, 2)
        else:
            self.validation_pass_rate = 0

    def before_submit(self):
        if self.status not in ("Outputs Generated", "Closed"):
            frappe.throw(_(
                "Cannot submit until outputs are generated. "
                "Current status: {0}"
            ).format(self.status))

        if self.rows_unmatched_in_file > 0:
            frappe.throw(_(
                "{0} rows in the source file are still unmatched. "
                "Resolve them before submitting."
            ).format(self.rows_unmatched_in_file))

        unresolved_unaccounted = sum(
            1 for r in self.unaccounted_employees
            if not r.category
        )
        if unresolved_unaccounted > 0:
            frappe.throw(_(
                "{0} unaccounted employees still need categorization. "
                "Resolve them before submitting."
            ).format(unresolved_unaccounted))

    def on_submit(self):
        self.db_set("status", "Closed", update_modified=False)

    def on_cancel(self):
        self.db_set("status", "Cancelled", update_modified=False)


# ============================================================================
# Whitelisted methods called from the form's JS
# ============================================================================

@frappe.whitelist()
def trigger_parse(run_name):
    """
    Called from the 'Parse File' button on the form.
    Enqueues the parser as a background job (long queue, 30-min timeout)
    because 1500+ rows can't run inline.
    """
    run = frappe.get_doc("Payroll Import Run", run_name)

    if run.status not in ("Draft", "Parsing", "Parsed", "Reconciliation Pending"):
        frappe.throw(_("Cannot parse from status '{0}'").format(run.status))

    if not run.source_file:
        frappe.throw(_("Source file is required"))

    run.db_set("status", "Parsing", update_modified=False)
    run.db_set("parse_error_log", "", update_modified=False)

    # Clear previous run data
    frappe.db.delete("Payroll Import Row", {"parent": run.name})
    frappe.db.delete("Payroll Unmatched Source Row", {"parent": run.name})
    frappe.db.delete("Payroll Unaccounted Employee", {"parent": run.name})
    frappe.db.commit()

    frappe.enqueue(
        method="erc_payroll_automation.erc_payroll_automation.parser.file_parser.run_parse",
        queue="long",
        timeout=1800,
        run_name=run.name,
        user=frappe.session.user,
        enqueue_after_commit=True,
    )

    return {"status": "queued", "message": _("Parsing started in background")}


@frappe.whitelist()
def trigger_generate_outputs(run_name):
    """
    Called from 'Generate Outputs' button. Only enabled after reconciliation
    is complete (rows_unmatched_in_file = 0 AND all unaccounted categorized).
    """
    run = frappe.get_doc("Payroll Import Run", run_name)

    if run.status != "Reconciled":
        frappe.throw(_(
            "Run must be in 'Reconciled' status to generate outputs. "
            "Current: {0}"
        ).format(run.status))

    if run.rows_unmatched_in_file > 0:
        frappe.throw(_("{0} rows are still unmatched in the source file"
                       ).format(run.rows_unmatched_in_file))

    uncategorized = sum(1 for r in run.unaccounted_employees if not r.category)
    if uncategorized > 0:
        frappe.throw(_("{0} unaccounted employees are not categorized yet"
                       ).format(uncategorized))

    # IMPORTANT: do NOT change status here. The background job will flip status to
    # "Outputs Generated" on success or leave it at "Reconciled" on failure.
    # (Previously this set status="Parsing" which (a) misled the user, (b) was
    # destructive because the form's "Reset to Draft" button shows for Parsing
    # and would wipe matched/unmatched/unaccounted rows on click.)

    frappe.enqueue(
        method="erc_payroll_automation.erc_payroll_automation.generators.run_all.generate_outputs",
        queue="long",
        timeout=1200,
        run_name=run.name,
        user=frappe.session.user,
        enqueue_after_commit=True,
    )

    return {"status": "queued", "message": _("Output generation started in background. Refresh in ~30 seconds.")}


@frappe.whitelist()
def force_regenerate_outputs(run_name):
    """Re-run output generation regardless of unmatched/uncategorized state.

    Recovery path when a previous generate run left the Run at 'Outputs Generated'
    without populating the attach fields, or when the user edited rows after the
    last successful generate and wants fresh outputs.
    """
    run = frappe.get_doc("Payroll Import Run", run_name)
    if run.docstatus == 1:
        frappe.throw(_("Cannot regenerate after Submit. Cancel the document first."))
    if run.status not in ("Reconciled", "Outputs Generated", "Reconciliation Pending"):
        frappe.throw(_(
            "Force regenerate only available from Reconciliation Pending, "
            "Reconciled, or Outputs Generated. Current: {0}"
        ).format(run.status))

    run.db_set("status", "Reconciled", update_modified=False)
    frappe.db.commit()

    frappe.enqueue(
        method="erc_payroll_automation.erc_payroll_automation.generators.run_all.generate_outputs",
        queue="long",
        timeout=1200,
        run_name=run.name,
        user=frappe.session.user,
        enqueue_after_commit=True,
    )
    return {"status": "queued",
            "message": _("Re-generation queued. Refresh in ~30 seconds.")}


@frappe.whitelist()
def mark_reconciled(run_name):
    """
    Called when finance has resolved all unmatched/unaccounted rows.
    Validates and transitions Parsed -> Reconciled.
    """
    run = frappe.get_doc("Payroll Import Run", run_name)

    if run.rows_unmatched_in_file > 0:
        frappe.throw(_("Cannot mark reconciled: {0} rows still unmatched in file"
                       ).format(run.rows_unmatched_in_file))

    uncategorized = sum(1 for r in run.unaccounted_employees if not r.category)
    if uncategorized > 0:
        frappe.throw(_("Cannot mark reconciled: {0} unaccounted employees not categorized"
                       ).format(uncategorized))

    run.db_set("status", "Reconciled", update_modified=False)
    return {"status": "ok", "message": _("Reconciled. You can now generate outputs.")}


@frappe.whitelist()
def revert_status(run_name, target_status):
    """Safe status reset — does NOT wipe child rows. Use when a job seems stuck
    (status='Parsing' from a dead worker, etc.) but you want to keep all matched/
    unmatched/unaccounted data intact."""
    valid_targets = {"Draft", "Parsed", "Reconciliation Pending", "Reconciled"}
    if target_status not in valid_targets:
        frappe.throw(_(
            "Invalid target status '{0}'. Allowed: {1}"
        ).format(target_status, ", ".join(sorted(valid_targets))))

    run = frappe.get_doc("Payroll Import Run", run_name)
    if run.status in ("Closed", "Cancelled"):
        frappe.throw(_("Cannot revert from terminal status '{0}'").format(run.status))

    run.db_set("status", target_status, update_modified=False)
    run.db_set("parse_error_log", "", update_modified=False)
    return {"status": "ok", "new_status": target_status,
            "message": _("Status reverted to '{0}'. Child rows preserved.").format(target_status)}


@frappe.whitelist()
def reset_to_draft(run_name):
    """Escape hatch when a Parse job is stuck in 'Parsing' (worker dead, OOM,
    silent crash, etc.). Clears child rows and counters, sets status back to Draft
    so the user can click 'Parse File' again."""
    run = frappe.get_doc("Payroll Import Run", run_name)
    if run.status == "Closed":
        frappe.throw(_("Cannot reset a Closed Run"))

    frappe.db.delete("Payroll Import Row", {"parent": run.name})
    frappe.db.delete("Payroll Unmatched Source Row", {"parent": run.name})
    frappe.db.delete("Payroll Unaccounted Employee", {"parent": run.name})
    frappe.db.commit()

    run.db_set({
        "status": "Draft",
        "rows_matched": 0,
        "rows_unmatched_in_file": 0,
        "rows_unaccounted_in_system": 0,
        "rows_with_warnings": 0,
        "rows_with_errors": 0,
        "validation_pass_rate": 0,
        "source_file_sheet_used": "",
        "source_file_rows_total": 0,
        "parse_error_log": "",
    }, update_modified=False)
    return {"status": "ok", "message": _("Reset. You can now Parse File again.")}


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def search_employees_for_resolution(doctype, txt, searchfield, start, page_len, filters):
    """Employee lookup for unmatched-row resolution.

    Accounts users can resolve payroll import rows without needing broad
    Employee list access. The lookup is gated by write access to the parent run.
    """
    filters = filters or {}
    run_name = filters.get("run_name")
    if not run_name:
        return []

    run = frappe.get_doc("Payroll Import Run", run_name)
    if not frappe.has_permission("Payroll Import Run", "write", doc=run):
        frappe.throw(_("Not permitted to resolve rows for this Payroll Import Run"))

    searchfield = sanitize_searchfield(searchfield or "name")
    if searchfield not in {
        "name",
        "employee_name",
        "iqama_national_id",
        "passport_number",
        "bank_ac_no",
    }:
        searchfield = "name"

    like_txt = f"%{txt}%"
    return frappe.db.sql(
        f"""
        SELECT
            name,
            employee_name,
            iqama_national_id,
            bank_ac_no
        FROM `tabEmployee`
        WHERE status = 'Active'
          AND (
              {searchfield} LIKE %(txt)s
              OR name LIKE %(txt)s
              OR employee_name LIKE %(txt)s
              OR iqama_national_id LIKE %(txt)s
              OR passport_number LIKE %(txt)s
              OR bank_ac_no LIKE %(txt)s
          )
        ORDER BY
            CASE WHEN name LIKE %(prefix)s THEN 0 ELSE 1 END,
            employee_name ASC,
            name ASC
        LIMIT %(start)s, %(page_len)s
        """,
        {
            "txt": like_txt,
            "prefix": f"{txt}%",
            "start": start,
            "page_len": page_len,
        },
    )


@frappe.whitelist()
def link_unmatched_to_employee(run_name, unmatched_row_name, employee_id):
    """
    When finance resolves an 'unmatched in file' row by linking it to an employee.
    Moves the row from unmatched_source_rows to parsed_rows.
    """
    run = frappe.get_doc("Payroll Import Run", run_name)

    unmatched = next(
        (r for r in run.unmatched_source_rows if r.name == unmatched_row_name),
        None
    )
    if not unmatched:
        frappe.throw(_("Unmatched row not found"))

    if not frappe.db.exists("Employee", employee_id):
        frappe.throw(_("Employee {0} does not exist").format(employee_id))

    # Promote to parsed_rows
    run.append("parsed_rows", {
        "row_number_in_file": unmatched.row_number_in_file,
        "employee": employee_id,
        "match_method": "manual_resolution",
        "match_confidence": 1.0,
        "raw_id_value": unmatched.raw_id_value,
        "raw_name": unmatched.raw_name,
        "raw_iban": unmatched.raw_iban,
        "raw_data_json": unmatched.raw_data_json,
    })

    # Remove from unmatched
    run.remove(unmatched)

    run.rows_matched = (run.rows_matched or 0) + 1
    run.rows_unmatched_in_file = max(0, (run.rows_unmatched_in_file or 0) - 1)

    run.save(ignore_permissions=True)
    return {"status": "ok"}
