#!/usr/bin/env python3
"""
把 Nexonia 导出的 CSV 导入 Excel tracking 表，并生成 reviewer email 草稿。

用法：
    python src/process_po_csv.py Nexonia_POs_2026-06.csv
"""

import sys
import os
import re
import pandas as pd
import openpyxl
from openpyxl.styles import Font, PatternFill
from collections import defaultdict
from datetime import datetime


# ── 1. 加载 CSV ────────────────────────────────────────────────────────────

def load_csv(csv_path):
    df = pd.read_csv(csv_path, dtype=str).fillna("")
    # 统一列名（容忍大小写、空格差异）
    df.columns = [c.strip().upper().replace(" ", "_") for c in df.columns]
    required = "PO_NUMBER"
    if required not in df.columns:
        # 兼容 "NUMBER" 列名
        if "NUMBER" in df.columns:
            df = df.rename(columns={"NUMBER": "PO_NUMBER"})
        else:
            sys.exit(f"CSV 里找不到 PO 号列。实际列名：{list(df.columns)}")
    return df


# ── 2. 写入 Excel ──────────────────────────────────────────────────────────

def write_to_excel(df, excel_path, po_sheet):
    """把 CSV 数据写入 Excel 的月份 sheet，保留已有的 Program Code / Reviewer 列。"""
    if os.path.exists(excel_path):
        wb = openpyxl.load_workbook(excel_path)
    else:
        wb = openpyxl.Workbook()
        wb.active.title = po_sheet

    if po_sheet not in wb.sheetnames:
        wb.create_sheet(po_sheet)

    ws = wb[po_sheet]

    # 检查是否有已有数据（如果用户之前手动填了 Program Code，保留）
    existing = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[0] and str(row[0]).strip().isdigit():
            po = str(row[0]).strip()
            existing[po] = row  # 保存整行

    # 写表头（如果空表）
    if ws.max_row <= 1:
        headers = ["PO Number", "Program Code", "PO, Program", "Reviewer", "Status",
                   "Creation Date", "Vendor", "Total Amount", "Memo"]
        for col, h in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=h)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="1F3864")

    # 写 PO 数据，从第 2 行开始
    col_map = {c: i for i, c in enumerate(
        [c.strip().upper().replace(" ", "_") for c in
         ["PO_NUMBER", "CREATION_DATE", "VENDOR", "TOTAL_AMOUNT", "MEMO"]]
    )}

    start_row = 2
    for i, (_, row) in enumerate(df.iterrows()):
        r = start_row + i
        po = str(row.get("PO_NUMBER", "")).strip()

        ws.cell(r, 1, po)
        ws.cell(r, 6, row.get("CREATION_DATE", ""))
        ws.cell(r, 7, row.get("VENDOR", ""))
        ws.cell(r, 8, row.get("TOTAL_AMOUNT", ""))
        ws.cell(r, 9, row.get("MEMO", ""))

        # 如果之前有 Program Code / Reviewer，保留
        if po in existing:
            old = existing[po]
            if old[1]:  # Program Code
                ws.cell(r, 2, old[1])
            if old[3]:  # Reviewer
                ws.cell(r, 4, old[3])
            if old[4]:  # Status
                ws.cell(r, 5, old[4])
        else:
            # 新 PO：Reviewer 用 VLOOKUP 自动填充
            ws.cell(r, 3, f'=IF(B{r}="","",A{r}&", "&B{r})')
            ws.cell(r, 4,
                f'=IF(B{r}="","",IFERROR(VLOOKUP(B{r},\'Program Reviewers\'!$A:$B,2,0),"no result"))')

    wb.save(excel_path)
    print(f"✅ Excel 已保存：{excel_path}（{len(df)} 条 PO）")
    return excel_path


# ── 3. 生成 email 草稿（需要 Reviewer 列已填充）─────────────────────────

def generate_emails(excel_path, po_sheet, output_dir="output/emails"):
    xl = pd.ExcelFile(excel_path)
    df = xl.parse(po_sheet, header=0, dtype=str).fillna("")
    df.columns = [str(c).strip() for c in df.columns]

    # 找 PO 号列 和 Reviewer 列
    po_col  = next((c for c in df.columns if "PO" in c.upper() and "NUMBER" in c.upper()), df.columns[0])
    rev_col = next((c for c in df.columns if "REVIEWER" in c.upper()), None)

    if not rev_col:
        print("⚠️  Excel 里没有 Reviewer 列，跳过 email 生成。先在 Column B 填 Program Code。")
        return

    grouped = defaultdict(list)
    for _, row in df.iterrows():
        po  = str(row[po_col]).strip()
        rev = str(row[rev_col]).strip()
        if po.isdigit() and rev and rev.lower() not in ("", "nan", "no result"):
            grouped[rev].append(po)

    if not grouped:
        print("⚠️  还没有 Reviewer 数据，先在 Excel 的 Program Code 列填写对应 code。")
        return

    try:
        dt = datetime.strptime(po_sheet, "%m-%Y")
        month_year = dt.strftime("%B %Y")
    except ValueError:
        month_year = po_sheet

    os.makedirs(output_dir, exist_ok=True)
    print(f"\n{'='*60}")
    for rev, pos in sorted(grouped.items()):
        pos_sorted = sorted(pos, key=int)
        subject = f"Please Review POs – {month_year}"
        body = (
            f"Hi {rev},\n\n"
            f"Please review the following Purchase Orders for {month_year}:\n\n"
            f"  POs: {', '.join(pos_sorted)}\n\n"
            f"Please process these at your earliest convenience.\n\nThank you!"
        )
        print(f"TO:      {rev}")
        print(f"SUBJECT: {subject}")
        print("-" * 60)
        print(body)
        print()

        safe = rev.replace(" ", "_")
        with open(f"{output_dir}/{safe}.txt", "w") as f:
            f.write(f"To: {rev}\nSubject: {subject}\n{'-'*60}\n{body}")
        print(f"[Saved → {output_dir}/{safe}.txt]")
        print()


# ── 主流程 ─────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        csvs = [f for f in os.listdir(".") if f.startswith("Nexonia_POs") and f.endswith(".csv")]
        if not csvs:
            sys.exit("用法：python src/process_po_csv.py <CSV文件路径>")
        csv_path = sorted(csvs)[-1]
        print(f"自动找到 CSV：{csv_path}")
    else:
        csv_path = sys.argv[1]

    df = load_csv(csv_path)
    print(f"CSV 加载成功：{len(df)} 条 PO")

    # 从文件名提取月份，e.g. Nexonia_POs_2026-06.csv → 06-2026
    m = re.search(r"(\d{4})-(\d{2})", csv_path)
    po_sheet   = f"{m.group(2)}-{m.group(1)}" if m else datetime.now().strftime("%m-%Y")
    excel_path = f"output/PO_Tracking_{po_sheet}.xlsx"

    write_to_excel(df, excel_path, po_sheet)

    print("\n下一步：")
    print(f"  1. 打开 {excel_path}")
    print(f"  2. 在 Column B 填入每个 PO 的 Program Code（Column D Reviewer 会自动显示）")
    print(f"  3. 再次运行此脚本，自动生成 email 草稿\n")

    generate_emails(excel_path, po_sheet)


if __name__ == "__main__":
    main()
