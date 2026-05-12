import frappe
from frappe.model.document import Document
from frappe import _


REQUIRED_TARGET_FIELDS = {"raw_id_value", "raw_name"}


class PayrollImportTemplate(Document):
    def validate(self):
        self._validate_column_map()
        self._validate_name_threshold()

    def _validate_column_map(self):
        targets = [r.target_field for r in (self.column_map or []) if r.target_field]
        seen = set()
        for t in targets:
            if t in seen:
                frappe.throw(_("Column Map contains duplicate target_field: {0}").format(t))
            seen.add(t)

        missing = REQUIRED_TARGET_FIELDS - seen
        if missing:
            frappe.msgprint(
                _("Column Map is missing recommended fields: {0}").format(", ".join(sorted(missing))),
                indicator="orange",
                alert=True,
            )

    def _validate_name_threshold(self):
        if self.name_match_threshold is None:
            return
        if not (0 <= self.name_match_threshold <= 1):
            frappe.throw(_("Fuzzy Name Match Threshold must be between 0 and 1"))
