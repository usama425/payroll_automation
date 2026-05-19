import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate


class PayrollWPSExportRun(Document):
    def validate(self):
        if self.payroll_period_start and self.payroll_period_end:
            if getdate(self.payroll_period_end) < getdate(self.payroll_period_start):
                frappe.throw(_("Payroll Period End cannot be before Period Start"))
        if not self.posting_date and self.payroll_period_end:
            self.posting_date = self.payroll_period_end


@frappe.whitelist()
def trigger_generate(run_name):
    """Generate the Al Rajhi WPS payroll upload file from submitted Salary Slips."""
    run = frappe.get_doc("Payroll WPS Export Run", run_name)

    if run.status not in ("Draft", "Generated", "Failed"):
        frappe.throw(_("Cannot generate from status '{0}'").format(run.status))
    if not run.project:
        frappe.throw(_("Project is required"))
    if not run.payroll_period_start or not run.payroll_period_end:
        frappe.throw(_("Payroll Period Start and End are required"))

    run.db_set("status", "Generating", update_modified=False)
    run.db_set("generation_log", "", update_modified=False)

    frappe.enqueue(
        method="erc_payroll_automation.erc_payroll_automation.generators.wps_payroll_file.generate_wps_file",
        queue="long",
        timeout=900,
        run_name=run.name,
        user=frappe.session.user,
        enqueue_after_commit=True,
    )
    return {
        "status": "queued",
        "message": _("WPS file generation started. Refresh in ~30 seconds."),
    }


@frappe.whitelist()
def revert_to_draft(run_name):
    """Reset a stuck Generating run back to Draft."""
    run = frappe.get_doc("Payroll WPS Export Run", run_name)
    run.db_set("status", "Draft", update_modified=False)
    run.db_set("generation_log", "", update_modified=False)
    return {"status": "ok"}
