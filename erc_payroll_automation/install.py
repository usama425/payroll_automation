import frappe


ROLES = [
    {
        "role_name": "Payroll Import Manager",
        "desk_access": 1,
        "description": "Full control over Payroll Import Runs: parse, reconcile, generate outputs, submit, cancel.",
    },
    {
        "role_name": "Payroll Import User",
        "desk_access": 1,
        "description": "Day-to-day payroll import work: create runs, parse, resolve unmatched/unaccounted rows.",
    },
]


def after_install():
    _ensure_roles()


def _ensure_roles():
    for role in ROLES:
        if frappe.db.exists("Role", role["role_name"]):
            continue
        doc = frappe.new_doc("Role")
        doc.update(role)
        doc.insert(ignore_permissions=True)
    frappe.db.commit()
