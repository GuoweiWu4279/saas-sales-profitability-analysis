#!/usr/bin/env python3
"""
Nexonia 全自动PO抓取器
----------------------
自动打开浏览器，读取PO列表，点进每个PO提取 Cost Center，
最后输出完整 CSV，无需手动输入任何数据。

安装依赖（只需一次）：
    pip install playwright
    playwright install chromium

运行：
    python src/nexonia_full_scraper.py

中途可以 Ctrl+C 暂停，下次运行自动从断点继续。
"""

import csv
import json
import os
import re
import sys
import time
from datetime import datetime

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    sys.exit(
        "\n❌ 缺少 Playwright。请先安装：\n"
        "   pip install playwright\n"
        "   playwright install chromium\n"
    )

NEXONIA_BASE   = "https://a.na1.system.nexonia.com"
PROGRESS_FILE  = "output/.scrape_progress.json"


# ─────────────────────────────────────────────────────────────────────────────
# 从列表页提取所有PO行（包括详情链接）
# ─────────────────────────────────────────────────────────────────────────────

LIST_EXTRACTOR_JS = """
() => {
    const rows = [...document.querySelectorAll('tr')];
    let colMap = {}, foundHeader = false;

    // 找表头行
    for (const row of rows) {
        const cells = [...row.querySelectorAll('th, td')];
        const texts = cells.map(c => c.innerText.replace(/\\s+/g,' ').trim().toUpperCase());
        if (texts.some(t => t.includes('NUMBER')) && texts.some(t => t.includes('VENDOR'))) {
            texts.forEach((t, i) => {
                if (t.includes('NUMBER') && !t.includes('ACCOUNT')) colMap.NUMBER = i;
                if (t.includes('DATE'))     colMap.DATE     = i;
                if (t.includes('VENDOR'))   colMap.VENDOR   = i;
                if (t.includes('APPROVER')) colMap.APPROVER = i;
                if (t.includes('AMOUNT'))   colMap.AMOUNT   = i;
                if (t.includes('MEMO'))     colMap.MEMO     = i;
            });
            foundHeader = true;
            break;
        }
    }
    if (!foundHeader || colMap.NUMBER === undefined)
        return { error: 'header_not_found', cols: [...document.querySelectorAll('th')].map(t=>t.innerText) };

    const data = [];
    let past = false;
    for (const row of rows) {
        const cells = [...row.querySelectorAll('td')];
        const ths   = [...row.querySelectorAll('th')];
        if (!past) {
            const headerTexts = [...row.querySelectorAll('th,td')]
                .map(c => c.innerText.toUpperCase());
            if (headerTexts.some(t => t.includes('NUMBER'))) { past = true; continue; }
        }
        if (!past || cells.length === 0) continue;

        const poNum = cells[colMap.NUMBER]?.innerText.trim() ?? '';
        if (!/^\\d{4,6}$/.test(poNum)) continue;

        // 找详情链接：优先找 href 包含 po/view 等关键词，否则取行里最后一个 <a>
        const allLinks = [...row.querySelectorAll('a[href]')];
        let href = null;
        for (const a of allLinks) {
            const h = a.getAttribute('href') || '';
            if (/view|detail|show|edit|open/i.test(h) || h.includes(poNum)) {
                href = h; break;
            }
        }
        if (!href && allLinks.length > 0)
            href = allLinks[allLinks.length - 1].getAttribute('href');

        data.push({
            po:       poNum,
            date:     cells[colMap.DATE]    ?.innerText.trim() ?? '',
            vendor:   cells[colMap.VENDOR]  ?.innerText.trim() ?? '',
            approver: cells[colMap.APPROVER]?.innerText.trim() ?? '',
            amount:   cells[colMap.AMOUNT]  ?.innerText.trim() ?? '',
            memo:     (cells[colMap.MEMO]   ?.innerText ?? '').replace(/\\s+/g,' ').trim(),
            href,
        });
    }
    return { data };
}
"""

COST_CENTER_JS = """
() => {
    // 候选标签名称（按优先级）
    const LABELS = ['cost center', 'cost center:', 'program', 'program code',
                    'account', 'fund', 'project code'];

    const els = [...document.querySelectorAll('td, th, label, span, div, p, li')];

    for (const el of els) {
        const raw = el.innerText.trim().toLowerCase().replace(/\\s+/g,' ');
        if (!LABELS.includes(raw)) continue;

        // A: 相邻 td/sibling
        let sib = el.nextElementSibling;
        while (sib) {
            const v = sib.innerText.trim();
            if (v && !LABELS.includes(v.toLowerCase())) return v;
            sib = sib.nextElementSibling;
        }

        // B: 同行的下一个 td
        const tr = el.closest('tr');
        if (tr) {
            const tds = [...tr.querySelectorAll('td, th')];
            for (let i = 0; i < tds.length - 1; i++) {
                if (LABELS.includes(tds[i].innerText.trim().toLowerCase().replace(/\\s+/g,' '))) {
                    const v = tds[i+1].innerText.trim();
                    if (v) return v;
                }
            }
        }

        // C: 父元素的下一个兄弟
        const nextPar = el.parentElement?.nextElementSibling;
        if (nextPar) {
            const v = nextPar.innerText.trim();
            if (v && !LABELS.includes(v.toLowerCase())) return v;
        }
    }

    // 最后兜底：页面文本正则
    const bodyText = document.body.innerText;
    const m = bodyText.match(/cost\\s*center[:\\s]+([A-Za-z][A-Za-z0-9.\\-]+)/i)
           || bodyText.match(/program\\s*code[:\\s]+([A-Za-z][A-Za-z0-9.\\-]+)/i);
    return m ? m[1] : null;
}
"""


# ─────────────────────────────────────────────────────────────────────────────
# 进度缓存（支持断点续抓）
# ─────────────────────────────────────────────────────────────────────────────

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_progress(data: dict):
    os.makedirs("output", exist_ok=True)
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ─────────────────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────────────────

def main():
    os.makedirs("output", exist_ok=True)
    progress = load_progress()
    done_pos = set(progress.keys())
    if done_pos:
        print(f"发现断点缓存：{len(done_pos)} 个PO已完成，自动跳过。")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=80)
        page    = browser.new_page()
        page.goto(NEXONIA_BASE + "/assistant/home.do", wait_until="domcontentloaded")

        print("\n" + "="*60)
        print("浏览器已打开，请：")
        print("  1. 登录 Nexonia")
        print("  2. 进入 Purchasing，打开本月 PO 列表")
        print("  3. 如果可以，调最大显示数量（减少分页）")
        print("="*60)
        input("准备好后按 Enter，脚本自动开始 ▶ ")

        # ── Phase 1: 收集所有页面的 PO 基础信息 ──────────────────────────
        all_po_info = []
        page_num = 0

        while True:
            page_num += 1
            print(f"\n  读取列表第 {page_num} 页…")

            # 等页面完全稳定，最多尝试3次
            for _ in range(3):
                try:
                    page.wait_for_load_state("load", timeout=15000)
                    page.wait_for_load_state("networkidle", timeout=10000)
                    break
                except PWTimeout:
                    pass

            # evaluate 失败时（页面跳转中）自动重试
            result = None
            for attempt in range(4):
                try:
                    result = page.evaluate(LIST_EXTRACTOR_JS)
                    break
                except Exception as e:
                    if "navigation" in str(e).lower() or "context" in str(e).lower():
                        print(f"     页面还在加载，等待后重试（{attempt+1}/4）…")
                        time.sleep(2)
                        try:
                            page.wait_for_load_state("networkidle", timeout=8000)
                        except PWTimeout:
                            pass
                    else:
                        raise

            if result is None:
                print("❌ 多次重试后仍无法读取页面，请确认已在PO列表页。")
                browser.close()
                return

            if "error" in result:
                if page_num == 1:
                    print(f"❌ 读不到PO表格。请确认你在PO列表页。")
                    print(f"   检测到的表头：{result.get('cols', [])}")
                    browser.close()
                    return
                break

            batch = result.get("data", [])
            print(f"     找到 {len(batch)} 个PO")
            if not batch:
                break
            all_po_info.extend(batch)

            # 找"下一页"按钮
            next_btn = None
            for selector in [
                "a[aria-label='Next page']",
                "a:has-text('Next')",
                "button:has-text('Next')",
                "[class*='next']:not([disabled])",
                "a:has-text('›')",
                "a:has-text('»')",
            ]:
                try:
                    btn = page.query_selector(selector)
                    if btn and btn.is_visible() and btn.is_enabled():
                        next_btn = btn
                        break
                except Exception:
                    pass

            if not next_btn:
                break
            next_btn.click()

        total = len(all_po_info)
        new_pos = [p for p in all_po_info if p["po"] not in done_pos]
        print(f"\n共 {total} 个PO，其中 {len(new_pos)} 个需要抓取 Cost Center。\n")

        # ── Phase 2: 逐个访问详情页，提取 Cost Center ─────────────────────
        for i, po_info in enumerate(all_po_info):
            po_num = po_info["po"]

            if po_num in done_pos:
                print(f"  [{i+1:>3}/{total}] PO {po_num}  ✓ 已缓存")
                continue

            href = po_info.get("href")
            if not href:
                print(f"  [{i+1:>3}/{total}] PO {po_num}  ⚠ 无详情链接")
                progress[po_num] = {**po_info, "cost_center": "NO_LINK"}
                save_progress(progress)
                continue

            detail_url = href if href.startswith("http") else NEXONIA_BASE + href

            try:
                page.goto(detail_url, wait_until="networkidle", timeout=20000)
                time.sleep(0.3)

                cost_center = page.evaluate(COST_CENTER_JS) or "NOT_FOUND"
                print(f"  [{i+1:>3}/{total}] PO {po_num}  →  {cost_center}")

                progress[po_num] = {**po_info, "cost_center": cost_center}
                save_progress(progress)

            except PWTimeout:
                print(f"  [{i+1:>3}/{total}] PO {po_num}  ⏱ 超时")
                progress[po_num] = {**po_info, "cost_center": "TIMEOUT"}
                save_progress(progress)
            except Exception as e:
                print(f"  [{i+1:>3}/{total}] PO {po_num}  ❌ {e}")
                progress[po_num] = {**po_info, "cost_center": "ERROR"}
                save_progress(progress)

        browser.close()

    # ── Phase 3: 导出 CSV ──────────────────────────────────────────────────
    month   = datetime.now().strftime("%Y-%m")
    out_csv = f"output/Nexonia_POs_{month}.csv"
    fields  = ["po", "date", "vendor", "approver", "amount", "cost_center", "memo"]

    # 按 PO 号排序输出
    rows = sorted(progress.values(), key=lambda r: int(r.get("po", 0)))

    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    if os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)

    not_found = [r for r in rows if r.get("cost_center") in ("NOT_FOUND", "NO_LINK", "TIMEOUT", "ERROR")]

    print(f"\n{'='*60}")
    print(f"✅ 完成！输出：{out_csv}")
    print(f"   总计 {len(rows)} 个PO，{len(rows)-len(not_found)} 个成功提取 Cost Center")
    if not_found:
        print(f"   ⚠️  {len(not_found)} 个未找到 Cost Center：{[r['po'] for r in not_found]}")
    print(f"\n下一步：")
    print(f"   python src/process_po_csv.py {out_csv}")


if __name__ == "__main__":
    main()
