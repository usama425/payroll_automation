"""
reconciliation — three-way diff between:
    A) employees expected to be in the file (system view, scoped by project + work_location)
    B) employees actually present in the file (matched rows)

    Matched          = A ∩ B
    Unmatched in file = rows ∈ file with no match in system  (B \\ A)
    Unaccounted       = A \\ B  (employee in system but absent from file)

Auto-categorizes Unaccounted rows when possible:
    - Employee.status == 'Inactive' or relieving_date < period_start  → "Already Inactive"
    - relieving_date BETWEEN period_start AND period_end              → "Left Mid-Month (EOS Required)"
    - On Leave Application overlapping the period                     → "On Unpaid Leave" / "On Suspended Leave"
    - else                                                             → blank (user must categorize)

Stub. Full implementation in the next chunk.
"""


def expected_employees(template):
    """Return the set of Employee names expected to appear in this template's file."""
    raise NotImplementedError("reconciliation.expected_employees — coming in next chunk")


def reconcile(run, matched_employee_ids: set, expected_employee_ids: set):
    """Populate run.unaccounted_employees and update counters."""
    raise NotImplementedError("reconciliation.reconcile — coming in next chunk")
