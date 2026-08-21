#!/usr/bin/env python3
"""Generate the synthetic sample exports in ``data/samples/``.

The point of these files is to prove the mapping layer rather than flatter it,
so each one deliberately uses a *different* header style, boolean vocabulary
and file format — the way five unrelated platforms actually behave:

    email          CSV,  "Delivered"/"Bounced" status strings, open COUNTS
    whatsapp       XLSX, "Yes"/"No" booleans, timestamped read receipts
    lms            CSV,  1/0 flags, "85%" progress strings, tri-state status
    teams_webinar  XLSX, "01:05:00" and "45 min" durations mixed together
    viva_engage    CSV,  aggregate per post, ISO dates, no person rows at all

Dates are written in DMY or ISO to match ``parsing.date_order: DMY`` in
config/settings.yaml. Run this after changing that setting.

Usage:  python scripts/generate_samples.py
"""

from __future__ import annotations

import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

SAMPLES = PROJECT_ROOT / "data" / "samples"
EDGE = SAMPLES / "edge_cases"

SEED = 20260401
HEADCOUNT = 1200

DEPARTMENTS = [
    ("Finance", 0.08),
    ("Operations", 0.16),
    ("Technology", 0.14),
    ("HR", 0.06),
    ("Sales", 0.12),
    ("Legal", 0.04),
    ("Manufacturing", 0.28),
    ("Marketing", 0.12),
]
REGIONS = [("EMEA", 0.45), ("AMER", 0.30), ("APAC", 0.20), ("LATAM", 0.05)]
GRADES = [("Band 1", 0.34), ("Band 2", 0.31), ("Band 3", 0.22), ("Band 4", 0.11), ("Exec", 0.02)]
SITES = ["London", "Manchester", "Chicago", "Singapore", "Pune", "Sao Paulo", "Remote"]

# Deskless workforces engage far less with desk-bound channels. Baking this in
# gives the coverage page a real story instead of a flat heatmap.
DEPT_ENGAGEMENT_FACTOR = {
    "Manufacturing": 0.55,
    "Operations": 0.80,
    "Sales": 0.95,
    "Finance": 1.15,
    "Technology": 1.20,
    "HR": 1.25,
    "Legal": 1.10,
    "Marketing": 1.15,
}

FIRST_NAMES = [
    "Aisha", "Ben", "Chloe", "Daniel", "Elena", "Farid", "Grace", "Hiro",
    "Ines", "Jonas", "Kira", "Liam", "Maya", "Noah", "Olu", "Priya",
    "Quinn", "Rosa", "Sam", "Tariq", "Uma", "Viktor", "Wei", "Xenia",
    "Yusuf", "Zoe", "Amara", "Bruno", "Cara", "Dmitri",
]
LAST_NAMES = [
    "Ahmed", "Brown", "Chen", "Dubois", "Evans", "Fischer", "Garcia", "Hansen",
    "Iyer", "Jensen", "Kowalski", "Lopez", "Muller", "Novak", "Okafor", "Patel",
    "Rossi", "Silva", "Tanaka", "Ueda", "Vargas", "Walsh", "Yilmaz", "Zhang",
]

CAMPAIGNS = [
    {
        "campaign_id": "cyber-aware-2026q1",
        "campaign_name": "Cyber Awareness 2026",
        "programme": "Security Awareness",
        "objective": "awareness",
        "owner_team": "Information Security",
        "start_date": date(2026, 1, 12),
        "end_date": date(2026, 2, 28),
        "mandatory": True,
        "target_population": HEADCOUNT,
        "target_completion_rate": 0.95,
        "target_engagement_rate": 0.35,
    },
    {
        "campaign_id": "code-of-conduct-2026",
        "campaign_name": "Code of Conduct Refresh",
        "programme": "Compliance",
        "objective": "compliance",
        "owner_team": "Legal & Compliance",
        "start_date": date(2026, 2, 2),
        "end_date": date(2026, 3, 31),
        "mandatory": True,
        "target_population": HEADCOUNT,
        "target_completion_rate": 0.98,
        "target_engagement_rate": 0.30,
    },
    {
        "campaign_id": "erp-golive-2026",
        "campaign_name": "ERP Go-Live Readiness",
        "programme": "ERP Programme",
        "objective": "change",
        "owner_team": "Change Management",
        "start_date": date(2026, 3, 2),
        "end_date": date(2026, 4, 30),
        "mandatory": False,
        "target_population": 640,
        "target_completion_rate": 0.80,
        "target_engagement_rate": 0.45,
    },
    {
        "campaign_id": "wellbeing-2026q2",
        "campaign_name": "Wellbeing Month",
        "programme": "Employee Experience",
        "objective": "engagement",
        "owner_team": "People Experience",
        "start_date": date(2026, 5, 1),
        "end_date": date(2026, 5, 31),
        "mandatory": False,
        "target_population": HEADCOUNT,
        "target_completion_rate": None,
        "target_engagement_rate": 0.25,
    },
    {
        "campaign_id": "dei-learning-2026",
        "campaign_name": "Inclusive Leadership",
        "programme": "Diversity, Equity & Inclusion",
        "objective": "training",
        "owner_team": "People Development",
        "start_date": date(2026, 4, 6),
        "end_date": date(2026, 6, 30),
        "mandatory": False,
        "target_population": 320,
        "target_completion_rate": 0.85,
        "target_engagement_rate": 0.40,
    },
    {
        "campaign_id": "townhall-q1-2026",
        "campaign_name": "Q1 Town Hall",
        "programme": "Leadership Comms",
        "objective": "awareness",
        "owner_team": "Internal Communications",
        "start_date": date(2026, 4, 14),
        "end_date": date(2026, 4, 16),
        "mandatory": False,
        "target_population": HEADCOUNT,
        "target_completion_rate": None,
        "target_engagement_rate": 0.30,
    },
]
CAMPAIGN_BY_ID = {c["campaign_id"]: c for c in CAMPAIGNS}


def weighted(rng: random.Random, choices: list[tuple[str, float]]) -> str:
    return rng.choices([c[0] for c in choices], weights=[c[1] for c in choices])[0]


def dmy(value: date | datetime | None) -> str:
    if value is None:
        return ""
    return value.strftime("%d/%m/%Y")


def dmy_hm(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.strftime("%d/%m/%Y %H:%M")


# --------------------------------------------------------------------------- #
# Employee roster — the join spine for everything else
# --------------------------------------------------------------------------- #


def build_employees(rng: random.Random) -> list[dict]:
    employees: list[dict] = []
    used: set[str] = set()
    for i in range(1, HEADCOUNT + 1):
        first = rng.choice(FIRST_NAMES)
        last = rng.choice(LAST_NAMES)
        local = f"{first}.{last}".lower()
        suffix = 1
        candidate = local
        while candidate in used:
            suffix += 1
            candidate = f"{local}{suffix}"
        used.add(candidate)

        emp_id = f"E{i:05d}"
        dept = weighted(rng, DEPARTMENTS)
        employees.append(
            {
                "employee_id": emp_id,
                "first_name": first,
                "last_name": last,
                "work_email": f"{candidate}@example.com",
                "upn": f"{candidate}@example.com",
                "mobile": f"+4477{rng.randint(10000000, 99999999)}",
                "lms_user_id": f"L{i:05d}",
                "department": dept,
                "sub_department": f"{dept} Team {rng.randint(1, 4)}",
                "region": weighted(rng, REGIONS),
                "country": "",
                "site": rng.choice(SITES),
                "grade_band": weighted(rng, GRADES),
                "employment_type": rng.choices(
                    ["Permanent", "Fixed Term", "Contractor"], weights=[0.86, 0.08, 0.06]
                )[0],
                "is_people_manager": rng.random() < 0.18,
                "is_active": True,
            }
        )
    return employees


def write_roster(employees: list[dict]) -> None:
    df = pd.DataFrame(
        [
            {
                "Employee ID": e["employee_id"],
                "Work Email": e["work_email"],
                "UPN": e["upn"],
                "Mobile": e["mobile"],
                "LMS User ID": e["lms_user_id"],
                "Department": e["department"],
                "Sub Department": e["sub_department"],
                "Region": e["region"],
                "Site": e["site"],
                "Grade Band": e["grade_band"],
                "Employment Type": e["employment_type"],
                "Is People Manager": "Yes" if e["is_people_manager"] else "No",
                "Is Active": "Active",
            }
            for e in employees
        ]
    )
    df.to_csv(SAMPLES / "employee_roster.csv", index=False)


def write_campaign_registry() -> None:
    df = pd.DataFrame(
        [
            {
                "Campaign ID": c["campaign_id"],
                "Campaign Name": c["campaign_name"],
                "Programme": c["programme"],
                "Objective": c["objective"],
                "Owner Team": c["owner_team"],
                "Start Date": dmy(c["start_date"]),
                "End Date": dmy(c["end_date"]),
                "Mandatory": "Yes" if c["mandatory"] else "No",
                "Target Population": c["target_population"],
                "Target Completion Rate": (
                    f"{c['target_completion_rate']:.0%}"
                    if c["target_completion_rate"]
                    else ""
                ),
                "Target Engagement Rate": (
                    f"{c['target_engagement_rate']:.0%}"
                    if c["target_engagement_rate"]
                    else ""
                ),
            }
            for c in CAMPAIGNS
        ]
    )
    df.to_csv(SAMPLES / "campaign_registry.csv", index=False)


def audience(rng: random.Random, employees: list[dict], campaign_id: str) -> list[dict]:
    """The slice of the workforce a campaign targeted."""
    target = CAMPAIGN_BY_ID[campaign_id]["target_population"]
    if target >= len(employees):
        return list(employees)
    return rng.sample(employees, target)


# --------------------------------------------------------------------------- #
# Channel exports
# --------------------------------------------------------------------------- #


def write_email(rng: random.Random, employees: list[dict]) -> None:
    """CSV, status strings, open/click COUNTS rather than booleans."""
    campaigns = [
        "cyber-aware-2026q1",
        "code-of-conduct-2026",
        "erp-golive-2026",
        "wellbeing-2026q2",
        "townhall-q1-2026",
    ]
    rows: list[dict] = []
    for cid in campaigns:
        c = CAMPAIGN_BY_ID[cid]
        send_day = datetime.combine(c["start_date"], datetime.min.time()).replace(hour=8, minute=30)
        for e in audience(rng, employees, cid):
            factor = DEPT_ENGAGEMENT_FACTOR[e["department"]]
            delivered = rng.random() < 0.972
            opens = 0
            clicks = 0
            first_open = None
            if delivered and rng.random() < min(0.95, 0.45 * factor):
                opens = rng.choices([1, 2, 3, 5], weights=[0.62, 0.24, 0.10, 0.04])[0]
                first_open = send_day + timedelta(
                    hours=rng.choices([1, 4, 20, 52], weights=[0.45, 0.30, 0.18, 0.07])[0],
                    minutes=rng.randint(0, 59),
                )
                if rng.random() < min(0.85, 0.28 * factor):
                    clicks = rng.choices([1, 2, 4], weights=[0.75, 0.20, 0.05])[0]
            rows.append(
                {
                    "Campaign Name": cid,
                    "Recipient Email": e["work_email"],
                    "Department": e["department"],
                    "Send Time": dmy_hm(send_day),
                    "Delivery Status": "Delivered" if delivered else "Bounced",
                    "Bounced": "No" if delivered else "Yes",
                    "Unique Opens": opens,
                    "Unique Clicks": clicks,
                    "First Open": dmy_hm(first_open),
                    "Unsubscribed": "Yes" if (delivered and rng.random() < 0.004) else "No",
                }
            )
    pd.DataFrame(rows).to_csv(SAMPLES / "email_campaigns_2026-01.csv", index=False)


def write_whatsapp(rng: random.Random, employees: list[dict]) -> None:
    """XLSX, Yes/No booleans, read receipts as timestamps."""
    campaigns = ["erp-golive-2026", "wellbeing-2026q2"]
    rows: list[dict] = []
    for cid in campaigns:
        c = CAMPAIGN_BY_ID[cid]
        sent = datetime.combine(c["start_date"], datetime.min.time()).replace(hour=9, minute=0)
        # Not everyone consents to WhatsApp — a realistic subset opts in.
        pool = [e for e in audience(rng, employees, cid) if rng.random() < 0.62]
        for e in pool:
            factor = DEPT_ENGAGEMENT_FACTOR[e["department"]]
            delivered = rng.random() < 0.951
            read_at = None
            replied = False
            button = False
            if delivered and rng.random() < min(0.97, 0.85 * factor):
                read_at = sent + timedelta(minutes=rng.randint(3, 900))
                if rng.random() < min(0.7, 0.24 * factor):
                    if rng.random() < 0.55:
                        button = True
                    else:
                        replied = True
            rows.append(
                {
                    "Broadcast Name": cid,
                    "Message Template": f"{cid}_reminder_v2",
                    "Phone Number": e["mobile"],
                    "Sent At": dmy_hm(sent),
                    "Delivered": "Yes" if delivered else "No",
                    "Read At": dmy_hm(read_at),
                    "Replied": "Yes" if replied else "No",
                    "Button Clicked": "Yes" if button else "No",
                    "Opted Out": "Yes" if (delivered and rng.random() < 0.011) else "No",
                }
            )
    pd.DataFrame(rows).to_excel(
        SAMPLES / "whatsapp_broadcast_2026-03.xlsx", index=False, engine="openpyxl"
    )


def write_lms(rng: random.Random, employees: list[dict]) -> None:
    """CSV, 1/0 flags, percentage strings, tri-state completion status."""
    campaigns = ["cyber-aware-2026q1", "code-of-conduct-2026", "dei-learning-2026"]
    rows: list[dict] = []
    for cid in campaigns:
        c = CAMPAIGN_BY_ID[cid]
        assigned = c["start_date"]
        due = c["end_date"]
        for e in audience(rng, employees, cid):
            factor = DEPT_ENGAGEMENT_FACTOR[e["department"]]
            started = rng.random() < min(0.97, 0.78 * factor)
            completed = started and rng.random() < min(0.96, 0.79 * factor)
            if completed:
                progress = 100
                status = "Completed"
                # Completion clusters just before the due date — the classic
                # compliance-training deadline spike.
                completed_on = due - timedelta(days=rng.choices(
                    [0, 1, 2, 5, 12, 25], weights=[0.20, 0.18, 0.14, 0.20, 0.16, 0.12]
                )[0])
                score = round(rng.gauss(82, 9), 1)
                score = max(45.0, min(100.0, score))
                passed = score >= 70
            elif started:
                progress = rng.choice([10, 25, 40, 55, 70, 85])
                status = "In Progress"
                completed_on = None
                score = ""
                passed = None
            else:
                progress = 0
                status = "Not Started"
                completed_on = None
                score = ""
                passed = None
            rows.append(
                {
                    "Course Code": cid,
                    "Course Name": c["campaign_name"],
                    "Learner ID": e["lms_user_id"],
                    "Department": e["department"],
                    "Assigned On": dmy(assigned),
                    "Started": 1 if started else 0,
                    "Completion Status": status,
                    "Progress %": f"{progress}%",
                    "Score": score,
                    "Assessment Passed": ("Yes" if passed else "No") if passed is not None else "",
                    "Completed On": dmy(completed_on),
                    "Due Date": dmy(due),
                }
            )
    pd.DataFrame(rows).to_csv(SAMPLES / "lms_enrolments_2026-02.csv", index=False)


def write_teams(rng: random.Random, employees: list[dict]) -> None:
    """XLSX, mixed duration formats — hh:mm:ss on some rows, "45 min" on others."""
    campaigns = ["townhall-q1-2026", "dei-learning-2026", "erp-golive-2026"]
    rows: list[dict] = []
    for cid in campaigns:
        c = CAMPAIGN_BY_ID[cid]
        session_minutes = {"townhall-q1-2026": 60, "dei-learning-2026": 90,
                           "erp-golive-2026": 45}[cid]
        start = datetime.combine(c["start_date"], datetime.min.time()).replace(hour=14, minute=0)
        for e in audience(rng, employees, cid):
            factor = DEPT_ENGAGEMENT_FACTOR[e["department"]]
            registered = rng.random() < min(0.9, 0.35 * factor)
            attended = registered and rng.random() < min(0.95, 0.60 * factor)
            if attended:
                # Most stay to the end; a tail drops off early.
                share = rng.choices(
                    [rng.uniform(0.05, 0.3), rng.uniform(0.3, 0.6), rng.uniform(0.6, 1.0)],
                    weights=[0.14, 0.20, 0.66],
                )[0]
                minutes = max(1, round(session_minutes * share))
                joins = rng.choices([1, 2, 3], weights=[0.82, 0.14, 0.04])[0]
            else:
                minutes = 0
                joins = 0
            # Two duration spellings in the same column, on purpose.
            if minutes == 0:
                duration = "00:00:00"
            elif rng.random() < 0.5:
                duration = f"{minutes // 60:02d}:{minutes % 60:02d}:00"
            else:
                duration = f"{minutes} min"
            rows.append(
                {
                    "Meeting Title": c["campaign_name"],
                    "User Principal Name": e["upn"],
                    "Full Name": f"{e['first_name']} {e['last_name']}",
                    "Department": e["department"],
                    "Start Time": dmy_hm(start),
                    "Registration Status": "Registered" if registered else "Not registered",
                    "Attended": "Yes" if attended else "No",
                    "Attendance Duration": duration,
                    "Session Duration": f"{session_minutes} min",
                    "Join Count": joins,
                }
            )
    pd.DataFrame(rows).to_excel(
        SAMPLES / "teams_webinar_attendance_2026-04.xlsx", index=False, engine="openpyxl"
    )


def write_viva(rng: random.Random) -> None:
    """CSV, aggregate per post, ISO dates, no person rows at all."""
    plan = [
        ("cyber-aware-2026q1", date(2026, 1, 14), 4),
        ("wellbeing-2026q2", date(2026, 5, 4), 6),
        ("townhall-q1-2026", date(2026, 4, 15), 3),
    ]
    rows: list[dict] = []
    post_no = 1
    for cid, first_post, count in plan:
        c = CAMPAIGN_BY_ID[cid]
        audience_size = c["target_population"]
        for i in range(count):
            published = first_post + timedelta(days=i * rng.randint(2, 6))
            impressions = int(audience_size * rng.uniform(0.55, 1.4))
            unique_viewers = int(min(audience_size, impressions * rng.uniform(0.35, 0.6)))
            reactions = int(unique_viewers * rng.uniform(0.06, 0.22))
            comments = int(unique_viewers * rng.uniform(0.01, 0.05))
            shares = int(unique_viewers * rng.uniform(0.003, 0.02))
            rows.append(
                {
                    "Community": "Internal Comms",
                    "Campaign ID": cid,
                    "Post ID": f"P{post_no:04d}",
                    "Post Title": f"{c['campaign_name']} update {i + 1}",
                    "Published On": published.isoformat(),
                    "Audience Size": audience_size,
                    "Impressions": impressions,
                    "Unique Viewers": unique_viewers,
                    "Reactions": reactions,
                    "Comments": comments,
                    "Shares": shares,
                }
            )
            post_no += 1
    pd.DataFrame(rows).to_csv(SAMPLES / "viva_engage_posts_2026-05.csv", index=False)


# --------------------------------------------------------------------------- #
# Edge cases — these are the files that prove the validation actually works
# --------------------------------------------------------------------------- #


def write_edge_cases() -> None:
    email = pd.read_csv(SAMPLES / "email_campaigns_2026-01.csv")

    # 1. Required column missing: no delivery status at all.
    email.drop(columns=["Delivery Status"]).head(200).to_csv(
        EDGE / "email_missing_required_column.csv", index=False
    )

    # 2. Nothing to fingerprint against — the loader must ask rather than guess.
    pd.DataFrame(
        {
            "Column A": ["x", "y", "z"],
            "Column B": [1, 2, 3],
            "Date": ["01/01/2026", "02/01/2026", "03/01/2026"],
        }
    ).to_csv(EDGE / "ambiguous_source.csv", index=False)

    # 3. Byte-identical re-drop: must be skipped by the file ledger.
    email.to_csv(EDGE / "email_exact_duplicate.csv", index=False)

    # 4. Superset re-export: same rows plus later activity. A hash ledger alone
    #    misses this, so it is what the monotonic-merge upsert has to get right —
    #    counts must not double, and a recorded open must not regress.
    extra = email.head(150).copy()
    extra["Recipient Email"] = extra["Recipient Email"].str.replace(
        "@example.com", ".new@example.com", regex=False
    )
    superset = pd.concat([email, extra], ignore_index=True)
    superset.to_csv(EDGE / "email_superset_reexport.csv", index=False)


def main() -> None:
    rng = random.Random(SEED)
    SAMPLES.mkdir(parents=True, exist_ok=True)
    EDGE.mkdir(parents=True, exist_ok=True)

    employees = build_employees(rng)
    write_roster(employees)
    write_campaign_registry()
    write_email(rng, employees)
    write_whatsapp(rng, employees)
    write_lms(rng, employees)
    write_teams(rng, employees)
    write_viva(rng)
    write_edge_cases()

    print(f"Wrote sample exports to {SAMPLES}")
    for path in sorted(SAMPLES.rglob("*")):
        if path.is_file():
            rel = path.relative_to(SAMPLES)
            rows = "?"
            try:
                if path.suffix == ".csv":
                    rows = str(len(pd.read_csv(path)))
                elif path.suffix == ".xlsx":
                    rows = str(len(pd.read_excel(path)))
            except Exception:  # pragma: no cover - reporting only
                pass
            print(f"  {rel!s:45s} {rows:>7s} rows  {path.stat().st_size / 1024:7.1f} KB")


if __name__ == "__main__":
    main()
