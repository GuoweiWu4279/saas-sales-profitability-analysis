# PO 审批自动化流程

把"手动抄 PO → 点进每个 PO 抄 Cost Center → 手动分 reviewer → 手写 email"
这一整套，变成两条命令。

## 一次性安装

```powershell
pip install playwright openpyxl pandas Pillow pypdf
playwright install chromium
```

并把你的映射文件 `PO_Review_FY2425.xlsx`（里面有 "Program Reviewers" tab）
放到项目根目录。

---

## 每个月的流程（两步）

### 第 1 步：抓取（全自动，不用手打任何东西）

```powershell
python src/nexonia_full_scraper.py
```

- 浏览器自动打开（**登录一次会永久记住**，以后免登录）
- 登录后进入 PO / Approvals 列表页，按 Enter
- 脚本自动：读列表 → 逐个点开每个 PO 弹窗 → 读 Cost Center → 关闭 → 下一个
- 输出：`output/Nexonia_POs_YYYY-MM.csv`
- 中途断了可以重跑，自动只补没抓到的

### 第 2 步：生成 Excel + email 草稿

```powershell
python src/process_po_csv.py output/Nexonia_POs_2026-06.csv
```

- 从 `PO_Review_*.xlsx` 的 Program Reviewers tab 读映射（大小写不敏感）
- 默认按 **2nd level** 分组（`--level 1` 改成第一级）
- 输出：
  - `output/PO_Tracking_YYYY-MM.xlsx`（tracking 表，存档）
  - `output/emails/<reviewer>.txt`（每个 reviewer 一封，复制即可发）
- 会报告：
  - 同一 code 多条不一致的（已取最后一条，让你核对）
  - 映射表里找不到的 code（需要你去主文件补上）

---

### 第 3 步：审批后 — 存 PDF + 下载附件 + 合并

```powershell
python src/po_pdf_workflow.py 77033 77034 77035
```

或者一次处理当月所有 PO：

```powershell
python src/po_pdf_workflow.py --all
```

- 浏览器自动打开，进入 Approvals 列表页后按 Enter
- 脚本自动：
  - 点开每个 PO 弹窗
  - 把页面（含弹窗）存成 PDF，文件名格式 `77033, ACBFHome01.pdf`
  - 下载弹窗里所有附件（backup PDF）
  - 把 PO PDF + 附件合并成一个最终文件
- 输出：`output/pdfs/77033, ACBFHome01.pdf`

**调试附件扫描**（如果附件没被找到，先用这个确认页面结构）：

```powershell
python src/po_pdf_workflow.py --scan 77033
```

---

## 原理

Nexonia 不能导出、数据"像按钮"不能复制。脚本用**几何坐标法**：读取页面上
每个文字/表单控件的屏幕 (x, y) 位置，像拍照一样还原出表格，所以不依赖网页
的 HTML 结构。Cost Center 的值显示为 `代码 - 代码 - 名称`，脚本取第一段作为
Program Code。

## 注意

- `PO_Review_*.xlsx`、抓取的 CSV、生成的 Excel/email 都含敏感数据，已在
  `.gitignore` 里排除，不会提交到仓库。
- 登录信息存在本地 `.nexonia_session/`（也已排除）。
