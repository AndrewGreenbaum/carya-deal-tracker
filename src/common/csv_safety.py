"""Keep exported text from being interpreted as spreadsheet formulas."""
import re

_SAFE_NUMBER_OR_PHONE = re.compile(r"^[+-]?[0-9][0-9 .()\-]*$")


def safe_csv_cell(value):
    if not isinstance(value, str):
        return value
    stripped = value.lstrip()
    # Plain signed numbers and telephone numbers cannot call spreadsheet functions.
    if _SAFE_NUMBER_OR_PHONE.fullmatch(stripped):
        return value
    if stripped.startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")):
        return "'" + value
    return value


def safe_csv_row(values):
    return [safe_csv_cell(value) for value in values]
