"""Data & admin — the plug & play control panel.

This is where a new company's exports get connected. It has to answer three
questions well, because everything else depends on them:

* What is in the inbox, and what does the app think each file is?
* Which of my columns did it understand, and which did it ignore?
* What happened last time, and what went wrong?
"""

from __future__ import annotations

import shutil
from pathlib import Path, PurePosixPath, PureWindowsPath

import pandas as pd
import streamlit as st

import data_access
from comms_dashboard.config import get_mappings, get_settings
from comms_dashboard.db import query_df
from comms_dashboard.ingest.loader import (
    ingest_file,
    ingest_inbox,
    remember_source_override,
    supersede_batch,
)
from comms_dashboard.ingest.reader import file_sha256, is_supported, read_file
from comms_dashboard.ingest.registry import build_adapters, detect_source
from comms_dashboard.models import LoadReport

#: Extensions the inbox will accept. The ``type=`` argument to
#: ``st.file_uploader`` only filters the browser's file picker — Streamlit does
#: not enforce it server-side — so the check has to happen here too.
UPLOAD_EXTENSIONS = {".csv", ".tsv", ".txt", ".xlsx", ".xlsm", ".xls"}


def safe_inbox_target(inbox: Path, uploaded_name: str) -> Path:
    """Resolve an uploaded file's name to a path *inside* the inbox.

    ``UploadedFile.name`` is the filename from the browser's multipart request.
    Tornado passes the ``Content-Disposition`` filename through verbatim and
    Streamlit stores it unmodified, so it is attacker-controlled: joining it
    onto the inbox directly lets ``../../config/secret_salt`` — or an absolute
    path, which replaces the base entirely — write anywhere the app's user can.

    Both separators are stripped, not just the platform's: a Linux host must
    still reject ``..\\..\\evil.csv``, because the name comes from whatever
    machine the browser is running on.

    Raises ``ValueError`` if nothing usable survives, so the caller reports the
    file as rejected rather than silently writing somewhere unexpected.
    """
    # Take the last segment under both POSIX and Windows rules, so neither
    # separator can survive into the joined path.
    name = PureWindowsPath(PurePosixPath(uploaded_name).name).name
    if not name or name in {".", ".."} or name.startswith("."):
        raise ValueError(f"unusable filename: {uploaded_name!r}")
    if Path(name).suffix.lower() not in UPLOAD_EXTENSIONS:
        raise ValueError(
            f"{name!r} is not a supported export "
            f"({', '.join(sorted(UPLOAD_EXTENSIONS))})"
        )

    target = inbox / name
    # Belt and braces: prove the result really is under the inbox before any
    # write. Catches anything the name-stripping above failed to anticipate.
    try:
        target.resolve().relative_to(inbox.resolve())
    except ValueError as exc:  # pragma: no cover - unreachable via the checks above
        raise ValueError(f"{uploaded_name!r} resolves outside the inbox") from exc
    return target

@st.cache_data(show_spinner=False)
def _probe(path_str: str, mtime: float, size: int) -> dict:
    """Detect a file's source and read a preview, cached per file version.

    The inbox panel re-renders on every widget interaction, and detecting plus
    previewing every file each time makes the page crawl once there are more
    than a handful. Keyed on mtime and size, so replacing a file in the inbox
    still re-probes it.
    """
    path = Path(path_str)
    detection = detect_source(path, build_adapters())
    return {
        "source": detection.source,
        "reason": detection.reason,
        "table": detection.as_table(),
        "headers": list(detection.probe.headers),
    }


@st.cache_data(show_spinner=False)
def _preview(path_str: str, mtime: float, size: int, source: str) -> pd.DataFrame:
    mapping = get_mappings()[source]
    return read_file(Path(path_str), mapping.read, nrows=200)


def _file_key(path: Path) -> tuple[str, float, int]:
    stat = path.stat()
    return str(path), stat.st_mtime, stat.st_size


STATUS_ICON = {
    "loaded": "✅",
    "loaded_with_warnings": "⚠️",
    "duplicate": "↩️",
    "rejected": "❌",
}


def render() -> None:
    st.title("Data & admin")
    settings = get_settings()
    inbox = settings.path("inbox")
    inbox.mkdir(parents=True, exist_ok=True)

    tabs = st.tabs(["Inbox", "Load history", "Mappings", "Warehouse"])

    with tabs[0]:
        _inbox_tab(settings, inbox)
    with tabs[1]:
        _history_tab()
    with tabs[2]:
        _mappings_tab()
    with tabs[3]:
        _warehouse_tab(settings)


# --------------------------------------------------------------------------- #
# Inbox
# --------------------------------------------------------------------------- #


def _inbox_tab(settings, inbox: Path) -> None:
    st.subheader("Inbox")
    st.caption(f"Drop platform exports into `{inbox}` — CSV or Excel.")

    left, right = st.columns([2, 1])
    with left:
        uploaded = st.file_uploader(
            "Or upload a file straight into the inbox",
            type=["csv", "xlsx", "xlsm", "xls", "tsv", "txt"],
            accept_multiple_files=True,
        )
        if uploaded:
            written, refused = 0, []
            for item in uploaded:
                try:
                    target = safe_inbox_target(inbox, item.name)
                except ValueError as exc:
                    refused.append(str(exc))
                    continue
                target.write_bytes(item.getbuffer())
                written += 1
            if written:
                st.success(f"Copied {written} file(s) into the inbox.")
            for reason in refused:
                st.error(f"Refused: {reason}")
            if written:
                st.rerun()
    with right:
        st.write("")
        if st.button("Copy sample exports into the inbox"):
            samples = settings.path("samples")
            count = 0
            for path in sorted(samples.iterdir()):
                if path.is_file():
                    shutil.copy2(path, inbox / path.name)
                    count += 1
            st.success(f"Copied {count} sample file(s).")
            st.rerun()

    files = sorted(p for p in inbox.iterdir() if p.is_file() and is_supported(p))
    if not files:
        st.info("The inbox is empty.")
        return

    st.markdown(f"**{len(files)} file(s) waiting**")
    load_now = st.button("Load everything in the inbox", type="primary", key="load_top")
    mappings = get_mappings()
    con = data_access.connection()
    campaigns = query_df(con, "SELECT campaign_id, campaign_name FROM dim_campaign ORDER BY campaign_name")
    campaign_labels = dict(zip(campaigns["campaign_id"], campaigns["campaign_name"])) \
        if not campaigns.empty else {}

    overrides: dict[str, tuple[str | None, str | None]] = {}

    for path in files:
        probe = _probe(*_file_key(path))
        detected = probe["source"]
        icon = "🔎" if detected else "❓"
        with st.expander(
            f"{icon} **{path.name}** — "
            + (f"detected as `{detected}`" if detected else "source not recognised"),
            expanded=detected is None,
        ):
            _file_preview(path, probe, detected, mappings, campaign_labels, overrides)

    st.markdown("---")
    if load_now or st.button("Load everything in the inbox", type="primary",
                             key="load_bottom"):
        _run_load(files, overrides, settings)


def _file_preview(path, probe, detected, mappings, campaign_labels, overrides) -> None:
    st.caption(probe["reason"])

    chosen_source = detected
    chosen_campaign = None

    if detected is None:
        st.warning(
            "The app will not guess: a wrong guess would silently file one "
            "channel's numbers under another. Pick the source below — the choice "
            "is remembered for this file.",
            icon=":material/warning:",
        )
        st.dataframe(
            pd.DataFrame(probe["table"]),
            use_container_width=True,
            hide_index=True,
        )
        chosen_source = st.selectbox(
            "This file is a…",
            [None] + sorted(mappings),
            format_func=lambda s: "— pick one —" if s is None else mappings[s].display_name,
            key=f"source_{path.name}",
        )

    if chosen_source:
        mapping = mappings[chosen_source]
        try:
            preview = _preview(*_file_key(path), chosen_source)
        except Exception as exc:  # noqa: BLE001 - surfaced to the admin
            st.error(f"Could not read this file: {exc}")
            return

        st.markdown("**Column mapping**")
        st.dataframe(
            _mapping_preview(preview, mapping),
            use_container_width=True,
            hide_index=True,
        )

        unmapped = _unmapped_columns(preview, mapping)
        if unmapped:
            st.caption(
                f"Ignored columns: {', '.join(unmapped)}. If one of those holds "
                f"data you need, add its header to `config/mappings/"
                f"{chosen_source}.yaml`."
            )

        if "campaign" in mapping.keys and campaign_labels:
            has_column = _mapping_preview(preview, mapping).query(
                "Field == 'campaign (key)' and Found != '—'"
            ).shape[0] > 0
            if not has_column:
                st.warning(
                    "No campaign column found in this export — Teams attendance "
                    "reports never have one. Assign a campaign before loading.",
                    icon=":material/label:",
                )
            chosen_campaign = st.selectbox(
                "Assign to campaign"
                + ("" if not has_column else " (overrides the column)"),
                [None] + list(campaign_labels),
                format_func=lambda c: "— use the file's own value —"
                if c is None
                else campaign_labels[c],
                key=f"campaign_{path.name}",
            )

        # Rendered inline rather than in an expander: this whole panel is
        # already inside one, and Streamlit does not allow them to nest.
        st.markdown("**First rows, as the app reads them**")
        st.dataframe(preview.head(10), use_container_width=True)

    overrides[str(path)] = (chosen_source if detected is None else None, chosen_campaign)


def _mapping_preview(preview: pd.DataFrame, mapping) -> pd.DataFrame:
    from comms_dashboard.ingest.normalize import normalise_header

    index = {normalise_header(c): str(c) for c in preview.columns}

    def find(aliases) -> str:
        for alias in aliases:
            column = index.get(normalise_header(alias))
            if column:
                return column
        return "—"

    rows = []
    for name, spec in mapping.keys.items():
        rows.append(
            {
                "Field": f"{name} (key)",
                "Type": spec.id_type or "key",
                "Found": find(spec.aliases),
                "Required": "yes" if spec.required else "no",
            }
        )
    for name, spec in mapping.fields.items():
        rows.append(
            {
                "Field": name,
                "Type": "constant" if spec.is_const else spec.type,
                "Found": "(constant)" if spec.is_const else find(spec.aliases),
                "Required": "yes" if spec.required else "no",
            }
        )
    return pd.DataFrame(rows)


def _unmapped_columns(preview: pd.DataFrame, mapping) -> list[str]:
    from comms_dashboard.ingest.normalize import normalise_header

    known: set[str] = set()
    for spec in list(mapping.keys.values()) + list(mapping.fields.values()):
        for alias in getattr(spec, "aliases", ()):
            known.add(normalise_header(alias))
    return [str(c) for c in preview.columns if normalise_header(c) not in known]


def _run_load(files, overrides, settings) -> None:
    con = data_access.connection()
    reports: list[LoadReport] = []
    progress = st.progress(0.0, "Loading…")

    def priority(path: Path) -> int:
        name = path.name.lower()
        if "roster" in name or "employee" in name:
            return 0
        if "campaign" in name and "registry" in name:
            return 1
        return 2

    ordered = sorted(files, key=priority)
    for i, path in enumerate(ordered):
        source_override, campaign_override = overrides.get(str(path), (None, None))
        if source_override:
            remember_source_override(
                con, file_sha256(path), source_override, campaign_override
            )
        reports.append(
            ingest_file(
                con,
                path,
                source_override=source_override,
                campaign_override=campaign_override,
                settings=settings,
            )
        )
        progress.progress((i + 1) / len(ordered), f"Loaded {path.name}")
    progress.empty()

    # Without this the pages would keep serving pre-load numbers, which looks
    # exactly like the loader having failed.
    data_access.clear_caches()
    _probe.clear()
    _preview.clear()

    for report in reports:
        _render_report(report)
    st.success("Done. The dashboard pages now reflect this data.")


def _render_report(report: LoadReport) -> None:
    icon = STATUS_ICON.get(report.status, "•")
    with st.expander(
        f"{icon} {report.file_name} — {report.status.replace('_', ' ')} "
        f"({report.rows_loaded:,} row(s) loaded)",
        expanded=report.status == "rejected",
    ):
        columns = st.columns(4)
        columns[0].metric("Read", f"{report.rows_read:,}")
        columns[1].metric("Loaded", f"{report.rows_loaded:,}")
        columns[2].metric("Rejected", f"{report.rows_rejected:,}")
        columns[3].metric("Time", f"{report.elapsed_ms:,} ms")
        st.caption(
            f"Source: `{report.source}` · mapping v{report.mapping_version} · "
            f"campaign from: {report.campaign_resolution or 'n/a'}"
        )
        for issue in report.issues:
            renderer = {
                "error": st.error,
                "warning": st.warning,
                "info": st.caption,
            }[issue.severity]
            text = f"**{issue.code}** — {issue.message}"
            if issue.example_values:
                text += f"  \ne.g. `{'`, `'.join(issue.example_values)}`"
            renderer(text)


# --------------------------------------------------------------------------- #
# History, mappings, warehouse
# --------------------------------------------------------------------------- #


def _history_tab() -> None:
    version = data_access.data_version()
    st.subheader("Load history")
    history = data_access.load_history(version)
    if history.empty:
        st.info("Nothing has been loaded yet.")
        return

    display = history.copy()
    display["status"] = display["status"].map(
        lambda s: f"{STATUS_ICON.get(s, '•')} {s.replace('_', ' ')}"
    )
    st.dataframe(
        display[
            ["batch_id", "finished_at", "file_name", "source", "status",
             "rows_read", "rows_loaded", "rows_rejected", "campaign_id"]
        ].rename(
            columns={
                "batch_id": "Batch",
                "finished_at": "When",
                "file_name": "File",
                "source": "Source",
                "status": "Status",
                "rows_read": "Read",
                "rows_loaded": "Loaded",
                "rows_rejected": "Rejected",
                "campaign_id": "Campaign",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )

    batch = st.selectbox("Inspect a batch", history["batch_id"].tolist())
    issues = data_access.load_issues(int(batch), version)
    if issues.empty:
        st.caption("No issues recorded for this batch.")
    else:
        st.dataframe(
            issues.rename(
                columns={
                    "severity": "Severity",
                    "code": "Code",
                    "column_name": "Column",
                    "message": "What happened",
                    "example_values": "Examples",
                    "occurrence_count": "Count",
                }
            ),
            use_container_width=True,
            hide_index=True,
        )

    st.markdown("---")
    st.markdown("**Force a reload**")
    st.caption(
        "A byte-identical file is skipped on a second drop. Superseding the "
        "previous batch lets it be re-imported — the row-level merge still "
        "prevents double counting."
    )
    row = history[history["batch_id"] == batch]
    if not row.empty and st.button("Supersede this batch"):
        supersede_batch(data_access.connection(), row.iloc[0]["file_name"])
        sha = query_df(
            data_access.connection(),
            "SELECT file_sha256 FROM load_batch WHERE batch_id = ?",
            [int(batch)],
        )
        if not sha.empty:
            supersede_batch(data_access.connection(), sha.iloc[0]["file_sha256"])
        data_access.clear_caches()
        st.success("Superseded. Drop the file into the inbox again to reload it.")


def _mappings_tab() -> None:
    st.subheader("Column mappings")
    st.caption(
        "These files are how the dashboard connects to your exports. Adding your "
        "own column headers to the alias lists is the whole configuration job — "
        "no code change needed. Matching ignores case, spaces and punctuation."
    )
    mappings = get_mappings()
    chosen = st.selectbox(
        "Mapping file",
        sorted(mappings),
        format_func=lambda s: f"{mappings[s].display_name}  ({s}.yaml)",
    )
    mapping = mappings[chosen]

    columns = st.columns(3)
    columns[0].metric("Grain", mapping.grain)
    columns[1].metric("Version", mapping.mapping_version)
    columns[2].metric("Fields", len(mapping.fields))

    st.markdown("**Detection rules**")
    st.dataframe(
        pd.DataFrame(
            [
                {"Rule": "filename patterns", "Value": ", ".join(mapping.detect.filename_glob)},
                {"Rule": "strong headers (3 pts)", "Value": ", ".join(mapping.detect.strong)},
                {"Rule": "weak headers (1 pt)", "Value": ", ".join(mapping.detect.weak)},
                {"Rule": "excluded headers (-5)", "Value": ", ".join(mapping.detect.exclude)},
                {"Rule": "minimum score", "Value": str(mapping.detect.min_score)},
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )

    st.markdown("**Accepted column headers**")
    rows = [
        {
            "Canonical field": name,
            "Type": "constant" if spec.is_const else spec.type,
            "Required": "yes" if spec.required else "no",
            "Accepted headers": ", ".join(spec.aliases) or "(constant)",
        }
        for name, spec in mapping.fields.items()
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    path = Path("config/mappings") / f"{chosen}.yaml"
    if path.exists():
        with st.expander("Raw YAML"):
            st.code(path.read_text(encoding="utf-8"), language="yaml")


def _warehouse_tab(settings) -> None:
    from comms_dashboard.db import table_counts

    version = data_access.data_version()
    st.subheader("Warehouse")
    con = data_access.connection()
    counts = table_counts(con)
    columns = st.columns(3)
    for i, (table, count) in enumerate(counts.items()):
        columns[i % 3].metric(table, f"{count:,}")

    path = settings.path("warehouse")
    if path.exists():
        st.caption(f"`{path}` — {path.stat().st_size / 1024 / 1024:.1f} MB")

    st.markdown("**Stage support matrix**")
    st.caption(
        "Materialised from `config/stage_ladder.yaml` at startup. This is the "
        "single source of truth for what each channel can and cannot measure."
    )
    matrix = data_access.stage_support_matrix(version)
    st.dataframe(
        matrix.rename(
            columns={
                "channel": "Channel",
                "stage": "Stage",
                "support_status": "Status",
                "unit": "Counts",
                "native_name": "Reported as",
                "caveat": "Note",
            }
        ).drop(columns=["stage_ordinal"]),
        use_container_width=True,
        hide_index=True,
    )
