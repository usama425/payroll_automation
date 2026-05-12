"""
transforms — small, named functions referenced by Payroll Import Column Map.transform.

Each transform takes a single raw cell value (str / int / float / datetime / None)
and returns a normalized value. The parser dispatches by name.

Stub. Full implementations arrive with the parser chunk.
"""


def trim_whitespace(value):
    raise NotImplementedError("transforms.trim_whitespace — coming in next chunk")


def to_float(value):
    raise NotImplementedError("transforms.to_float — coming in next chunk")


def to_int(value):
    raise NotImplementedError("transforms.to_int — coming in next chunk")


def excel_date_to_iso(value):
    raise NotImplementedError("transforms.excel_date_to_iso — coming in next chunk")


def iban_normalize(value):
    raise NotImplementedError("transforms.iban_normalize — coming in next chunk")


def nationality_normalize(value):
    raise NotImplementedError("transforms.nationality_normalize — coming in next chunk")


def strip_arabic_diacritics(value):
    raise NotImplementedError("transforms.strip_arabic_diacritics — coming in next chunk")


def uppercase(value):
    raise NotImplementedError("transforms.uppercase — coming in next chunk")


def lowercase(value):
    raise NotImplementedError("transforms.lowercase — coming in next chunk")


# Used by file_parser to dispatch a transform by name.
REGISTRY = {
    "none": lambda v: v,
    "trim_whitespace": trim_whitespace,
    "to_float": to_float,
    "to_int": to_int,
    "excel_date_to_iso": excel_date_to_iso,
    "iban_normalize": iban_normalize,
    "nationality_normalize": nationality_normalize,
    "strip_arabic_diacritics": strip_arabic_diacritics,
    "uppercase": uppercase,
    "lowercase": lowercase,
}
