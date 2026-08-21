"""Reading whatever file the admin dropped in the inbox.

Deliberately tolerant about *form* (encoding, delimiter, a title row above the
headers, a trailing totals row) and deliberately strict about *content* — that
is the mapping layer's job, not this one's.

Excel is read through openpyxl in Python rather than DuckDB's excel extension,
because loading that extension downloads it from the internet on first use.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import pandas as pd

from ..models import IngestError

CSV_SUFFIXES = {".csv", ".txt", ".tsv"}
EXCEL_SUFFIXES = {".xlsx", ".xlsm", ".xls"}
ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")


def is_supported(path: Path) -> bool:
    return path.suffix.lower() in (CSV_SUFFIXES | EXCEL_SUFFIXES)


def _sniff_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def read_headers(path: Path, read_spec: dict[str, Any] | None = None) -> list[str]:
    """Read just the header row — used by source detection, which must be cheap."""
    df = read_file(path, read_spec, nrows=5)
    return [str(c) for c in df.columns]


def read_file(
    path: Path,
    read_spec: dict[str, Any] | None = None,
    nrows: int | None = None,
) -> pd.DataFrame:
    """Load a CSV or Excel export into a DataFrame with original headers intact."""
    read_spec = read_spec or {}
    suffix = path.suffix.lower()
    # header_row is 1-based in the YAML (what an admin sees in Excel).
    header_row = int(read_spec.get("header_row", 1)) - 1
    na_values = read_spec.get("na_values") or ["", "-", "N/A", "NULL", "#N/A"]

    if suffix in EXCEL_SUFFIXES:
        df = pd.read_excel(
            path,
            sheet_name=read_spec.get("sheet", 0),
            header=header_row,
            na_values=na_values,
            keep_default_na=True,
            nrows=nrows,
            engine="openpyxl" if suffix != ".xls" else None,
            dtype=object,
        )
    elif suffix in CSV_SUFFIXES:
        df = _read_csv(path, read_spec, header_row, na_values, nrows)
    else:
        raise IngestError(
            f"{path.name}: unsupported file type {suffix!r} "
            f"(expected one of {sorted(CSV_SUFFIXES | EXCEL_SUFFIXES)})"
        )

    if isinstance(df, dict):  # pragma: no cover - sheet_name=None not used
        raise IngestError(f"{path.name}: multiple sheets returned; set read.sheet")

    df = df.dropna(axis=1, how="all")
    df.columns = [str(c).strip() for c in df.columns]
    df = df.loc[:, ~df.columns.str.startswith("Unnamed:")]
    if read_spec.get("skip_blank_rows", True):
        df = df.dropna(axis=0, how="all")
    df = _drop_totals_row(df)
    return df.reset_index(drop=True)


def _read_csv(
    path: Path,
    read_spec: dict[str, Any],
    header_row: int,
    na_values: list[str],
    nrows: int | None,
) -> pd.DataFrame:
    encoding = read_spec.get("encoding", "auto")
    encodings = ENCODINGS if encoding in (None, "auto") else (encoding,)
    delimiter = read_spec.get("delimiter", "auto")

    last_error: Exception | None = None
    for enc in encodings:
        try:
            sep = delimiter
            if sep in (None, "auto"):
                with path.open("r", encoding=enc, errors="strict") as fh:
                    sep = _sniff_delimiter(fh.read(8192))
            return pd.read_csv(
                path,
                sep=sep,
                header=header_row,
                encoding=enc,
                na_values=na_values,
                keep_default_na=True,
                nrows=nrows,
                dtype=object,
                skip_blank_lines=True,
            )
        except (UnicodeDecodeError, UnicodeError) as exc:
            last_error = exc
            continue
        except pd.errors.EmptyDataError as exc:
            raise IngestError(f"{path.name}: file is empty") from exc
    raise IngestError(
        f"{path.name}: could not decode with any of {list(encodings)} — "
        f"set read.encoding in the mapping file ({last_error})"
    )


def _drop_totals_row(df: pd.DataFrame) -> pd.DataFrame:
    """Drop a trailing 'Total'/'Grand Total' row if the export tacked one on.

    Left in place it would be counted as a person, which quietly inflates every
    stage on that channel.
    """
    if df.empty:
        return df
    first_col = df.columns[0]
    last_value = str(df.iloc[-1][first_col]).strip().lower()
    if last_value in {"total", "totals", "grand total", "sum", "subtotal"}:
        return df.iloc[:-1]
    return df


def file_sha256(path: Path) -> str:
    """Content hash, used by the load ledger to skip byte-identical re-drops."""
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
