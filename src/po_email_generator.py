#!/usr/bin/env python3
"""
PO Email Generator
------------------
Reads your monthly PO tracking Excel, matches each PO to its reviewer
via the "Program Reviewers" sheet, then prints ready-to-send email drafts.

Usage:
    python src/po_email_generator.py <path_to_excel>

    # Or drop the Excel in this folder and just run:
    python src/po_email_generator.py

Expected Excel structure:
    Sheet "06-2026" (or any MM-YYYY sheet):
        Column A: PO Number   (e.g. 77033)
        Column B: Program Code (e.g. ACFlexMHSA01.1)

    Sheet "Program Reviewers":
        Column A: Program Code
        Column B: Reviewer Name
"""

import sys
import os
import pandas as pd
from collections import defaultdict
from datetime import datetime


# ---------------------------------------------------------------------------
# Email template — edit this to match your tone
# ---------------------------------------------------------------------------
EMAIL_SUBJECT = "Please Review POs – {month_year}"

EMAIL_BODY = """\
Hi {reviewer},

Please review the following Purchase Orders for {month_year}:

  POs: {po_list}

Please process these at your earliest convenience. Let me know if you have
any questions.

Thank you!
"""


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def find_excel(provided_path=None):
    if provided_path:
        return provided_path
    for f in os.listdir("."):
        if f.endswith((".xlsx", ".xls")):
            return f
    sys.exit("No Excel file found. Pass the path as an argument: python src/po_email_generator.py my_file.xlsx")


def detect_po_sheet(sheet_names):
    """Pick the sheet that looks like the current month (MM-YYYY)."""
    current = datetime.now().strftime("%m-%Y")
    if current in sheet_names:
        return current
    # Fall back: first sheet that isn't "Program Reviewers" or "Sheet*"
    for name in sheet_names:
        if "reviewer" not in name.lower() and not name.lower().startswith("sheet"):
            return name
    return sheet_names[0]


def load_reviewer_map(xl, sheet="Program Reviewers"):
    df = xl.parse(sheet, header=0)
    mapping = {}
    for _, row in df.iterrows():
        code = str(row.iloc[0]).strip()
        reviewer = str(row.iloc[1]).strip()
        if code and code.lower() != "nan" and reviewer.lower() != "nan":
            mapping[code] = reviewer
    return mapping


def load_po_rows(xl, sheet_name):
    df = xl.parse(sheet_name, header=None)
    rows = []
    for _, row in df.iterrows():
        po_num = str(row.iloc[0]).strip()
        program = str(row.iloc[1]).strip() if len(row) > 1 else ""
        # Skip header / empty rows
        if not po_num or po_num.lower() == "nan":
            continue
        # PO numbers are numeric
        if not po_num.replace(".", "").isdigit():
            continue
        rows.append((po_num, program))
    return rows


def group_by_reviewer(po_rows, reviewer_map):
    grouped = defaultdict(list)
    unmatched = []
    for po_num, program in po_rows:
        reviewer = reviewer_map.get(program)
        if reviewer:
            grouped[reviewer].append(po_num)
        else:
            unmatched.append((po_num, program))
    return grouped, unmatched


def build_emails(grouped, month_year):
    emails = {}
    for reviewer, po_list in sorted(grouped.items()):
        po_list_sorted = sorted(po_list, key=lambda x: int(x.split(".")[0]))
        emails[reviewer] = {
            "subject": EMAIL_SUBJECT.format(month_year=month_year),
            "body": EMAIL_BODY.format(
                reviewer=reviewer,
                month_year=month_year,
                po_list=", ".join(po_list_sorted),
            ),
            "pos": po_list_sorted,
        }
    return emails


def save_and_print(emails, output_dir="output/emails"):
    os.makedirs(output_dir, exist_ok=True)
    sep = "=" * 60

    for reviewer, data in emails.items():
        print(f"\n{sep}")
        print(f"TO:      {reviewer}")
        print(f"SUBJECT: {data['subject']}")
        print("-" * 60)
        print(data["body"])

        safe = reviewer.replace(" ", "_")
        path = os.path.join(output_dir, f"{safe}.txt")
        with open(path, "w") as f:
            f.write(f"To: {reviewer}\n")
            f.write(f"Subject: {data['subject']}\n")
            f.write("-" * 60 + "\n")
            f.write(data["body"])
        print(f"[Saved → {path}]")

    print(f"\n{sep}")
    print(f"Done: {len(emails)} email draft(s) generated in {output_dir}/")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    excel_path = find_excel(sys.argv[1] if len(sys.argv) > 1 else None)
    print(f"Reading: {excel_path}")

    xl = pd.ExcelFile(excel_path)
    po_sheet = detect_po_sheet(xl.sheet_names)
    print(f"PO sheet detected: {po_sheet}")

    # Month label for emails, e.g. "06-2026" → "June 2026"
    try:
        dt = datetime.strptime(po_sheet, "%m-%Y")
        month_year = dt.strftime("%B %Y")
    except ValueError:
        month_year = po_sheet

    reviewer_map = load_reviewer_map(xl)
    print(f"Reviewer mappings loaded: {len(reviewer_map)}")

    po_rows = load_po_rows(xl, po_sheet)
    print(f"PO rows found: {len(po_rows)}")

    grouped, unmatched = group_by_reviewer(po_rows, reviewer_map)

    if unmatched:
        print(f"\nWARNING — {len(unmatched)} PO(s) not matched to any reviewer:")
        for po, code in unmatched:
            print(f"  PO {po}  (program: '{code}' — not in Program Reviewers sheet)")

    emails = build_emails(grouped, month_year)
    save_and_print(emails)


if __name__ == "__main__":
    main()
