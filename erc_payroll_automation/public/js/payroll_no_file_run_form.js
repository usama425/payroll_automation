frappe.ui.form.on('Payroll No File Run', {
    refresh: function(frm) {
        if (frm.is_new()) return;

        if (frm.doc.status === 'Draft' || frm.doc.status === 'Failed') {
            frm.add_custom_button(__('Generate'), function() {
                frappe.call({
                    method: 'erc_payroll_automation.erc_payroll_automation.doctype.payroll_no_file_run.payroll_no_file_run.trigger_generate',
                    args: { run_name: frm.doc.name },
                    freeze: true,
                    freeze_message: __('Queueing generator...'),
                    callback: function() {
                        frappe.show_alert({
                            message: __('Generation started. Refresh in a moment.'),
                            indicator: 'blue',
                        });
                        setTimeout(() => frm.reload_doc(), 4000);
                    }
                });
            }).addClass('btn-primary');
        }

        if (frm.doc.status === 'Generating') {
            frm.add_custom_button(__('Refresh'), () => frm.reload_doc());
            frm.add_custom_button(__('Revert to Draft (if stuck)'), function() {
                frappe.confirm(
                    __('Reset status to Draft? Use only if the worker is dead.'),
                    function() {
                        frappe.call({
                            method: 'erc_payroll_automation.erc_payroll_automation.doctype.payroll_no_file_run.payroll_no_file_run.revert_to_draft',
                            args: { run_name: frm.doc.name },
                            callback: () => frm.reload_doc(),
                        });
                    }
                );
            });
            frm.dashboard.add_indicator(__('Generating... refresh in ~30s'), 'orange');
        }

        if (frm.doc.status === 'Generated') {
            if (frm.doc.internal_sheet_file) {
                frm.add_custom_button(__('Internal Sheet'), function() {
                    window.open(frm.doc.internal_sheet_file);
                }, __('Downloads'));
            }
            frm.add_custom_button(__('Re-generate'), function() {
                frappe.confirm(
                    __('Re-run generation and overwrite the existing file?'),
                    function() {
                        frm.set_value('status', 'Draft');
                        frm.save().then(() => {
                            frappe.call({
                                method: 'erc_payroll_automation.erc_payroll_automation.doctype.payroll_no_file_run.payroll_no_file_run.trigger_generate',
                                args: { run_name: frm.doc.name },
                                callback: () => setTimeout(() => frm.reload_doc(), 4000),
                            });
                        });
                    }
                );
            }, __('Actions'));
        }
    },

    project: function(frm) {
        if (frm.doc.project && !frm.doc.payroll_period_start) {
            const today = frappe.datetime.get_today();
            frm.set_value('payroll_period_start', frappe.datetime.month_start(today));
            frm.set_value('payroll_period_end', frappe.datetime.month_end(today));
        }
    },
});
