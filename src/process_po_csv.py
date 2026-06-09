#!/usr/bin/env python3
"""
把 nexonia_full_scraper 抓到的 CSV 处理成：
  1. 一份 Excel tracking 表（存档/给你看，值都直接填好）
  2. 每个 reviewer 一封 email 草稿（复制即可发）

映射来自你的主文件 PO_Review*.xlsx 的 "Program Reviewers" 这个 tab
（Program Code / 1st level / 2nd Level）。大小写不敏感，一个 code 出现多条
且不一致时取最后一条并报告冲突。

用法：
    python src/process_po_csv.py output/Nexonia_POs_2026-06.csv
    python src/process_po_csv.py output/Nexonia_POs_2026-06.csv PO_Review_FY2425.xlsx --level 1

默认按 2nd level 分组发 email（--level 1 改成第一级）。
"""

import sys
import os
import re
import glob
import csv as csvlib
import pandas as pd
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from collections import defaultdict
from datetime import datetime


# ── 1. 加载抓取的 CSV ───────────────────────────────────────────────────────
def load_csv(csv_path):
    df = pd.read_csv(csv_path, dtype=str).fillna("")
    df.columns = [c.strip().upper().replace(" ", "_") for c in df.columns]

    if "PO_NUMBER" not in df.columns:
        for alt in ("PO", "NUMBER"):
            if alt in df.columns:
                df = df.rename(columns={alt: "PO_NUMBER"})
                break
        else:
            sys.exit(f"CSV 里找不到 PO 号列。实际列名：{list(df.columns)}")

    if "COST_CENTER" in df.columns and "PROGRAM_CODE" not in df.columns:
        df = df.rename(columns={"COST_CENTER": "PROGRAM_CODE"})

    if "PROGRAM_CODE" in df.columns:
        df["PROGRAM_CODE"] = df["PROGRAM_CODE"].apply(
            lambda v: str(v).split(";")[0].strip() if v else ""
        )
    else:
        df["PROGRAM_CODE"] = ""
    return df


# ── 2. 读取映射（Program Code → 1st / 2nd reviewer）─────────────────────────
def norm(code):
    return str(code).strip().lower()


def find_reviewer_file(explicit=None):
    if explicit:
        return explicit
    pats = ["PO_Review*.xlsx", "PO_Review*.xls",
            "output/PO_Review*.xlsx", "program_reviewers.csv"]
    for pat in pats:
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]
    return None


def load_reviewer_map(path):
    """返回 (mapping, conflicts)
       mapping[norm_code] = {'code':原始, 'l1':一级, 'l2':二级}
       conflicts = [(code, [(l1,l2), ...]), ...]   多条且不一致的
    """
    rows = []   # (code, l1, l2) 按出现顺序

    if path.lower().endswith((".xlsx", ".xls")):
        wb = openpyxl.load_workbook(path, data_only=True)
        sheet = next((s for s in wb.sheetnames
                      if "program" in s.lower() and "review" in s.lower()), None)
        if sheet is None:
            sys.exit(f"在 {path} 里找不到 'Program Reviewers' 这个 tab。")
        ws = wb[sheet]
        for r in ws.iter_rows(values_only=True):
            if not r or not r[0]:
                continue
            code = str(r[0]).strip()
            low = code.lower()
            if low in ("program", "po que approval reference sheet") or low.startswith("po que"):
                continue
            l1 = str(r[1]).strip() if len(r) > 1 and r[1] else ""
            l2 = str(r[2]).strip() if len(r) > 2 and r[2] else ""
            rows.append((code, l1, l2))
    else:  # csv: program_code,reviewer  (单级，放到 l1=l2)
        with open(path, newline="", encoding="utf-8-sig") as f:
            for row in csvlib.DictReader(f):
                code = (row.get("program_code") or "").strip()
                rev = (row.get("reviewer") or "").strip()
                if code:
                    rows.append((code, rev, rev))

    grouped = defaultdict(list)
    for code, l1, l2 in rows:
        grouped[norm(code)].append((code, l1, l2))

    mapping, conflicts = {}, []
    for key, entries in grouped.items():
        last = entries[-1]                       # 取最后一条
        mapping[key] = {"code": last[0], "l1": last[1], "l2": last[2]}
        distinct = {(e[1], e[2]) for e in entries}
        if len(distinct) > 1:
            conflicts.append((last[0], [(e[1], e[2]) for e in entries]))
    return mapping, conflicts


def reviewer_for(code, mapping, level):
    """level=2 优先二级，空则退一级；level=1 优先一级，空则退二级。"""
    m = mapping.get(norm(code))
    if not m:
        return ""
    if level == 2:
        return m["l2"] or m["l1"]
    return m["l1"] or m["l2"]


# ── 3. 写 Excel tracking 表（纯值）──────────────────────────────────────────
def write_excel(df, mapping, level, excel_path, po_sheet):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = po_sheet

    headers = ["PO Number", "Program Code", "Reviewer", "Status",
               "Creation Date", "Vendor", "Total Amount", "Memo"]
    widths = [11, 16, 16, 10, 14, 40, 13, 50]
    for col, (h, w) in enumerate(zip(headers, widths), 1):
        c = ws.cell(1, col, h)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F3864")
        c.alignment = Alignment(horizontal="center")
        ws.column_dimensions[openpyxl.utils.get_column_letter(col)].width = w

    for i, (_, row) in enumerate(df.iterrows(), start=2):
        code = row.get("PROGRAM_CODE", "")
        ws.cell(i, 1, row.get("PO_NUMBER", ""))
        ws.cell(i, 2, code)
        ws.cell(i, 3, reviewer_for(code, mapping, level))
        ws.cell(i, 5, row.get("CREATION_DATE", ""))
        ws.cell(i, 6, row.get("VENDOR", ""))
        ws.cell(i, 7, row.get("TOTAL_AMOUNT", ""))
        ws.cell(i, 8, row.get("MEMO", ""))
    ws.freeze_panes = "A2"

    os.makedirs(os.path.dirname(excel_path) or ".", exist_ok=True)
    wb.save(excel_path)
    print(f"✅ Excel 已保存：{excel_path}（{len(df)} 条 PO）")


# ── 4. 生成 email 草稿 ──────────────────────────────────────────────────────
def generate_emails(df, mapping, level, month_year, output_dir="output/emails"):
    grouped = defaultdict(list)
    missing = defaultdict(list)        # 找不到 code 的 PO
    for _, row in df.iterrows():
        po = str(row.get("PO_NUMBER", "")).strip()
        code = row.get("PROGRAM_CODE", "")
        if not po:
            continue
        rev = reviewer_for(code, mapping, level)
        if rev:
            grouped[rev].append(po)
        else:
            missing[code].append(po)

    os.makedirs(output_dir, exist_ok=True)
    sep = "=" * 60
    if grouped:
        print(f"\n{sep}")
        for rev, pos in sorted(grouped.items()):
            pos_sorted = sorted(pos, key=lambda x: int(re.sub(r"\D", "", x) or 0))
            po_str = ", ".join(pos_sorted)
            subject = f"Please Review POs – {month_year}"
            body = f"Hi {rev},\n\nPlease review the following POs: {po_str}\n\nThank you!"
            print(f"TO:      {rev}\nSUBJECT: {subject}\n{'-'*60}\n{body}")
            safe = re.sub(r"[^\w]+", "_", rev).strip("_")
            with open(f"{output_dir}/{safe}.txt", "w", encoding="utf-8") as f:
                f.write(f"To: {rev}\nSubject: {subject}\n{'-'*60}\n{body}\n")
            print(f"\n[已保存 → {output_dir}/{safe}.txt]\n{sep}")

    if missing:
        print(f"\n⚠️  以下 Program Code 在映射表里找不到（对应 PO 未进任何 email）：")
        for code, pos in sorted(missing.items()):
            print(f"    {code:18} → PO {', '.join(sorted(pos))}")
        print("    请在 PO_Review 文件的 Program Reviewers tab 里补上这些 code。")


# ── 主流程 ─────────────────────────────────────────────────────────────────
def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    level = 1 if "--level" in sys.argv and "1" in sys.argv[sys.argv.index("--level") + 1:] else 2

    if not args:
        cands = sorted(glob.glob("output/Nexonia_POs*.csv"))
        if not cands:
            sys.exit("用法：python src/process_po_csv.py output/Nexonia_POs_2026-06.csv")
        csv_path = cands[-1]
        print(f"自动找到 CSV：{csv_path}")
    else:
        csv_path = args[0]

    reviewer_file = find_reviewer_file(args[1] if len(args) > 1 else None)
    if not reviewer_file:
        sys.exit("找不到映射文件。请把 PO_Review_*.xlsx 放到项目目录下，再跑一次。")
    print(f"映射文件：{reviewer_file}（按 {'2nd' if level==2 else '1st'} level 分组）")

    df = load_csv(csv_path)
    print(f"CSV 加载成功：{len(df)} 条 PO")

    mapping, conflicts = load_reviewer_map(reviewer_file)
    print(f"映射加载：{len(mapping)} 个 Program Code")

    # 只报告本次 PO 实际用到的 code 的冲突
    used = {norm(c) for c in df["PROGRAM_CODE"].tolist() if c}
    relevant = [(code, v) for code, v in conflicts if norm(code) in used]
    if relevant:
        print(f"\n⚠️  本次有 {len(relevant)} 个 code 在表里多条不一致（已取最后一条，请核对）：")
        for code, variants in relevant:
            vs = " | ".join(f"1st={a or '-'},2nd={b or '-'}" for a, b in variants)
            print(f"    {code:18} → {vs}")

    m = re.search(r"(\d{4})-(\d{2})", csv_path)
    po_sheet = f"{m.group(2)}-{m.group(1)}" if m else datetime.now().strftime("%m-%Y")
    try:
        month_year = datetime.strptime(po_sheet, "%m-%Y").strftime("%B %Y")
    except ValueError:
        month_year = po_sheet

    write_excel(df, mapping, level, f"output/PO_Tracking_{po_sheet}.xlsx", po_sheet)
    generate_emails(df, mapping, level, month_year)


if __name__ == "__main__":
    main()
