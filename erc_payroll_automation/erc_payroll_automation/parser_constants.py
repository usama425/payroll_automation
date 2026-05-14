"""Field names shared between the parser, controllers, and generators.

Lives outside the parser package so it can be imported from doctype controllers
without dragging in openpyxl / rapidfuzz.
"""


# Canonical numeric fields parsed off the customer sheet (in order they appear on Row).
NUMERIC_FIELDS = (
    "basic_per_contract", "housing_per_contract", "transportation_per_contract",
    "other_allowance_per_contract", "total_per_contract",
    "working_days",
    "basic_per_working_days", "housing_per_working_days",
    "transportation_per_working_days", "other_allowance_per_working_days",
    "overtime", "other_income", "deductions", "hq_deductions",
    "gosi_from_file", "net_salary_from_file",
)
