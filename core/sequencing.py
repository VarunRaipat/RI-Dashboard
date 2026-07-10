"""Auto-incrementing DI No. / Challan No. sequences, scoped per Sale Type."""
from datetime import date
import pandas as pd


def fy_start(on_date=None):
    """Start date (Apr 1) of the Indian financial year containing `on_date`."""
    d = on_date or date.today()
    year = d.year if d.month >= 4 else d.year - 1
    return date(year, 4, 1)


def next_sequence_number(df, column, sale_type, date_col=None, start=1):
    """Next plain integer for `column`, scoped to rows matching `sale_type`.

    Only purely-numeric existing values count towards the max, so legacy
    non-numeric numbers (old codes, imported spreadsheet data) don't block
    the sequence — it just picks up from the highest numeric value seen.

    If `date_col` is given, only rows from the current financial year
    (Apr 1 onward) count towards the max — so the sequence resets to 1
    each new financial year instead of carrying last year's numbers forward.

    `start` sets a floor for this sale_type (e.g. a sequence that should
    begin at 356 instead of 1, confirmed per Sale Type) — it's only used
    when there's no existing numeric data to continue from; once real data
    exists, the sequence continues from its own max as usual and is never
    pulled back down below that.
    """
    if df is None or df.empty or "sale_type" not in df.columns or column not in df.columns:
        return start
    if date_col and date_col in df.columns:
        row_dates = pd.to_datetime(df[date_col], errors="coerce").dt.date
        df = df[row_dates >= fy_start()]
        if df.empty:
            return start
    subset = df.loc[df["sale_type"] == sale_type, column].dropna().astype(str).str.strip()
    nums = pd.to_numeric(subset, errors="coerce").dropna()
    return max(int(nums.max()) + 1, start) if not nums.empty else start


def is_duplicate(df, column, value, sale_type=None, date_col=None):
    """True if `value` (trimmed, case-insensitive) already exists in `column`.

    Pass `sale_type`/`date_col` to scope the check the same way
    `next_sequence_number` scopes its max — otherwise a sequence that
    resets each financial year (or restarts per Sale Type) would flag
    its own reused numbers as duplicates of an earlier year/type.
    """
    value = str(value).strip()
    if df is None or df.empty or column not in df.columns or not value:
        return False
    subset = df
    if sale_type is not None and "sale_type" in subset.columns:
        subset = subset[subset["sale_type"] == sale_type]
    if date_col and date_col in subset.columns:
        row_dates = pd.to_datetime(subset[date_col], errors="coerce").dt.date
        subset = subset[row_dates >= fy_start()]
    existing = subset[column].dropna().astype(str).str.strip().str.lower()
    return value.lower() in set(existing)
