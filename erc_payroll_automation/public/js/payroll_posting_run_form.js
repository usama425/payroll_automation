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
                const n_changes = (frm.doc.salary_changes || []).length;
                frappe.confirm(
                    __('Post payroll? This will:<br>• Create a DRAFT Payroll Entry for finance to submit<br><br>Salary changes are NOT applied by the system ({0} detected — report only; correct Employee manually after project email approval).<br>Salary Slips are NOT auto-submitted by this step.', [n_changes]),
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
                __('{0} salary change(s) detected — report only, the system will NOT apply them. Correct Employee manually after project email approval.',
                   [(frm.doc.salary_changes || []).length]),
                (frm.doc.salary_changes || []).length ? 'orange' : 'blue'
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
            const has_pe = !!frm.doc.created_payroll_entry;
            if (has_pe) {
                frm.add_custom_button(__('Open Draft Payroll Entry'), function() {
                    frappe.set_route('Form', 'Payroll Entry', frm.doc.created_payroll_entry);
                }, __('Actions'));

                // After Create Salary Slips on the PE (and BEFORE submitting them),
                // make each slip's Net equal the sheet with one Other Additions /
                // Deduction for absent line.
                frm.add_custom_button(__('Reconcile Slips to Sheet'), function() {
                    frappe.confirm(
                        __('Reconcile the draft Salary Slips so each one\'s Net equals the sheet?<br><br>Run this AFTER "Create Salary Slips" on the Payroll Entry and BEFORE submitting the slips. It adds a single Other Additions (sheet higher) or Deduction for absent (sheet lower) line per slip. Safe to re-run.'),
                        function() {
                            frappe.call({
                                method: 'erc_payroll_automation.erc_payroll_automation.doctype.payroll_posting_run.payroll_posting_run.trigger_reconcile',
                                args: { run_name: frm.doc.name },
                                freeze: true,
                                freeze_message: __('Reconciling slips to the sheet...'),
                                callback: function(r) {
                                    if (r.message && r.message.message) {
                                        frappe.show_alert({ message: r.message.message, indicator: 'blue' });
                                    }
                                }
                            });
                        }
                    );
                }).addClass('btn-primary');

                frm.dashboard.add_indicator(__('Posted — on the Payroll Entry: Create Salary Slips → "Reconcile Slips to Sheet" → Submit'), 'green');
            } else {
                frm.dashboard.add_indicator(__('Posted, but Payroll Entry not created — click "Create Payroll Entry"'), 'orange');
            }
            // Create (or rebuild) the project-scoped draft Payroll Entry.
            // Rebuild deletes the old DRAFT PE first (e.g. to fix a wrongly-scoped one).
            const label = has_pe ? __('Recreate Payroll Entry') : __('Create Payroll Entry');
            frm.add_custom_button(label, function() {
                frappe.confirm(
                    has_pe
                        ? __('Delete the current draft Payroll Entry and rebuild it scoped to the project + work location?')
                        : __('Create the draft Payroll Entry scoped to the project + work location?'),
                    function() {
                        frappe.call({
                            method: 'erc_payroll_automation.erc_payroll_automation.doctype.payroll_posting_run.payroll_posting_run.create_payroll_entry',
                            args: { run_name: frm.doc.name },
                            freeze: true,
                            freeze_message: __('Building Payroll Entry...'),
                            callback: function(r) {
                                if (r.message && r.message.payroll_entry) {
                                    frappe.show_alert({ message: __('Payroll Entry: {0}', [r.message.payroll_entry]), indicator: 'green' });
                                }
                                frm.reload_doc();
                            }
                        });
                    }
                );
            }).addClass(has_pe ? '' : 'btn-primary');

            // Resolve HRMS "Multiple Additional Salaries with overwrite property"
            // errors from a period posted more than once.
            frm.add_custom_button(__('Clean up duplicate Additional Salary'), function() {
                frappe.confirm(
                    __('Cancel duplicate overwrite Additional Salary records for this project + period? Keeps the newest per employee + component.'),
                    function() {
                        frappe.call({
                            method: 'erc_payroll_automation.erc_payroll_automation.doctype.payroll_posting_run.payroll_posting_run.cleanup_duplicate_additional_salary',
                            args: { run_name: frm.doc.name },
                            freeze: true,
                            freeze_message: __('Cleaning up duplicates...'),
                            callback: function(r) {
                                if (r.message && r.message.message) {
                                    frappe.msgprint({ title: __('Done'), indicator: 'green', message: r.message.message });
                                }
                            }
                        });
                    }
                );
            }, __('Actions'));
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
