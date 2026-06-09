#!/usr/bin/env python3
"""
Nexonia 全自动 PO 抓取器（几何坐标版 v2）
=========================================
读取页面上每个文字/表单控件的屏幕坐标(x, y)，像拍照一样还原出表格，
不依赖网页 HTML 结构（即使数据"像按钮"也能抓）。

工作流程：
  Phase 1  一次性读取整个 PO 列表（PO号、日期、供应商、金额、Memo）
  Phase 2  逐个点开 PO 弹窗，从明细行的 "Cost Center" 列读出 Program Code，
           读完按 Esc 关闭弹窗，再开下一个

特点：
  • 登录一次永久记住（持久化浏览器，下次免登录/免导航）
  • 失败绝不关浏览器，停下让你看 + 转储诊断
  • 自动重试缓存里没拿到有效 Code 的 PO

安装（只需一次）：
    pip install playwright
    playwright install chromium

运行：
    python src/nexonia_full_scraper.py
"""

import csv
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    sys.exit(
        "\n❌ 缺少 Playwright。请先安装：\n"
        "   pip install playwright\n"
        "   playwright install chromium\n"
    )

NEXONIA_BASE  = "https://a.na1.system.nexonia.com"
SESSION_DIR   = os.path.abspath(".nexonia_session")   # 登录信息保存在这里
PROGRESS_FILE = "output/.scrape_progress.json"

# Program Code（Cost Center 值）的样子：字母开头、含数字、无空格，如 ACFlexMHSA01.1 / GH001.6
CODE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9./\-]*\d[A-Za-z0-9./\-]*$")


def is_valid_code(s):
    return bool(s) and " " not in s and bool(CODE_RE.match(s.strip()))


# ─────────────────────────────────────────────────────────────────────────────
# JS：采集页面上所有"叶子"文字 + 表单控件的值 + 屏幕坐标
# ─────────────────────────────────────────────────────────────────────────────
LEAF_COLLECTOR_JS = r"""
() => {
    const out = [];
    const push = (txt, el, isForm) => {
        txt = (txt || '').replace(/\s+/g, ' ').trim();
        if (!txt) return;
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) return;
        out.push({
            t: txt,
            x: Math.round(r.left),
            y: Math.round(r.top + window.scrollY),
            f: !!isForm,
        });
    };

    for (const el of document.querySelectorAll('body *')) {
        const tag = el.tagName;

        // 表单控件：读它显示的值（下拉框读选中项文字）
        if (tag === 'INPUT') {
            if (el.type !== 'hidden' && el.type !== 'checkbox' && el.value) push(el.value, el, true);
            continue;
        }
        if (tag === 'TEXTAREA') {
            if (el.value) push(el.value, el, true);
            continue;
        }
        if (tag === 'SELECT') {
            const o = el.options[el.selectedIndex];
            if (o && o.text) push(o.text, el, true);
            continue;
        }

        // 普通元素：只取直接文字（不含子元素），避免重复
        let direct = '';
        for (const n of el.childNodes) {
            if (n.nodeType === 3) direct += n.textContent;
        }
        push(direct, el, false);
    }
    return {
        leaves: out,
        scrollHeight: document.body.scrollHeight,
        innerHeight: window.innerHeight,
    };
}
"""

# 不属于数据的"页面框架"文字，列表还原时排除
CHROME_WORDS = {
    "EXPENSES", "PURCHASING", "APPROVALS", "REPORTING", "ADD FILTER", "REFRESH",
    "APPROVE", "REJECT", "ACTION", "NUMBER", "CREATION DATE", "VENDOR",
    "APPROVER", "MEMO", "TOTAL AMOUNT", "ABODE SERVICES", "PRIVACY POLICY",
}


# ─────────────────────────────────────────────────────────────────────────────
# 列表还原：把"文字+坐标"还原成表格记录
# ─────────────────────────────────────────────────────────────────────────────
HEADER_ALIASES = {
    "ACTION":   ["ACTION"],
    "NUMBER":   ["NUMBER"],
    "DATE":     ["CREATION DATE", "CREATION", "DATE"],
    "VENDOR":   ["VENDOR"],
    "APPROVER": ["APPROVER"],
    "MEMO":     ["MEMO"],
    "AMOUNT":   ["TOTAL AMOUNT", "AMOUNT"],
}


def locate_columns(leaves):
    """根据表头文字找到每一列的 x 坐标和表头所在的 y。"""
    colx, header_y = {}, None
    for lf in leaves:
        u = lf["t"].upper()
        for col, names in HEADER_ALIASES.items():
            if col in colx:
                continue
            if u in names:
                colx[col] = lf["x"]
                header_y = lf["y"] if header_y is None else min(header_y, lf["y"])
    return colx, header_y


def nearest_col(x, colx):
    best, best_d = None, 1e9
    for col, cx in colx.items():
        d = abs(x - cx)
        if d < best_d:
            best_d, best = d, col
    return best


def build_records(leaves):
    colx, header_y = locate_columns(leaves)
    if "NUMBER" not in colx or header_y is None:
        return None, colx

    num_x = colx["NUMBER"]
    po_leaves = [
        lf for lf in leaves
        if lf["y"] > header_y + 5
        and re.fullmatch(r"\d{4,6}", lf["t"])
        and abs(lf["x"] - num_x) < 70
    ]
    po_leaves.sort(key=lambda l: l["y"])

    seen, uniq = set(), []
    for lf in po_leaves:
        if lf["t"] not in seen:
            seen.add(lf["t"])
            uniq.append(lf)
    po_leaves = uniq

    records = []
    for i, po in enumerate(po_leaves):
        y0 = po["y"] - 8
        y1 = po_leaves[i + 1]["y"] - 8 if i + 1 < len(po_leaves) else 1e18

        cells = defaultdict(list)
        for lf in leaves:
            if not (y0 <= lf["y"] < y1):
                continue
            if lf["t"].upper() in CHROME_WORDS:   # 排除导航/表头噪声
                continue
            cells[nearest_col(lf["x"], colx)].append((lf["y"], lf["x"], lf["t"]))

        def gather(col):
            items = sorted(cells.get(col, []))
            return " ".join(t for _, _, t in items).strip()

        records.append({
            "po":       po["t"],
            "date":     gather("DATE"),
            "vendor":   gather("VENDOR"),
            "approver": gather("APPROVER"),
            "amount":   gather("AMOUNT"),
            "memo":     gather("MEMO"),
        })
    return records, colx


# ─────────────────────────────────────────────────────────────────────────────
# 弹窗详情：从明细行的 "Cost Center" 列读 Program Code
# ─────────────────────────────────────────────────────────────────────────────
def find_cost_center(leaves):
    # 找所有 "Cost Center" 列表头位置
    headers = [(lf["x"], lf["y"]) for lf in leaves
               if lf["t"].strip().upper() == "COST CENTER"]
    if not headers:
        return None

    found = []
    for hx, hy in headers:
        for lf in leaves:
            if not (hy + 5 < lf["y"] < hy + 280):
                continue
            if abs(lf["x"] - hx) > 90:
                continue
            t = lf["t"].strip()
            if is_valid_code(t):
                found.append((lf["y"], t))

    # 按出现顺序去重
    seen, out = set(), []
    for _, t in sorted(found):
        if t not in seen:
            seen.add(t)
            out.append(t)
    return ";".join(out) if out else None


# ─────────────────────────────────────────────────────────────────────────────
# 找含 PO 表格的 frame（多数情况就是主页面）
# ─────────────────────────────────────────────────────────────────────────────
def grab(target):
    return target.evaluate(LEAF_COLLECTOR_JS)


def find_target(page):
    for t in [page] + list(page.frames):
        try:
            data = grab(t)
            leaves = data["leaves"]
            colx, _ = locate_columns(leaves)
            has_num = any(re.fullmatch(r"\d{4,6}", lf["t"]) for lf in leaves)
            if "NUMBER" in colx and "VENDOR" in colx and has_num:
                return t
        except Exception:
            pass
    return None


def capture_list(target):
    """列表一次性全部渲染（非虚拟滚动）。先滚到底触发懒加载，回到顶部再单次采集，
    避免固定表头/导航在滚动中产生重影污染。"""
    try:
        target.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(0.6)
        target.evaluate("window.scrollTo(0, 0)")
        time.sleep(0.3)
    except Exception:
        pass
    return grab(target)["leaves"]


# ─────────────────────────────────────────────────────────────────────────────
# 进度缓存
# ─────────────────────────────────────────────────────────────────────────────
def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_progress(data):
    os.makedirs("output", exist_ok=True)
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def dump_diagnostics(page):
    os.makedirs("output", exist_ok=True)
    try:
        page.screenshot(path="output/diagnostic_screenshot.png", full_page=True)
        print("   已保存截图：output/diagnostic_screenshot.png")
    except Exception:
        pass
    for i, fr in enumerate([page] + list(page.frames)):
        try:
            data = grab(fr)
            leaves = data["leaves"]
            colx, _ = locate_columns(leaves)
            print(f"   frame[{i}] {fr.url[:70]}  文字块={len(leaves)} 列={list(colx)}")
            with open(f"output/diagnostic_frame_{i}.json", "w", encoding="utf-8") as f:
                json.dump({"url": fr.url, "columns": colx, "leaves": leaves},
                          f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"   frame[{i}] 读取失败：{e}")


# ─────────────────────────────────────────────────────────────────────────────
# 弹窗：打开 / 关闭
# ─────────────────────────────────────────────────────────────────────────────
def close_modal(page, target):
    """关闭 PO 详情弹窗，确保回到列表。"""
    for _ in range(3):
        # 弹窗已关？
        try:
            if target.get_by_text("Order Details", exact=False).count() == 0:
                return True
        except Exception:
            return True
        # 先试 Esc
        try:
            page.keyboard.press("Escape")
            time.sleep(0.4)
        except Exception:
            pass
        # 再试点 Close
        try:
            btn = target.get_by_text("Close", exact=True).first
            if btn.is_visible():
                btn.click(timeout=1500)
                time.sleep(0.4)
        except Exception:
            pass
    return False


# ─────────────────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────────────────
def main():
    os.makedirs("output", exist_ok=True)
    progress = load_progress()
    valid_cached = sum(1 for v in progress.values() if is_valid_code(v.get("cost_center", "")))
    if progress:
        print(f"发现断点缓存：{len(progress)} 个，其中 {valid_cached} 个已有有效 Code（会跳过），"
              f"其余会自动重试。")

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=SESSION_DIR,
            headless=False,
            slow_mo=30,
            viewport=None,
            args=["--start-maximized"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        try:
            page.goto(NEXONIA_BASE + "/assistant/home.do", wait_until="domcontentloaded")
        except Exception:
            pass

        print("\n" + "=" * 64)
        print("浏览器已打开。请：")
        print("  1. 如果没登录，登录 Nexonia（之后会被记住）")
        print("  2. 进入 PO / Approvals 列表页（有一堆 PO 的那个表格页）")
        print("  3. 停在那一页即可，不用复制任何东西")
        print("=" * 64)
        input("准备好后按 Enter 开始抓取 ▶ ")

        print("\n  正在定位 PO 表格…")
        target = find_target(page)
        if target is None:
            print("\n❌ 没找到 PO 表格。浏览器保持打开，下面是诊断：")
            dump_diagnostics(page)
            input("\n请把输出/截图发给我。按 Enter 关闭 ▶ ")
            ctx.close()
            return

        # ── Phase 1: 一次性读取列表 ──────────────────────────────────────
        print("  读取 PO 列表…")
        leaves = capture_list(target)
        records, colx = build_records(leaves)
        if not records:
            print(f"\n❌ 没还原出 PO 行。识别到的列：{list(colx)}")
            dump_diagnostics(page)
            input("\n请把输出发给我。按 Enter 关闭 ▶ ")
            ctx.close()
            return

        total = len(records)
        print(f"  ✅ 识别 {total} 个 PO（例：{records[0]['po']} | "
              f"{records[0]['vendor'][:28]} | {records[0]['amount']}）")

        # ── Phase 2: 逐个开弹窗抓 Cost Center ────────────────────────────
        print(f"\n  开始抓 Cost Center（共 {total} 个）…")
        for i, rec in enumerate(records):
            po = rec["po"]
            if po in progress and is_valid_code(progress[po].get("cost_center", "")):
                print(f"  [{i+1:>3}/{total}] PO {po}  ✓ 缓存 {progress[po]['cost_center']}")
                continue

            cost_center = "NOT_FOUND"
            try:
                # 点 PO 号打开弹窗
                target.get_by_text(po, exact=True).first.click(timeout=6000)

                # 等弹窗 + 明细加载
                try:
                    target.get_by_text("Order Details", exact=False).first.wait_for(timeout=8000)
                except PWTimeout:
                    pass

                cc = None
                for _ in range(3):           # 明细行可能异步加载，重试几次
                    time.sleep(0.8)
                    cc = find_cost_center(grab(target)["leaves"])
                    if cc:
                        break
                cost_center = cc if cc else "NOT_FOUND"

                # 第一个 PO 转储详情，便于核对
                if i == 0 and not cc:
                    with open("output/diagnostic_detail_first.json", "w", encoding="utf-8") as f:
                        json.dump({"po": po, "leaves": grab(target)["leaves"]},
                                  f, ensure_ascii=False, indent=2)

                close_modal(page, target)

            except PWTimeout:
                cost_center = "TIMEOUT"
                close_modal(page, target)
            except Exception as e:
                cost_center = "ERROR"
                close_modal(page, target)
                if i == 0:
                    print(f"      （首个 PO 报错：{e}）")

            mark = ("→ " + cost_center) if is_valid_code(cost_center) else ("⚠ " + cost_center)
            print(f"  [{i+1:>3}/{total}] PO {po}  {mark}")

            progress[po] = {**rec, "cost_center": cost_center}
            save_progress(progress)

        # ── 导出 CSV ─────────────────────────────────────────────────────
        month   = datetime.now().strftime("%Y-%m")
        out_csv = f"output/Nexonia_POs_{month}.csv"
        fields  = ["po", "date", "vendor", "approver", "amount", "cost_center", "memo"]
        rows = sorted(progress.values(), key=lambda r: int(r.get("po", 0)))

        with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)

        bad = [r for r in rows if not is_valid_code(r.get("cost_center", ""))]
        print(f"\n{'='*64}")
        print(f"✅ 完成！输出：{out_csv}")
        print(f"   {len(rows)} 个 PO，{len(rows)-len(bad)} 个拿到有效 Cost Center")
        if bad:
            print(f"   ⚠️  {len(bad)} 个没拿到：{[r['po'] for r in bad]}")
            print("   （重新运行会自动只重试这些；首个失败的详情见 "
                  "output/diagnostic_detail_first.json）")
        else:
            if os.path.exists(PROGRESS_FILE):
                os.remove(PROGRESS_FILE)

        print(f"\n下一步：python src/process_po_csv.py {out_csv}")
        input("\n按 Enter 关闭浏览器（登录已记住）▶ ")
        ctx.close()


if __name__ == "__main__":
    main()
