# ERC Payroll Automation

Internal Frappe v14 app for ERC's payroll import & reconciliation workflow.

## What it does

Customers send monthly payroll Excel files in inconsistent formats. This app:

1. **Parses** the customer file using a per-project Template (column map, sheet name, header row, transforms).
2. **Matches** each row to an Employee using a four-tier strategy: Iqama → Passport → IBAN → fuzzy name.
3. **Reconciles** three-way:
   - Matched (in file + in system)
   - Unmatched in file (rows in customer file with no matching employee)
   - Unaccounted in system (employees expected by project/work-location but missing from file)
4. **Validates** rows against per-template Validation Rules (tolerance %, exact match, GOSI consistency, etc.).
5. **Generates** the universal 37-column internal sheet and a validation report.

## Phase 1 scope

- Configuration: Payroll Import Template + Column Map + Validation Rule
- Transaction: Payroll Import Run (submittable) + 3 child tables
- Workflow: Draft → Parsing → Reconciliation Pending → Reconciled → Outputs Generated → Closed
- Roles: Payroll Import Manager, Payroll Import User

Phase 2 (push to Salary Slip / Additional Salary) is out of scope.

## Install

```bash
bench get-app /path/to/erc_payroll_automation
bench --site <your-site> install-app erc_payroll_automation
bench --site <your-site> migrate
```

## Assumptions

- Frappe / ERPNext v14
- `Work Location` DocType already exists in the bench
- `custom_location` (Link → Work Location) custom field already exists on Employee

## License

MIT
