frappe.ui.form.on('Payroll Import Run', {
    refresh: function(frm) {
        // ===== Action Buttons by Status =====

        if (frm.doc.status === 'Draft' && frm.doc.source_file && frm.doc.template) {
            frm.add_custom_button(__('Parse File'), function() {
                frappe.call({
                    method: 'erc_payroll_automation.erc_payroll_automation.doctype.payroll_import_run.payroll_import_run.trigger_parse',
                    args: { run_name: frm.doc.name },
                    freeze: true,
                    freeze_message: __('Queueing parser...'),
                    callback: function(r) {
                        frappe.show_alert({
                            message: __('Parsing started. Refresh in a moment.'),
                            indicator: 'blue'
                        });
                        setTimeout(() => frm.reload_doc(), 3000);
                    }
                });
            }, __('Actions')).addClass('btn-primary');
        }

        if (frm.doc.status === 'Parsing') {
            frm.add_custom_button(__('Refresh Status'), function() {
                frm.reload_doc();
            }, __('Actions'));

            // SAFE recovery: keeps all data, just changes status
            frm.add_custom_button(__('Revert Status (preserve data)'), function() {
                const target = frm.doc.rows_matched > 0
                    ? (frm.doc.rows_unmatched_in_file > 0 ? 'Reconciliation Pending' : 'Reconciled')
                    : 'Draft';
                frappe.confirm(
                    __('Revert status to {0}? All matched/unmatched/unaccounted rows are preserved. Use this when generate or parse seems stuck on the worker side.', [target]),
                    function() {
                        frappe.call({
                            method: 'erc_payroll_automation.erc_payroll_automation.doctype.payroll_import_run.payroll_import_run.revert_status',
                            args: { run_name: frm.doc.name, target_status: target },
                            callback: () => frm.reload_doc()
                        });
                    }
                );
            }, __('Actions'));

            // DESTRUCTIVE recovery: wipes everything
            frm.add_custom_button(__('Reset to Draft (WIPES data)'), function() {
                frappe.confirm(
                    __('DESTRUCTIVE: Deletes all parsed rows + unmatched + unaccounted, resets status to Draft. Use only if you want to start over. Continue?'),
                    function() {
                        frappe.call({
                            method: 'erc_payroll_automation.erc_payroll_automation.doctype.payroll_import_run.payroll_import_run.reset_to_draft',
                            args: { run_name: frm.doc.name },
                            callback: () => frm.reload_doc()
                        });
                    }
                );
            }, __('Actions'));

            frm.dashboard.add_indicator(__('In progress... if stuck >5 min, use "Revert Status (preserve data)"'), 'orange');
        }

        if (frm.doc.status === 'Parsed' || frm.doc.status === 'Reconciliation Pending') {
            const can_reconcile = (frm.doc.rows_unmatched_in_file === 0) &&
                                   _all_unaccounted_categorized(frm);

            if (can_reconcile) {
                frm.add_custom_button(__('Mark Reconciled'), function() {
                    frappe.call({
                        method: 'erc_payroll_automation.erc_payroll_automation.doctype.payroll_import_run.payroll_import_run.mark_reconciled',
                        args: { run_name: frm.doc.name },
                        callback: () => frm.reload_doc()
                    });
                }, __('Actions')).addClass('btn-primary');
            } else {
                frm.dashboard.add_indicator(
                    __('Resolve {0} unmatched + {1} uncategorized first',
                       [frm.doc.rows_unmatched_in_file, _count_uncategorized(frm)]),
                    'red'
                );
            }
        }

        if (frm.doc.status === 'Reconciled') {
            frm.add_custom_button(__('Generate Outputs'), function() {
                frappe.call({
                    method: 'erc_payroll_automation.erc_payroll_automation.doctype.payroll_import_run.payroll_import_run.trigger_generate_outputs',
                    args: { run_name: frm.doc.name },
                    freeze: true,
                    freeze_message: __('Generating Excel outputs...'),
                    callback: function() {
                        setTimeout(() => frm.reload_doc(), 3000);
                    }
                });
            }, __('Actions')).addClass('btn-primary');
        }

        if (frm.doc.status === 'Outputs Generated' || frm.doc.status === 'Closed') {
            if (frm.doc.internal_sheet_file) {
                frm.add_custom_button(__('Internal Sheet'), function() {
                    window.open(frm.doc.internal_sheet_file);
                }, __('Downloads'));
            }
            if (frm.doc.additional_salary_file) {
                frm.add_custom_button(__('Additional Salary Import'), function() {
                    window.open(frm.doc.additional_salary_file);
                }, __('Downloads'));
            }
            if (frm.doc.validation_report_file) {
                frm.add_custom_button(__('Validation Report'), function() {
                    window.open(frm.doc.validation_report_file);
                }, __('Downloads'));
            }
        }

        // ===== Summary Indicators on Dashboard =====
        if (frm.doc.rows_matched > 0) {
            frm.dashboard.add_indicator(
                __('{0} matched / {1} unmatched / {2} unaccounted',
                    [frm.doc.rows_matched, frm.doc.rows_unmatched_in_file, frm.doc.rows_unaccounted_in_system]),
                _summary_indicator_color(frm)
            );
        }

        if (frm.doc.rows_with_errors > 0) {
            frm.dashboard.add_indicator(
                __('{0} rows with errors', [frm.doc.rows_with_errors]), 'red'
            );
        }
    },

    template: function(frm) {
        // When template changes, fetch defaults
        if (frm.doc.template) {
            frappe.db.get_doc('Payroll Import Template', frm.doc.template).then(t => {
                if (!frm.doc.payroll_period_start) {
                    // Default to first/last day of current month
                    const today = frappe.datetime.get_today();
                    const start = frappe.datetime.month_start(today);
                    const end = frappe.datetime.month_end(today);
                    frm.set_value('payroll_period_start', start);
                    frm.set_value('payroll_period_end', end);
                }
            });
        }
    }
});


function _all_unaccounted_categorized(frm) {
    if (!frm.doc.unaccounted_employees || frm.doc.unaccounted_employees.length === 0) {
        return true;
    }
    return frm.doc.unaccounted_employees.every(r => r.category);
}

function _count_uncategorized(frm) {
    if (!frm.doc.unaccounted_employees) return 0;
    return frm.doc.unaccounted_employees.filter(r => !r.category).length;
}

function _summary_indicator_color(frm) {
    if (frm.doc.rows_unmatched_in_file > 0 ||
        _count_uncategorized(frm) > 0) return 'red';
    if (frm.doc.rows_with_warnings > 0) return 'orange';
    return 'green';
}
