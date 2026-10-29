"""Scans active Employees for a bank account (IBAN) shared by more than one
of them and lists every offending group so finance can investigate.

Two fields on Employee can carry an IBAN on this site — ``iban`` (the one
actually kept up to date; see ``bank_ac_no`` fallback below) and the older
``bank_ac_no``. A row is only flagged when the *cleaned* value (spaces/
punctuation stripped, upper-cased) collides across two or more Active
employees; a blank IBAN is never treated as a duplicate.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime


def _clean_iban(value):
	return "".join(c for c in str(value) if c.isalnum()).upper() if value else ""


def find_duplicate_ibans():
	"""Return (summary_dict, rows) for every IBAN shared by >1 Active employee."""
	employees = frappe.get_all(
		"Employee",
		filters={"status": "Active"},
		fields=[
			"name", "employee_name", "status", "iban", "bank_ac_no",
			"project", "department", "date_of_joining", "basic_salary", "ctc",
		],
	)

	by_iban = {}
	without_iban = 0
	for e in employees:
		key = _clean_iban(e.iban) or _clean_iban(e.bank_ac_no)
		if not key:
			without_iban += 1
			continue
		by_iban.setdefault(key, []).append(e)

	duplicates = {k: v for k, v in by_iban.items() if len(v) > 1}

	summary = {
		"active_employees_scanned": len(employees),
		"employees_without_iban": without_iban,
		"unique_ibans": len(by_iban),
		"duplicate_iban_groups": len(duplicates),
	}

	rows = []
	# Largest groups first, then by IBAN for stable ordering.
	for group_no, (iban, group) in enumerate(
		sorted(duplicates.items(), key=lambda kv: (-len(kv[1]), kv[0])), start=1
	):
		for e in group:
			rows.append({
				"group_no": group_no,
				"iban": iban,
				"employee": e.name,
				"employee_name": e.employee_name,
				"status": e.status,
				"project": e.project,
				"department": e.department,
				"date_of_joining": e.date_of_joining,
				"basic_salary": e.basic_salary,
				"ctc": e.ctc,
			})

	return summary, rows


class PayrollIBANDuplicateCheck(Document):
	def run_scan(self):
		summary, rows = find_duplicate_ibans()
		self.scan_datetime = now_datetime()
		self.active_employees_scanned = summary["active_employees_scanned"]
		self.employees_without_iban = summary["employees_without_iban"]
		self.unique_ibans = summary["unique_ibans"]
		self.duplicate_iban_groups = summary["duplicate_iban_groups"]
		self.set("results", rows)


@frappe.whitelist()
def scan(docname=None):
	"""Run a duplicate-IBAN scan and save the result.

	Creates a new Payroll IBAN Duplicate Check when ``docname`` is not given
	(the "New" form's Scan button), otherwise re-scans and updates the
	existing document in place.
	"""
	if docname and not docname.startswith("new-"):
		doc = frappe.get_doc("Payroll IBAN Duplicate Check", docname)
	else:
		doc = frappe.new_doc("Payroll IBAN Duplicate Check")

	doc.run_scan()
	doc.save()
	frappe.msgprint(
		_("Scanned {0} active employees — {1} IBAN(s) shared by more than one employee.").format(
			doc.active_employees_scanned, doc.duplicate_iban_groups
		),
		indicator="orange" if doc.duplicate_iban_groups else "green",
		alert=True,
	)
	return doc.name
