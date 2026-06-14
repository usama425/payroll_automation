# Diagnose why bridging new-joiner slips don't match the Elite sheet.
# Dumps: each employee's fixed-pay fields + SSAs (base + mirrored allowances)
# + their latest Salary Slips' component breakdown, and the Salary Structure
# component definitions (formula / amount / depends_on_payment_days).
#
# Run:  bench --site <YOUR-SITE> execute erc_payroll_automation.scripts.diag_bridging.run
# (edit EMPS if you want other employees)

import frappe

EMPS = ["HR-EMP-02517", "HR-EMP-02522"]  # Tala, Leonie (bridging new joiners)


def run():
    structures = set()
    for emp in EMPS:
        print("=" * 78)
        print("EMPLOYEE", emp)
        e = frappe.db.get_value(
            "Employee", emp,
            ["employee_name", "date_of_joining", "status", "basic_salary",
             "housing_allowance", "transport_allowance", "food_allowance"],
            as_dict=True) or {}
        print("  fields:", dict(e))

        for s in frappe.get_all(
                "Salary Structure Assignment",
                filters={"employee": emp, "docstatus": 1},
                fields=["name", "salary_structure", "from_date", "base"],
                order_by="from_date desc"):
            structures.add(s.salary_structure)
            doc = frappe.get_doc("Salary Structure Assignment", s.name)
            allow = {f: doc.get(f) for f in
                     ("housing_allowance", "transport_allowance", "food_allowance",
                      "other_allowance", "transportation_allowance")
                     if doc.meta.has_field(f)}
            print(f"  SSA {s.name}  structure={s.salary_structure}  "
                  f"from={s.from_date}  base={s.base}  allow={allow}")

        for sl in frappe.get_all(
                "Salary Slip", filters={"employee": emp, "docstatus": ["!=", 2]},
                fields=["name", "start_date", "end_date", "payment_days",
                        "total_working_days", "gross_pay", "net_pay",
                        "salary_structure", "docstatus"],
                order_by="start_date desc", limit=3):
            print(f"  SLIP {sl.name} {sl.start_date}->{sl.end_date} "
                  f"pay_days={sl.payment_days}/{sl.total_working_days} "
                  f"gross={sl.gross_pay} net={sl.net_pay} "
                  f"struct={sl.salary_structure} docstatus={sl.docstatus}")
            d = frappe.get_doc("Salary Slip", sl.name)
            for row in d.earnings:
                print(f"       EARN   {row.salary_component:<22} amount={row.amount} "
                      f"default={getattr(row,'default_amount',None)} "
                      f"pay_days_based={getattr(row,'depends_on_payment_days',None)}")
            for row in d.deductions:
                print(f"       DEDUCT {row.salary_component:<22} amount={row.amount}")

    for st in sorted(structures):
        print("=" * 78)
        print("SALARY STRUCTURE", st)
        d = frappe.get_doc("Salary Structure", st)
        for kind in ("earnings", "deductions"):
            for row in d.get(kind):
                print(f"  {kind[:4].upper():<5} {row.salary_component:<22} "
                      f"amount={row.amount} "
                      f"based_on_formula={getattr(row,'amount_based_on_formula',None)} "
                      f"formula={getattr(row,'formula',None)!r} "
                      f"pay_days_based={getattr(row,'depends_on_payment_days',None)} "
                      f"cond={getattr(row,'condition',None)!r}")

    print("=" * 78)
    print("END — paste everything above back to Claude.")


if __name__ == "__main__":
    run()
