# case_search_light

A lightweight FastMCP server based on case_search.

## Included tools

| Tool | 用途 |
|---|---|
| `list_cases` | 以關鍵字（統編／公司名稱／行業別）或行業別搜尋案件，回傳摘要清單 |
| `search_case` | 以統一編號查詢單一案件的完整資料（附欄位中文說明） |
| `get_risk_rules` | 列出風險規則，可依稅目篩選 |
| `assess_case_risk` | 以案件資料逐條比對風險規則，回傳命中規則、佐證數值與建議查核作為 |
| `math_calculate` | 兩數加減乘除 |

所有工具皆為唯讀。查不到資料時回傳 `status: not_found`（附相近候選 `suggestions`），不會丟錯誤。

規則條件（`tax_rules.csv` 的 `logic_condition`）以白名單方式求值：只允許欄位名、數字、
`+ - * /`、比較運算、`AND` / `OR` / `NOT` 與 `abs()`。資料缺漏或除以零的規則會列在
`unable_to_evaluate`，不會當成未命中。

## Demo data

`mcp-server/case_data.csv` 共 18 筆虛構案件，由 `scripts/generate_demo_data.py` 產生：只手寫各年度的銷項、進項、存貨，
比率與已繳稅額一律由公式推導，欄位不會互相矛盾；銷貨成本（期初存貨＋進貨－期末存貨）也保證不為負。要調整案件請改腳本後重跑，不要直接改 CSV。

- 8 筆風險案例，涵蓋 R001～R007 各規則（含單一命中與複合命中）。
- 10 筆對照組，多數刻意「接近但未達」門檻，例如精品服飾庫存偏高、建材批發毛利薄、外銷零稅率實繳 0。
- 統一編號沿用 POC 情境格式（BAN1111～BAN1128）；公司名稱一律以干支開頭，明示為虛構。
- BAN1111 對應 POC 主題「批發零售業連續兩年有大額存貨無對應銷項」（R001＋R002）。

各案件預期命中的規則列在 `tests/test_server.py` 的 `EXPECTED_HITS`。

No environment variables are required. Requires Python 3.10+.

## Run

```bash
pip install -r requirements.txt
python mcp-server/server.py
```

> `mcp` 套件需釘在 `<2`：mcp 2.x 移除了 `mcp.server.fastmcp`（改名為 `MCPServer`），本程式在 2.x 會無法啟動。

## Example platform start command

```bash
sh -c "pip install --quiet -r requirements.txt && exec python mcp-server/server.py"
```

## Test

```bash
pip install pytest
python -m pytest tests
```
