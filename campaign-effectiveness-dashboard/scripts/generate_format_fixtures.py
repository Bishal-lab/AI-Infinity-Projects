#!/usr/bin/env python3
"""Generate the real-world *format* fixtures in ``data/samples/formats/``.

These carry the exact column headers of three real exports, with entirely
invented values. No company data is committed to this repository — the point is
to lock the header shapes and the awkward behaviours they exposed, so a mapping
change cannot silently stop handling them:

    lms_agency_format.csv        MM/DD/YYYY dates, "Completed and Passed"
                                 status, Job Role as the only segment, and
                                 attempts recorded with 0% progress
    teams_attendance_format.xlsx attendees ONLY - no registration column, no
                                 session length, location inside the name,
                                 "15m" durations
    viva_daily_format.csv        daily community rows with NO community-size
                                 column, every measure split members /
                                 non-members

Usage:  python scripts/generate_format_fixtures.py
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FORMATS = PROJECT_ROOT / "data" / "samples" / "formats"
SEED = 71


def write_lms(rng: random.Random) -> None:
    """LMS export: MDY dates, tri-state status, attempts at zero progress."""
    roles = [
        "Associate Agency Development Manager", "Agency Development Manager",
        "Senior Agency Development Manager", "Manager - Training",
        "Deputy Manager - Training", "Associate Partner - Agency Sales",
    ]
    rows = []
    for i in range(1, 41):
        # The last four never finalise: attempts recorded, progress still 0.
        passed = i <= 36
        completed_on = datetime(2026, 7, 1) + timedelta(days=rng.randint(0, 30))
        score = round(rng.choice([85.71, 92.86, 78.57]), 2) if passed else 0
        rows.append({
            "Corporate Name": "AGENCY",
            "Login Code": f"AAA{i:03d}",
            "System Role": "Learner",
            "Job Role": roles[i % len(roles)],
            "User Full Name": "REDACTED",
            "Employee ID": 10000 + i,
            "Course/Training Name": "Sample Course_Jun'26",
            "Node Event Name": "Sample_Assessment_Jun'26",
            "Progress": 100 if passed else 0,
            "Node Status": "Completed and Passed" if passed else "Submitted not finalized",
            "Marks Obtained": 12 if passed else 0,
            "Aggregate Marks Obtained": score,
            "Max Score": 14,
            # MM/DD/YYYY on purpose: the day part exceeds 12 often enough that
            # a DMY reading would fail outright.
            "Last Access Date": completed_on.strftime("%m/%d/%Y %H:%M:%S"),
            "Completion Date": completed_on.strftime("%m/%d/%Y %H:%M:%S") if passed else "",
            "Attempts": 1 if passed else rng.randint(3, 7),
            "Start Date": "06/18/2026 13:40:00",
            "Minimum Passing Marks": 80,
            "Passed Status": "Pass" if passed else "",
            "Grade Text": "B" if passed else "J",
        })
    pd.DataFrame(rows).to_csv(FORMATS / "lms_agency_format.csv", index=False)


def write_teams(rng: random.Random) -> None:
    """Teams attendance: attendees only, no registration, no session length."""
    cities = ["Gurugram", "Chennai", "Indore", "Lucknow", "Kanpur", "Pune"]
    start = datetime(2026, 6, 25, 16, 25)
    rows = []
    for i in range(1, 41):
        joined = start + timedelta(minutes=rng.randint(0, 25))
        minutes = rng.choice([8, 15, 41, 74, 90, 93, 94])
        left = joined + timedelta(minutes=minutes)
        rows.append({
            "Name": f"Participant {i} ({cities[i % len(cities)]} - Agency)",
            "First Join": joined.strftime("%Y-%m-%d %H:%M:%S"),
            "Last Leave": left.strftime("%Y-%m-%d %H:%M:%S"),
            # Two spellings in one column, as the real export has.
            "In-Meeting Duration": f"{minutes}m" if i % 2 else f"{minutes} min",
            "Email": f"participant{i}-example.com",
            "Participant ID (UPN)": f"participant{i}-example.com",
            "Role": rng.choice(["Presenter", "Attendee"]),
            "Engagement: Camera On": rng.randint(0, 8),
            "Engagement: Raise Hands": rng.randint(0, 3),
            "Engagement: Unmute": rng.randint(0, 10),
        })
    pd.DataFrame(rows).to_excel(
        FORMATS / "teams_attendance_format.xlsx", index=False, engine="openpyxl"
    )


def write_viva(rng: random.Random) -> None:
    """Viva daily community analytics: no community-size column anywhere."""
    rows = []
    for i in range(60):
        day = date(2026, 1, 1) + timedelta(days=i)
        rows.append({
            "Date": day.isoformat(),
            "Members and admins views on posts": rng.randint(11, 900),
            "Non-members views on posts": rng.randint(1, 60),
            "Members and admins messages posted": rng.randint(0, 3),
            "Non-members messages posted": rng.randint(0, 1),
            "Members and admins reactions on messages": rng.randint(0, 10),
            "Non-members reactions on messages": rng.randint(0, 3),
            "People reached": rng.randint(5, 390),
            "Members reached": rng.randint(1, 380),
            "Total answers": rng.randint(0, 5),
            "Total questions": rng.randint(0, 3),
        })
    pd.DataFrame(rows).to_csv(FORMATS / "viva_daily_format.csv", index=False)


def main() -> None:
    rng = random.Random(SEED)
    FORMATS.mkdir(parents=True, exist_ok=True)
    write_lms(rng)
    write_teams(rng)
    write_viva(rng)
    print(f"Wrote format fixtures to {FORMATS}")
    for path in sorted(FORMATS.iterdir()):
        print(f"  {path.name:34s} {path.stat().st_size / 1024:6.1f} KB")


if __name__ == "__main__":
    main()
