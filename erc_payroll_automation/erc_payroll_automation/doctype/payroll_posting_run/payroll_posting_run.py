import frappe
from frappe.model.document import Document
from frappe import _
from frappe.utils import getdate


class PayrollPostingRun(Document):
    def validate(self):
        if self.payroll_period_start and self.payroll_period_end:
            if getdate(self.payroll_period_end) < getdate(self.payroll_period_start):
                frappe.throw(_("Payroll Period End cannot be before Period Start"))
        if not self.posting_date and self.payroll_period_end:
            self.posting_date = self.payroll_period_end


@frappe.whitelist()
def trigger_parse(run_name):
    """Read the approved Internal Sheet and build the review tables."""
    run = frappe.get_doc("Payroll Posting Run", run_name)
    if run.status not in ("Draft", "Review", "Failed"):
        frappe.throw(_("Cannot parse from status '{0}'").format(run.status))
    if not run.approved_sheet:
        frappe.throw(_("Upload the approved Internal Sheet first"))
    if not run.template:
        frappe.throw(_("Template is required"))

    run.db_set("status", "Parsing", update_modified=False)
    run.db_set("posting_log", "", update_modified=False)
    frappe.enqueue(
        method="erc_payroll_automation.erc_payroll_automation.generators.posting.parse_sheet",
        queue="long",
        timeout=900,
        run_name=run.name,
        user=frappe.session.user,
        enqueue_after_commit=True,
    )
    return {"status": "queued",
            "message": _("Parsing the sheet. Refresh in ~20 seconds.")}


@frappe.whitelist()
def trigger_post(run_name):
    """Apply approved salary changes + create Additional Salary + draft Payroll Entry."""
    run = frappe.get_doc("Payroll Posting Run", run_name)
    if run.status != "Review":
        frappe.throw(_(
            "Run must be in 'Review' status to post. Current: {0}"
        ).format(run.status))

    tmpl_structure = frappe.db.get_value(
        "Payroll Import Template", run.template, "salary_structure")
    if not tmpl_structure:
        frappe.throw(_(
            "Template '{0}' has no Salary Structure set. Open the Template "
            "and fill the 'Salary Structure' field before posting."
        ).format(run.template))

    run.db_set("status", "Posting", update_modified=False)
    frappe.enqueue(
        method="erc_payroll_automation.erc_payroll_automation.generators.posting.post",
        queue="long",
        timeout=1500,
        run_name=run.name,
        user=frappe.session.user,
        enqueue_after_commit=True,
    )
    return {"status": "queued",
            "message": _("Posting to payroll. Refresh in ~30 seconds.")}


@frappe.whitelist()
def create_payroll_entry(run_name):
    """Recovery: create just the draft Payroll Entry for an already-Posted run
    that doesn't have one yet — e.g. when an earlier post applied SSAs +
    Additional Salary but failed only at the Payroll Entry step. Safe to call
    once; refuses if a PE is already linked."""
    from erc_payroll_automation.erc_payroll_automation.generators.posting import (
        _create_draft_payroll_entry,
    )

    run = frappe.get_doc("Payroll Posting Run", run_name)
    if run.status != "Posted":
        frappe.throw(_("Create Payroll Entry is only available on a Posted run. "
                       "Current: {0}").format(run.status))
    if run.created_payroll_entry and frappe.db.exists(
            "Payroll Entry", run.created_payroll_entry):
        frappe.throw(_("A Payroll Entry is already linked: {0}").format(
            run.created_payroll_entry))

    pe_name = _create_draft_payroll_entry(run)
    run.db_set("created_payroll_entry", pe_name, update_modified=False)
    frappe.db.commit()
    return {"status": "ok", "payroll_entry": pe_name}


@frappe.whitelist()
def revert_status(run_name, target_status="Draft"):
    """Recovery: reset a stuck Parsing/Posting run."""
    if target_status not in ("Draft", "Review"):
        frappe.throw(_("Invalid target status"))
    run = frappe.get_doc("Payroll Posting Run", run_name)
    run.db_set("status", target_status, update_modified=False)
    return {"status": "ok", "new_status": target_status}
