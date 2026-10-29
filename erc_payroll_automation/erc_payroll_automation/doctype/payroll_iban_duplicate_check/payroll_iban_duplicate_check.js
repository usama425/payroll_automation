frappe.ui.form.on('Payroll IBAN Duplicate Check', {
    refresh: function (frm) {
        frm.add_custom_button(__('Scan Now'), function () {
            frappe.call({
                method: 'erc_payroll_automation.erc_payroll_automation.doctype.payroll_iban_duplicate_check.payroll_iban_duplicate_check.scan',
                args: { docname: frm.doc.name },
                freeze: true,
                freeze_message: __('Scanning active employees...'),
                callback: function (r) {
                    if (r.message && r.message !== frm.doc.name) {
                        frappe.set_route('Form', 'Payroll IBAN Duplicate Check', r.message);
                    } else {
                        frm.reload_doc();
                    }
                }
            });
        }).addClass('btn-primary');

        if (frm.doc.duplicate_iban_groups > 0) {
            frm.dashboard.add_indicator(
                __('{0} IBAN(s) shared by more than one active employee', [frm.doc.duplicate_iban_groups]),
                'red'
            );
        } else if (frm.doc.scan_datetime) {
            frm.dashboard.add_indicator(__('No duplicate IBANs found'), 'green');
        }
    }
});
