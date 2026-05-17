frappe.ui.form.on('Payroll Posting Run', {
    refresh: function(frm) {
        if (frm.is_new()) return;

        if (['Draft', 'Failed', 'Review'].includes(frm.doc.status)
            && frm.doc.approved_sheet && frm.doc.template) {
            frm.add_custom_button(__('Parse Sheet'), function() {
                frappe.call({
                    method: 'erc_payroll_automation.erc_payroll_automation.doctype.payroll_posting_run.payroll_posting_run.trigger_parse',
                    args: { run_name: frm.doc.name },
                    freeze: true,
                    freeze_message: __('Reading sheet...'),
                    callback: function() {
                        frappe.show_alert({ message: __('Parsing. Refresh shortly.'), indicator: 'blue' });
                        setTimeout(() => frm.reload_doc(), 4000);
                    }
                });
            }).addClass('btn-primary');
        }

        if (frm.doc.status === 'Review') {
            frm.add_custom_button(__('Post to Payroll'), function() {
                const n_apply = (frm.doc.salary_changes || []).filter(r => r.apply).length;
                const n_addsal = (frm.doc.additional_salary_preview || []).length;
                frappe.confirm(
                    __('Post payroll? This will:<br>• Update {0} approved salary change(s) on Employee + create new Salary Structure Assignments<br>• Submit {1} Additional Salary record(s)<br>• Create a DRAFT Payroll Entry for finance to submit<br><br>Salary Slips are NOT auto-submitted by this step.', [n_apply, n_addsal]),
                    function() {
                        frappe.call({
                            method: 'erc_payroll_automation.erc_payroll_automation.doctype.payroll_posting_run.payroll_posting_run.trigger_post',
                            args: { run_name: frm.doc.name },
                            freeze: true,
                            freeze_message: __('Posting to payroll...'),
                            callback: function() {
                                setTimeout(() => frm.reload_doc(), 5000);
                            }
                        });
                    }
                );
            }).addClass('btn-primary');

            frm.add_custom_button(__('Re-parse Sheet'), function() {
                frappe.call({
                    method: 'erc_payroll_automation.erc_payroll_automation.doctype.payroll_posting_run.payroll_posting_run.trigger_parse',
                    args: { run_name: frm.doc.name },
                    callback: () => setTimeout(() => frm.reload_doc(), 4000)
                });
            }, __('Actions'));

            frm.dashboard.add_indicator(
                __('Review {0} salary change(s) — tick Apply on the ones you approve',
                   [(frm.doc.salary_changes || []).length]),
                (frm.doc.salary_changes || []).some(r => r.prorated_flag) ? 'orange' : 'blue'
            );
        }

        if (['Parsing', 'Posting'].includes(frm.doc.status)) {
            frm.add_custom_button(__('Refresh'), () => frm.reload_doc());
            frm.add_custom_button(__('Revert (if stuck)'), function() {
                frappe.call({
                    method: 'erc_payroll_automation.erc_payroll_automation.doctype.payroll_posting_run.payroll_posting_run.revert_status',
                    args: { run_name: frm.doc.name, target_status: frm.doc.status === 'Posting' ? 'Review' : 'Draft' },
                    callback: () => frm.reload_doc()
                });
            }, __('Actions'));
            frm.dashboard.add_indicator(__('{0}... refresh in ~30s', [frm.doc.status]), 'orange');
        }

        if (frm.doc.status === 'Posted') {
            if (frm.doc.created_payroll_entry) {
                frm.add_custom_button(__('Open Draft Payroll Entry'), function() {
                    frappe.set_route('Form', 'Payroll Entry', frm.doc.created_payroll_entry);
                }, __('Actions'));
            }
            frm.dashboard.add_indicator(__('Posted — review the draft Payroll Entry, then Get Employees + Submit there'), 'green');
        }
    },

    template: function(frm) {
        if (frm.doc.template && !frm.doc.payroll_period_start) {
            const today = frappe.datetime.get_today();
            frm.set_value('payroll_period_start', frappe.datetime.month_start(today));
            frm.set_value('payroll_period_end', frappe.datetime.month_end(today));
        }
    }
});
