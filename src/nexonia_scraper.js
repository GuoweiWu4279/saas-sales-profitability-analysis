/**
 * Nexonia PO Extractor
 * -------------------------------------------
 * 使用方法：
 *   1. 打开 Nexonia PO 列表页，确保所有 PO 都显示（翻页到底 or 改成 "Show All"）
 *   2. 按 F12 打开开发者工具，点 Console 标签
 *   3. 把下面全部代码粘贴进去，回车
 *   4. CSV 自动下载到你的 Downloads 文件夹
 *   5. 用 Python 脚本导入到 Excel
 */
(function () {

  // ── Step 1: 找到表头行，确认列索引 ──────────────────────────────────────
  const allRows = [...document.querySelectorAll("tr")];
  let headerRow = null;
  let colMap = {};

  for (const row of allRows) {
    const cells = [...row.querySelectorAll("th, td")];
    const texts = cells.map(c => c.innerText.replace(/\s+/g, " ").trim().toUpperCase());

    // 找到同时包含 NUMBER 和 VENDOR 的行作为表头
    if (texts.some(t => t.includes("NUMBER")) && texts.some(t => t.includes("VENDOR"))) {
      headerRow = row;
      texts.forEach((t, i) => {
        if (t.includes("NUMBER") && !t.includes("ACCOUNT")) colMap["NUMBER"] = i;
        if (t.includes("CREATION") || t.includes("DATE")) colMap["DATE"] = i;
        if (t.includes("VENDOR"))   colMap["VENDOR"]   = i;
        if (t.includes("APPROVER")) colMap["APPROVER"] = i;
        if (t.includes("MEMO"))     colMap["MEMO"]     = i;
        if (t.includes("AMOUNT"))   colMap["AMOUNT"]   = i;
      });
      break;
    }
  }

  if (!headerRow) {
    alert("❌ 找不到 PO 表格。请确认你在 PO 列表页面。");
    return;
  }

  console.log("列映射：", colMap);

  // ── Step 2: 提取数据行 ────────────────────────────────────────────────────
  const data = [];
  let pastHeader = false;

  for (const row of allRows) {
    if (row === headerRow) { pastHeader = true; continue; }
    if (!pastHeader) continue;

    const cells = [...row.querySelectorAll("td")];
    if (cells.length === 0) continue;

    const poNum = cells[colMap["NUMBER"]]?.innerText.trim() ?? "";

    // PO号是4-6位数字
    if (!/^\d{4,6}$/.test(poNum)) continue;

    data.push({
      "PO Number":     poNum,
      "Creation Date": cells[colMap["DATE"]]?.innerText.trim()     ?? "",
      "Vendor":        cells[colMap["VENDOR"]]?.innerText.trim()   ?? "",
      "Approver":      cells[colMap["APPROVER"]]?.innerText.trim() ?? "",
      "Memo":          cells[colMap["MEMO"]]?.innerText.replace(/\s+/g, " ").trim() ?? "",
      "Total Amount":  cells[colMap["AMOUNT"]]?.innerText.trim()   ?? "",
    });
  }

  if (data.length === 0) {
    alert("❌ 没有找到 PO 数据。如果有多页，请先调整每页显示数量到最大。");
    return;
  }

  // ── Step 3: 生成 CSV 并下载 ──────────────────────────────────────────────
  const headers = ["PO Number", "Creation Date", "Vendor", "Approver", "Memo", "Total Amount"];
  const escape  = v => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const csv     = [
    headers.map(escape).join(","),
    ...data.map(row => headers.map(h => escape(row[h])).join(","))
  ].join("\n");

  const today    = new Date().toISOString().slice(0, 7);   // e.g. 2026-06
  const filename = `Nexonia_POs_${today}.csv`;

  const blob = new Blob(["﻿" + csv, { type: "text/csv;charset=utf-8;" }]);
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href     = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);

  console.log(`✅ 提取了 ${data.length} 条 PO，文件：${filename}`);
  alert(`✅ 成功提取 ${data.length} 条 PO！\n文件已下载：${filename}\n\n接下来运行：\npython src/process_po_csv.py`);

})();
