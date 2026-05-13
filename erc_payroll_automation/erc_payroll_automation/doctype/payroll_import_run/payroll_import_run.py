import frappe
from frappe.model.document import Document
from frappe import _
from frappe.utils import getdate


class PayrollImportRun(Document):
    def validate(self):
        self.validate_template_complete()
        self.validate_period()
        self.set_default_posting_date()
        self.compute_validation_pass_rate()

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
