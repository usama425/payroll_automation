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

    # If a PE is already linked, recreate cleanly: delete the old DRAFT and
    # rebuild (refuse if it's already submitted).
    if run.created_payroll_entry and frappe.db.exists(
            "Payroll Entry", run.created_payroll_entry):
        existing = frappe.get_doc("Payroll Entry", run.created_payroll_entry)
        if existing.docstatus == 1:
            frappe.throw(_(
                "Linked Payroll Entry {0} is already submitted — cannot "
                "recreate. Cancel it first if you need to rebuild."
            ).format(existing.name))
        frappe.delete_doc("Payroll Entry", existing.name,
                          ignore_permissions=True, force=True)

    pe_name = _create_draft_payroll_entry(run)
    run.db_set("created_payroll_entry", pe_name, update_modified=False)
    frappe.db.commit()
    return {"status": "ok", "payroll_entry": pe_name}


@frappe.whitelist()
def trigger_reconcile(run_name):
    """Reconcile the Payroll Entry's DRAFT Salary Slips to the sheet's Net Salary
    (single Other Additions / Deduction for absent line per slip). Run AFTER
    'Create Salary Slips' on the Payroll Entry and BEFORE submitting them."""
    run = frappe.get_doc("Payroll Posting Run", run_name)
    if not run.created_payroll_entry:
        frappe.throw(_("No Payroll Entry yet — create it and its Salary Slips first."))
    drafts = frappe.db.count(
        "Salary Slip", {"payroll_entry": run.created_payroll_entry, "docstatus": 0})
    if not drafts:
        frappe.throw(_(
            "No DRAFT Salary Slips found on Payroll Entry {0}. Open it and run "
            "Create Salary Slips first (and don't submit them until after "
            "reconciling).").format(run.created_payroll_entry))

    frappe.enqueue(
        method="erc_payroll_automation.erc_payroll_automation.generators.posting.reconcile_payroll_entry",
        queue="long",
        timeout=1800,
        run_name=run.name,
        user=frappe.session.user,
        enqueue_after_commit=True,
    )
    return {"status": "queued",
            "message": _("Reconciling {0} draft slip(s) to the sheet. Refresh the "
                         "Payroll Entry in ~30–60s.").format(drafts)}


@frappe.whitelist()
def cleanup_duplicate_additional_salary(run_name):
    """Cancel duplicate 'overwrite' Additional Salary records for this run's
    project + period, keeping the newest per (employee, salary component).

    Resolves the HRMS error during slip creation when a period was posted more
    than once: "Multiple Additional Salaries with overwrite property exist for
    Salary Component X between <start> and <end>".
    """
    from collections import defaultdict

    run = frappe.get_doc("Payroll Posting Run", run_name)
    if not run.payroll_period_start or not run.payroll_period_end:
        frappe.throw(_("This run has no payroll period set."))

    filters = {
        "overwrite_salary_structure_amount": 1,
        "docstatus": 1,
        "payroll_date": ["between",
                         [run.payroll_period_start, run.payroll_period_end]],
    }
    # Scope to employees on this run's project when possible.
    if run.project and frappe.get_meta("Employee").has_field("project"):
        employees = frappe.get_all("Employee", filters={"project": run.project},
                                   pluck="name")
        if not employees:
            return {"status": "ok", "cancelled": 0,
                    "message": _("No employees found on project {0}.").format(run.project)}
        filters["employee"] = ["in", employees]

    rows = frappe.get_all(
        "Additional Salary", filters=filters,
        fields=["name", "employee", "salary_component"], order_by="creation asc")

    groups = defaultdict(list)
    for r in rows:
        groups[(r.employee, r.salary_component)].append(r.name)

    cancelled = 0
    for names in groups.values():
        for name in names[:-1]:            # keep the newest, cancel earlier dups
            frappe.get_doc("Additional Salary", name).cancel()
            cancelled += 1
    frappe.db.commit()

    return {"status": "ok", "cancelled": cancelled,
            "message": _("Cancelled {0} duplicate Additional Salary record(s) "
                         "for the period.").format(cancelled)}


@frappe.whitelist()
def revert_status(run_name, target_status="Draft"):
    """Recovery: reset a stuck Parsing/Posting run."""
    if target_status not in ("Draft", "Review"):
        frappe.throw(_("Invalid target status"))
    run = frappe.get_doc("Payroll Posting Run", run_name)
    run.db_set("status", target_status, update_modified=False)
    return {"status": "ok", "new_status": target_status}
