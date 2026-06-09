#!/usr/bin/env python3
"""
Nexonia 全自动 PO 抓取器（几何坐标版）
=====================================
不依赖网页的 HTML 结构（即使数据"像按钮"无法复制也能抓），
而是读取页面上每个文字的屏幕坐标(x, y)，像拍照一样还原出表格。

特点：
  • 登录一次，永久记住 —— 用持久化浏览器，下次跑不用重新登录/导航
  • 失败绝不关浏览器 —— 出错就停下让你看，浏览器留着
  • 自动滚动加载所有行
  • 中途中断可续抓（断点缓存）

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


# ─────────────────────────────────────────────────────────────────────────────
# JS：采集页面上所有"叶子"文字 + 它们的屏幕坐标
# （叶子 = 直接含文字的元素，坐标用绝对坐标 scrollY 以便跨滚动去重）
# ─────────────────────────────────────────────────────────────────────────────
LEAF_COLLECTOR_JS = r"""
() => {
    const out = [];
    const els = document.querySelectorAll('body *');
    for (const el of els) {
        // 只取该元素"直接"包含的文字（不含子元素的文字），避免重复
        let direct = '';
        for (const n of el.childNodes) {
            if (n.nodeType === 3) direct += n.textContent;
        }
        direct = direct.replace(/\s+/g, ' ').trim();
        if (!direct) continue;

        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) continue;

        out.push({
            t: direct,
            x: Math.round(r.left),
            y: Math.round(r.top + window.scrollY),
        });
    }
    return {
        leaves: out,
        scrollHeight: document.body.scrollHeight,
        innerHeight: window.innerHeight,
    };
}
"""


# ─────────────────────────────────────────────────────────────────────────────
# 把"文字+坐标"还原成表格记录
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
    # PO 号 = 表头下方、横坐标接近 NUMBER 列、且是 4-6 位数字
    po_leaves = [
        lf for lf in leaves
        if lf["y"] > header_y + 5
        and re.fullmatch(r"\d{4,6}", lf["t"])
        and abs(lf["x"] - num_x) < 70
    ]
    po_leaves.sort(key=lambda l: l["y"])

    # 跨滚动会有重复 PO，按 PO 号去重保留最先出现的
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
            if y0 <= lf["y"] < y1:
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


def find_cost_center(leaves):
    """在详情视图里按标签找 Cost Center 的值（标签右边或下方）。"""
    LABELS = ["COST CENTER", "PROGRAM", "PROGRAM CODE", "PROJECT", "PROJECT CODE", "FUND"]

    def is_label(txt):
        return txt.upper().rstrip(":").strip() in LABELS

    for lf in leaves:
        if not is_label(lf["t"]):
            continue
        ly, lx = lf["y"], lf["x"]

        # 右边同一行
        right = sorted(
            [o for o in leaves if abs(o["y"] - ly) < 16 and o["x"] > lx + 5 and o["t"].strip()],
            key=lambda o: o["x"],
        )
        for o in right:
            if not is_label(o["t"]):
                return o["t"]

        # 正下方
        below = sorted(
            [o for o in leaves if 0 < o["y"] - ly < 45 and abs(o["x"] - lx) < 140 and o["t"].strip()],
            key=lambda o: o["y"],
        )
        for o in below:
            if not is_label(o["t"]):
                return o["t"]
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 滚动采集：滚到底，沿途收集所有叶子（应对懒加载/虚拟滚动）
# ─────────────────────────────────────────────────────────────────────────────
def collect_all_leaves(target):
    all_leaves, seen = [], set()

    def grab():
        data = target.evaluate(LEAF_COLLECTOR_JS)
        for lf in data["leaves"]:
            key = (lf["t"], lf["x"], lf["y"])
            if key not in seen:
                seen.add(key)
                all_leaves.append(lf)
        return data

    data = grab()
    last_h, stable = 0, 0
    for _ in range(60):  # 最多滚 60 屏
        target.evaluate("window.scrollBy(0, Math.round(window.innerHeight * 0.85))")
        time.sleep(0.4)
        data = grab()
        h = data["scrollHeight"]
        if h == last_h:
            stable += 1
            if stable >= 2:
                break
        else:
            stable = 0
        last_h = h

    target.evaluate("window.scrollTo(0, 0)")
    time.sleep(0.2)
    return all_leaves


# ─────────────────────────────────────────────────────────────────────────────
# 找到含 PO 表格的 frame（多数情况就是主页面）
# ─────────────────────────────────────────────────────────────────────────────
def find_target(page):
    candidates = [page] + list(page.frames)
    for t in candidates:
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


# ─────────────────────────────────────────────────────────────────────────────
# 诊断转储：抓不到时把页面结构存下来供分析
# ─────────────────────────────────────────────────────────────────────────────
def dump_diagnostics(page):
    os.makedirs("output", exist_ok=True)
    try:
        page.screenshot(path="output/diagnostic_screenshot.png", full_page=True)
        print("   已保存截图：output/diagnostic_screenshot.png")
    except Exception:
        pass
    for i, fr in enumerate([page] + list(page.frames)):
        try:
            data = fr.evaluate(LEAF_COLLECTOR_JS)
            leaves = data["leaves"]
            colx, _ = locate_columns(leaves)
            sample = [lf["t"] for lf in leaves[:40]]
            print(f"   frame[{i}] {fr.url[:70]}  文字块={len(leaves)} 识别到的列={list(colx)}")
            if i == 0 or colx:
                with open(f"output/diagnostic_frame_{i}.json", "w", encoding="utf-8") as f:
                    json.dump({"url": fr.url, "columns": colx, "leaves": leaves},
                              f, ensure_ascii=False, indent=2)
                print(f"     详细文字+坐标已存：output/diagnostic_frame_{i}.json")
            print(f"     前40个文字块：{sample}")
        except Exception as e:
            print(f"   frame[{i}] 读取失败：{e}")


# ─────────────────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────────────────
def main():
    os.makedirs("output", exist_ok=True)
    progress = load_progress()
    if progress:
        print(f"发现断点缓存：{len(progress)} 个PO已完成，将自动跳过。")

    with sync_playwright() as p:
        # 持久化浏览器：登录信息存在 SESSION_DIR，下次免登录
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=SESSION_DIR,
            headless=False,
            slow_mo=40,
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
        print("  1. 如果没登录，登录 Nexonia（之后会被记住，下次免登录）")
        print("  2. 进入 PO / Approvals 列表页（就是有一堆 PO 的那个表格页）")
        print("  3. 不用复制任何东西，停在那一页即可")
        print("=" * 64)
        input("准备好后按 Enter 开始抓取 ▶ ")

        # ── 找含 PO 的 frame ─────────────────────────────────────────────
        print("\n  正在定位 PO 表格…")
        target = find_target(page)
        if target is None:
            print("\n❌ 没找到 PO 表格。下面是页面诊断，浏览器保持打开：")
            dump_diagnostics(page)
            input("\n请把上面的输出/截图发给我。看完后按 Enter 关闭 ▶ ")
            ctx.close()
            return

        # ── Phase 1: 滚动采集 + 几何还原 ──────────────────────────────────
        print("  正在滚动加载并读取所有 PO…")
        leaves = collect_all_leaves(target)
        records, colx = build_records(leaves)

        if not records:
            print(f"\n❌ 读到了页面文字但没还原出 PO 行。识别到的列：{list(colx)}")
            print("   浏览器保持打开，下面是诊断：")
            dump_diagnostics(page)
            input("\n请把输出发给我。按 Enter 关闭 ▶ ")
            ctx.close()
            return

        total = len(records)
        print(f"  ✅ 成功识别 {total} 个 PO")
        print(f"     示例：{records[0]['po']} | {records[0]['vendor'][:30]} | {records[0]['amount']}")

        # ── Phase 2: 逐个打开详情，抓 Cost Center ────────────────────────
        print(f"\n  开始逐个抓取 Cost Center（共 {total} 个）…")
        list_url = target.url

        for i, rec in enumerate(records):
            po = rec["po"]
            if po in progress:
                print(f"  [{i+1:>3}/{total}] PO {po}  ✓ 已缓存 ({progress[po].get('cost_center','')})")
                continue

            cost_center = "NOT_FOUND"
            try:
                # 点 PO 号（展开或进入详情）
                loc = target.get_by_text(po, exact=True).first
                loc.scroll_into_view_if_needed(timeout=5000)
                loc.click(timeout=5000)
                time.sleep(1.0)

                detail_leaves = collect_all_leaves(target)
                cc = find_cost_center(detail_leaves)
                cost_center = cc if cc else "NOT_FOUND"

                # 第一个 PO：转储详情结构供核对
                if i == 0:
                    with open("output/diagnostic_detail_first.json", "w", encoding="utf-8") as f:
                        json.dump({"po": po, "cost_center": cc, "leaves": detail_leaves},
                                  f, ensure_ascii=False, indent=2)

                # 收起 / 返回列表
                try:
                    loc2 = target.get_by_text(po, exact=True).first
                    loc2.click(timeout=2000)   # 再点一次收起（若是内联展开）
                    time.sleep(0.4)
                except Exception:
                    if target.url != list_url:
                        target.goto(list_url, wait_until="domcontentloaded")
                        time.sleep(0.6)

            except PWTimeout:
                cost_center = "TIMEOUT"
            except Exception as e:
                cost_center = "ERROR"
                if i == 0:
                    print(f"      （第一个 PO 出错：{e}）")

            mark = "→ " + cost_center if cost_center not in ("NOT_FOUND", "TIMEOUT", "ERROR") else "⚠ " + cost_center
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

        bad = [r for r in rows if r.get("cost_center") in ("NOT_FOUND", "TIMEOUT", "ERROR")]
        print(f"\n{'='*64}")
        print(f"✅ 完成！输出：{out_csv}")
        print(f"   {len(rows)} 个 PO，{len(rows)-len(bad)} 个成功拿到 Cost Center")
        if bad:
            print(f"   ⚠️  {len(bad)} 个没抓到：{[r['po'] for r in bad]}")
            print("   （详情结构见 output/diagnostic_detail_first.json，发我可优化）")
        else:
            if os.path.exists(PROGRESS_FILE):
                os.remove(PROGRESS_FILE)

        print(f"\n下一步：python src/process_po_csv.py {out_csv}")
        input("\n按 Enter 关闭浏览器（登录已记住，下次免登录）▶ ")
        ctx.close()


if __name__ == "__main__":
    main()
