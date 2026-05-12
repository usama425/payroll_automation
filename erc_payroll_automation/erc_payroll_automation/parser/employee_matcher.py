"""
employee_matcher — four-tier matching engine:

    Tier 1: Iqama / National ID (exact)
    Tier 2: Passport (exact)
    Tier 3: IBAN (exact, normalized)
    Tier 4: Fuzzy name (rapidfuzz, threshold from template.name_match_threshold)

Builds in-memory indexes scoped to template.project and template.filter_by_work_location
to keep matching fast for the typical 1500-row file.

Stub. Full implementation in the next chunk.
"""


def build_indexes(template):
    """Return a dict of dicts keyed by id-type → value → employee_id."""
    raise NotImplementedError("employee_matcher.build_indexes — coming in next chunk")


def match_row(parsed_row: dict, indexes: dict, template) -> dict:
    """
    Try each tier in order; return the first hit as
    {"employee": "EMP-0001", "method": "iqama_exact", "confidence": 1.0}
    or {"employee": None, ...} when nothing meets the threshold.
    """
    raise NotImplementedError("employee_matcher.match_row — coming in next chunk")
