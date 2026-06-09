#!/usr/bin/env python3
"""
审批后 PDF 工作流
=================
对每个已审批的 PO，自动：
  1. 打开 Nexonia PO 弹窗，存成 PDF（命名：77033, ACBFHome01.pdf）
  2. 下载弹窗里所有附件（backup PDF）
  3. 把 PO PDF + 附件 PDF 合并成一个最终文件

安装依赖（只需一次）：
    pip install playwright Pillow pypdf
    playwright install chromium

用法：
    python src/po_pdf_workflow.py 77033 77034 77035
    python src/po_pdf_workflow.py --all                      # 当月 CSV 里所有 PO
    python src/po_pdf_workflow.py --csv output/Nexonia_POs_2026-06.csv 77033 77034
    python src/po_pdf_workflow.py --scan 77033               # 只扫描附件，不下载（调试用）

输出目录：output/pdfs/
  output/pdfs/77033, ACBFHome01.pdf   ← 合并后最终文件
  output/pdfs/temp/77033/             ← 中间文件（可删）
"""

import glob
import io
import json
import os
import re
import sys
import time
from pathlib import Path

# ── 导入共用工具（与 nexonia_full_scraper 共享）──────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from nexonia_full_scraper import (
        LEAF_COLLECTOR_JS, locate_columns,
        close_modal, SESSION_DIR, NEXONIA_BASE,
    )
except ImportError as e:
    sys.exit(f"❌ 无法导入 nexonia_full_scraper：{e}")

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    sys.exit("❌ 缺少 Playwright。请先：pip install playwright && playwright install chromium")

try:
    import pandas as pd
    _HAVE_PANDAS = True
except ImportError:
    _HAVE_PANDAS = False

OUTPUT_DIR = "output/pdfs"


# ─────────────────────────────────────────────────────────────────────────────
# 加载 PO → Program Code 映射（从已抓的 CSV）
# ─────────────────────────────────────────────────────────────────────────────
def load_code_map(csv_path=None):
    if csv_path is None:
        cands = sorted(glob.glob("output/Nexonia_POs*.csv"))
        if not cands:
            return {}
        csv_path = cands[-1]
        print(f"自动找到 CSV：{csv_path}")

    result = {}
    try:
        import csv as csvlib
        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            reader = csvlib.DictReader(f)
            for row in reader:
                # 统一列名
                keys = {k.strip().upper().replace(" ", "_"): v for k, v in row.items()}
                po = (keys.get("PO") or keys.get("PO_NUMBER") or "").strip()
                raw = (keys.get("COST_CENTER") or keys.get("PROGRAM_CODE") or "").strip()
                if not po:
                    continue
                # "CODE - CODE - Name" → 取第一段
                code = re.split(r"\s+[-–—]\s+", raw)[0].strip() if raw else "UNKNOWN"
                if code:
                    result[po] = code
    except Exception as e:
        print(f"⚠️  加载 CSV 失败：{e}")
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 找含 PO 列表的 frame
# ─────────────────────────────────────────────────────────────────────────────
def find_target(page):
    for t in [page] + list(page.frames):
        try:
            data = t.evaluate(LEAF_COLLECTOR_JS)
            leaves = data["leaves"]
            colx, _ = locate_columns(leaves)
            has_num = any(re.fullmatch(r"\d{4,6}", lf["t"]) for lf in leaves)
            if "NUMBER" in colx and "VENDOR" in colx and has_num:
                return t
        except Exception:
            pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 把当前页面存为 PDF
# 优先用 page.pdf()（新版 Chromium 在 headful 也可用）；
# 失败则截图 → Pillow → PDF
# ─────────────────────────────────────────────────────────────────────────────
def save_pdf(page, output_path):
    try:
        page.pdf(
            path=output_path,
            format="Letter",
            print_background=True,
            margin={"top": "0.5in", "bottom": "0.5in",
                    "left": "0.5in", "right": "0.5in"},
        )
        return True
    except Exception as err:
        if "headless" not in str(err).lower() and "pdf" not in str(err).lower():
            raise
    # 退路：截图 → Pillow PDF
    return _screenshot_to_pdf(page, output_path)


def _screenshot_to_pdf(page, output_path):
    try:
        from PIL import Image
    except ImportError:
        sys.exit(
            "❌ 当前模式需要 Pillow：pip install Pillow\n"
            "   （或者在 headless 模式下运行以直接生成 PDF）"
        )
    png = page.screenshot(full_page=True)
    img = Image.open(io.BytesIO(png))
    if img.mode == "RGBA":
        img = img.convert("RGB")
    img.save(output_path, "PDF", resolution=150.0)
    return True


# ─────────────────────────────────────────────────────────────────────────────
# 附件扫描 JS
# ─────────────────────────────────────────────────────────────────────────────
FIND_ATTACHMENTS_JS = r"""
() => {
    const results = [];
    const seen = new Set();

    function add(href, text, el) {
        href = (href || '').trim();
        if (!href || seen.has(href)) return;
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) return;
        seen.add(href);
        results.push({
            href,
            text: (text || '').replace(/\s+/g, ' ').trim().slice(0, 120),
            x: Math.round(r.left + r.width / 2),
            y: Math.round(r.top  + r.height / 2),
        });
    }

    // 1. <a> 标签：href 含文件扩展名 / download 关键字 / attachment 关键字
    document.querySelectorAll('a[href]').forEach(a => {
        const h = a.href || '';
        if (/\.(pdf|xlsx?|docx?|png|jpe?g|gif|zip|tiff?)(\?|$)/i.test(h)
            || /download|attachment|document|file/i.test(h)
            || a.hasAttribute('download')) {
            add(h, a.textContent, a);
        }
    });

    // 2. 任何元素带 data-href / data-url / data-src 且像文件
    document.querySelectorAll('[data-href],[data-url],[data-src]').forEach(el => {
        const h = el.getAttribute('data-href')
                || el.getAttribute('data-url')
                || el.getAttribute('data-src') || '';
        if (/\.(pdf|xlsx?|docx?|png|jpe?g|zip)(\?|$)/i.test(h)
            || /download|attachment/i.test(h)) {
            add(h, el.textContent, el);
        }
    });

    // 3. 按钮 / 可点击元素文字含 download / attachment / view file 等
    document.querySelectorAll('button,[role="button"],input[type="button"],input[type="submit"]').forEach(btn => {
        const t = (btn.textContent || btn.value || '').toLowerCase();
        const ti = (btn.title || btn.getAttribute('aria-label') || '').toLowerCase();
        if (/download|attach|view\s*(file|doc|pdf)|open\s*(file|doc)/i.test(t + ' ' + ti)) {
            const r = btn.getBoundingClientRect();
            if (r.width > 0 && r.height > 0) {
                results.push({
                    href: '',
                    text: (btn.textContent || btn.value || '').trim().slice(0, 80),
                    x: Math.round(r.left + r.width / 2),
                    y: Math.round(r.top  + r.height / 2),
                    isButton: true,
                });
            }
        }
    });

    return results;
}
"""


# ─────────────────────────────────────────────────────────────────────────────
# 下载附件
# ─────────────────────────────────────────────────────────────────────────────
def download_attachments(page, target, po_num, download_dir, scan_only=False):
    """
    扫描弹窗里的附件并下载到 download_dir。
    scan_only=True 时只打印、不下载（调试用）。
    返回已下载文件路径列表。
    """
    time.sleep(0.6)
    items = target.evaluate(FIND_ATTACHMENTS_JS)

    if not items:
        print("      （未找到附件）")
        return []

    if scan_only:
        print(f"      扫描到 {len(items)} 个附件候选：")
        for it in items:
            flag = "[按钮]" if it.get("isButton") else f"[链接] {it['href'][:80]}"
            print(f"        {flag}  文字：{it['text']}")
        return []

    os.makedirs(download_dir, exist_ok=True)
    downloaded = []

    for idx, it in enumerate(items, 1):
        href = it.get("href", "")
        text = it.get("text", f"attachment_{idx}")
        is_btn = it.get("isButton", False)

        # 猜文件名
        m = re.search(r"([^/?#]+\.(pdf|xlsx?|docx?|png|jpe?g|gif|zip|tiff?))(\?|$)",
                      href, re.I)
        guessed_name = m.group(1) if m else f"{po_num}_attachment_{idx}.pdf"
        dest = os.path.join(download_dir, guessed_name)

        try:
            with page.expect_download(timeout=15_000) as dl_info:
                if is_btn:
                    page.mouse.click(it["x"], it["y"])
                else:
                    # 在新标签页触发下载避免跳走
                    page.evaluate(f"window.open({json.dumps(href)}, '_blank')")

            dl = dl_info.value
            final_name = dl.suggested_filename or guessed_name
            dest = os.path.join(download_dir, final_name)
            dl.save_as(dest)
            downloaded.append(dest)
            print(f"      ✅ 附件 {idx}：{final_name}")

        except Exception as e:
            print(f"      ⚠️  附件 {idx} 下载失败（{text[:40]}）：{e}")

    return downloaded


# ─────────────────────────────────────────────────────────────────────────────
# 合并 PDF 列表 → 单个文件
# ─────────────────────────────────────────────────────────────────────────────
def combine_pdfs(pdf_files, output_path):
    valid = [f for f in pdf_files if os.path.exists(f)
             and f.lower().endswith(".pdf")]
    if not valid:
        print("      没有有效 PDF 可合并")
        return False

    if len(valid) == 1:
        import shutil
        shutil.copy2(valid[0], output_path)
        return True

    try:
        from pypdf import PdfWriter
    except ImportError:
        try:
            from PyPDF2 import PdfWriter
        except ImportError:
            print("⚠️  未安装 pypdf，无法合并：pip install pypdf")
            import shutil
            shutil.copy2(valid[0], output_path)
            print(f"      （已复制第一个 PDF 作为占位）")
            return False

    writer = PdfWriter()
    for p in valid:
        try:
            writer.append(p)
        except Exception as e:
            print(f"      ⚠️  跳过损坏文件 {os.path.basename(p)}：{e}")

    with open(output_path, "wb") as f:
        writer.write(f)
    return True


# ─────────────────────────────────────────────────────────────────────────────
# 单个 PO 的完整处理
# ─────────────────────────────────────────────────────────────────────────────
def process_po(page, target, po_num, program_code, base_dir, scan_only=False):
    print(f"\n  ── PO {po_num}  [{program_code}] ──")

    safe_name = f"{po_num}, {program_code}"
    tmp_dir = os.path.join(base_dir, "temp", po_num)
    os.makedirs(tmp_dir, exist_ok=True)
    final_path = os.path.join(base_dir, f"{safe_name}.pdf")

    # 1. 打开弹窗
    try:
        target.get_by_text(po_num, exact=True).first.click(timeout=6_000)
        try:
            target.get_by_text("Order Details", exact=False).first.wait_for(timeout=8_000)
        except PWTimeout:
            pass
        time.sleep(1.2)
    except Exception as e:
        print(f"      ❌ 无法打开弹窗：{e}")
        return False

    all_pdfs = []

    if not scan_only:
        # 2. 保存页面 PDF
        po_pdf = os.path.join(tmp_dir, f"{safe_name}_page.pdf")
        try:
            save_pdf(page, po_pdf)
            all_pdfs.append(po_pdf)
            print(f"      ✅ 页面 PDF 已保存")
        except Exception as e:
            print(f"      ⚠️  页面 PDF 失败：{e}")

    # 3. 扫描 + 下载附件
    att_dir = os.path.join(tmp_dir, "attachments")
    downloaded = download_attachments(
        page, target, po_num, att_dir, scan_only=scan_only
    )
    att_pdfs = [f for f in downloaded if f.lower().endswith(".pdf")]
    non_pdf  = [f for f in downloaded if not f.lower().endswith(".pdf")]

    if non_pdf:
        print(f"      （非 PDF 附件已存入 {att_dir}/：" +
              ", ".join(os.path.basename(f) for f in non_pdf) + "）")

    # 4. 合并
    if not scan_only:
        all_pdfs.extend(att_pdfs)
        if combine_pdfs(all_pdfs, final_path):
            n_att = len(att_pdfs)
            print(f"      ✅ 合并完成（PO页面 + {n_att} 个附件）→ {final_path}")
        else:
            print(f"      ⚠️  合并失败，中间文件在 {tmp_dir}/")

    close_modal(page, target)
    return True


# ─────────────────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────────────────
def main():
    raw = sys.argv[1:]

    # 解析参数
    process_all = "--all" in raw
    scan_only   = "--scan" in raw
    csv_path    = None
    po_nums     = []

    i = 0
    while i < len(raw):
        a = raw[i]
        if a in ("--all", "--scan"):
            pass
        elif a == "--csv" and i + 1 < len(raw):
            csv_path = raw[i + 1]
            i += 1
        elif re.fullmatch(r"\d{4,6}", a):
            po_nums.append(a)
        i += 1

    code_map = load_code_map(csv_path)

    if process_all:
        po_nums = sorted(code_map.keys(), key=lambda x: int(x))
        if not po_nums:
            sys.exit("❌ CSV 里找不到 PO 记录。请先跑 nexonia_full_scraper.py。")
        print(f"--all：处理 {len(po_nums)} 个 PO")

    if not po_nums:
        print(__doc__)
        sys.exit(1)

    mode_str = "【仅扫描附件，不下载】" if scan_only else ""
    print(f"\n{'='*60}")
    print(f"处理 {len(po_nums)} 个 PO  {mode_str}")
    for po in po_nums:
        code = code_map.get(po, "UNKNOWN")
        print(f"  PO {po:6}  →  {code}")
    if "UNKNOWN" in [code_map.get(p, "UNKNOWN") for p in po_nums]:
        print("  ⚠️  部分 PO 在 CSV 里找不到 Code，将用 UNKNOWN 命名。")
        print("     先跑 nexonia_full_scraper.py 抓取数据可修复。")
    print("=" * 60)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=SESSION_DIR,
            headless=False,
            slow_mo=30,
            viewport=None,
            args=["--start-maximized"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        try:
            page.goto(NEXONIA_BASE + "/assistant/home.do",
                      wait_until="domcontentloaded")
        except Exception:
            pass

        print("\n浏览器已打开。")
        print("请进入 PO / Approvals 列表页，确保所有 PO 都在页面上可见。")
        input("准备好后按 Enter 开始 ▶ ")

        target = find_target(page)
        if target is None:
            print("❌ 没找到 PO 列表。请确认当前在 Approvals 页面。")
            input("按 Enter 退出 ▶ ")
            ctx.close()
            return

        success, failed = [], []
        for po in po_nums:
            code = code_map.get(po, "UNKNOWN")
            ok = process_po(page, target, po, code,
                            OUTPUT_DIR, scan_only=scan_only)
            (success if ok else failed).append(po)

        print(f"\n{'='*60}")
        if scan_only:
            print(f"扫描完成：{len(success)} 个 PO 已扫描")
        else:
            print(f"✅ 完成：{len(success)} 个成功，{len(failed)} 个失败")
            if failed:
                print(f"   失败：{failed}")
            print(f"   输出目录：{OUTPUT_DIR}/")
            print(f"   中间文件：{OUTPUT_DIR}/temp/（可删）")

        input("\n按 Enter 关闭浏览器 ▶ ")
        ctx.close()


if __name__ == "__main__":
    main()
