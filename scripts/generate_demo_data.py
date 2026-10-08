"""產生 mcp-server/case_data.csv 的 demo 資料。

只手寫每個案件的「原始金額」，比率與稅額一律由公式推導，避免欄位彼此矛盾：

- 存貨對銷項比率 = 存貨 / 銷項
- 存貨對進項比率 = 存貨 / 進項
- 加值率         = (銷項 - 進項) / 銷項
- 已繳營業稅額   = 一般課稅：max(0, (銷項 - 進項) × 5% − 上年度累積留抵)
                   查定課徵：銷項 × 1%
                   外銷零稅率：0（進項稅額申請退還）

另檢查銷貨成本（期初存貨 + 本期進貨 − 期末存貨）不得為負，避免出現會計上不可能的數字。

統一編號沿用 POC 情境的格式（BAN + 4 碼，例如 BAN1111）。
公司名稱一律以天干地支開頭，明示為虛構。

用法：python scripts/generate_demo_data.py
"""

import csv
from pathlib import Path

OUTPUT = Path(__file__).resolve().parents[1] / "mcp-server" / "case_data.csv"

GENERAL = "1(一般課稅)"
ASSESSED = "2(查定課徵)"

FIELDS = [
    "ban", "company_name", "industry_type",
    "yr111_sales", "yr111_purchases", "yr111_inventory",
    "yr111_inv_sales_ratio", "yr111_inv_purch_ratio", "yr111_vat_rate", "yr111_tax_paid",
    "yr112_sales", "yr112_purchases", "yr112_inventory",
    "yr112_inv_sales_ratio", "yr112_inv_purch_ratio", "yr112_vat_rate", "yr112_tax_paid",
    "tax_method",
]

# (公司名稱, 行業別, 課稅方式, 外銷零稅率, 111 年(銷項, 進項, 存貨), 112 年(銷項, 進項, 存貨), 情境)
CASES = [
    ("甲申五金批發有限公司", "五金批發業", GENERAL, False,
     (20_000_000, 21_000_000, 45_000_000), (12_000_000, 17_000_000, 58_000_000),
     "R001＋R002：連續兩年持續進貨、存貨墊高到營收近 5 倍，銷項卻下滑、加值率為負，"
     "即 POC 主題「批發零售業連續兩年有大額存貨無對應銷項」"),
    ("乙酉電子材料行", "電子零組件批發業", GENERAL, False,
     (50_000_000, 52_000_000, 10_000_000), (55_000_000, 58_000_000, 14_000_000),
     "R002：連兩年進項大於銷項、長期留抵，疑似收集不實進項發票"),
    ("丙戌國際實業有限公司", "金屬製品批發業", GENERAL, False,
     (0, 0, 0), (200_000_000, 197_000_000, 500_000),
     "R006＋R007：前一年零營業，次年突然對開 2 億、進銷幾乎相等，疑似虛設行號循環交易"),
    ("丁亥精密商行", "機械器具批發業", GENERAL, False,
     (18_000_000, 16_000_000, 40_000_000), (19_000_000, 27_000_000, 62_000_000),
     "R001＋R003：存貨一年暴增 55% 但營收只成長 5%，且存貨長期為營收 2 倍以上"),
    ("丁卯顧問有限公司", "管理顧問業", GENERAL, False,
     (5_000_000, 1_000_000, 0), (5_500_000, 20_000_000, 0),
     "R004＋R005：顧問業本業無進貨，卻一年購入 2 千萬名車與不動產扣抵，形成鉅額留抵"),
    ("戊寅家電批發有限公司", "家用電器批發業", GENERAL, False,
     (36_000_000, 30_000_000, 8_000_000), (36_500_000, 32_000_000, 14_400_000),
     "R003：存貨成長 80%、營收持平，進貨未轉為銷售"),
    ("己卯開發有限公司", "不動產租賃業", GENERAL, False,
     (8_000_000, 5_600_000, 0), (8_200_000, 15_000_000, 0),
     "R005：租金收入持平，進項卻倍增，疑似以公司名義購置非營業用資產"),
    ("庚辰貿易有限公司", "綜合商品批發業", GENERAL, False,
     (300_000, 250_000, 50_000), (8_000_000, 6_800_000, 400_000),
     "R007：休眠多年的公司營收一年暴增 26 倍，需監控後續開票"),
    ("戊辰服飾精品店", "服飾品零售業", GENERAL, False,
     (12_000_000, 8_000_000, 15_000_000), (11_000_000, 9_000_000, 18_000_000),
     "對照組：精品服飾庫存偏高（約營收 1.5 倍）屬產業常態，未達 R001 門檻"),
    ("己巳建材行", "建材批發業", GENERAL, False,
     (30_000_000, 29_000_000, 5_000_000), (32_000_000, 31_000_000, 5_000_000),
     "對照組：建材批發毛利薄（加值率約 3%），接近但未達 R002 門檻"),
    ("庚午餐飲股份有限公司", "餐飲業", GENERAL, False,
     (80_000_000, 30_000_000, 2_000_000), (48_000_000, 19_000_000, 1_300_000),
     "對照組：關閉門市營收衰退 40%，進貨與存貨同步下修，屬正常經營調整"),
    ("辛未進出口有限公司", "國際貿易業", GENERAL, True,
     (100_000_000, 80_000_000, 10_000_000), (120_000_000, 90_000_000, 15_000_000),
     "對照組：外銷適用零稅率，實繳稅額為 0 屬正常，未達 R004 門檻"),
    ("壬申科技股份有限公司", "資訊軟體服務業", GENERAL, False,
     (15_000_000, 5_000_000, 0), (16_000_000, 14_000_000, 0),
     "對照組：當年度購置伺服器設備使進項增加，但加值率仍為正，未達 R005 門檻"),
    ("癸酉文具店", "文具零售業", ASSESSED, False,
     (2_000_000, 1_200_000, 300_000), (2_100_000, 1_300_000, 350_000),
     "對照組：月銷售額未達 20 萬元的小規模營業人，由稅捐機關查定課徵 1%"),
    ("甲戌便利商店", "便利商店業", GENERAL, False,
     (24_000_000, 17_500_000, 1_500_000), (25_000_000, 18_200_000, 1_550_000),
     "對照組：加盟單店，營收、毛利與存貨週轉皆穩定"),
    ("乙亥工程行", "營造業", GENERAL, False,
     (50_000_000, 40_000_000, 0), (55_000_000, 44_000_000, 0),
     "對照組：營造業依工程進度開立發票，無商品存貨"),
    ("丙子設計工作室", "室內設計業", GENERAL, False,
     (3_000_000, 500_000, 0), (3_500_000, 600_000, 0),
     "對照組：勞務型產業，進項少、加值率高"),
    ("丁丑物流有限公司", "汽車貨運業", GENERAL, False,
     (80_000_000, 60_000_000, 2_000_000), (85_000_000, 65_000_000, 2_200_000),
     "對照組：營收與成本同步成長，各項比率穩定"),
]

def ratio(numerator: float, denominator: float) -> str:
    return f"{numerator / denominator:.4f}" if denominator else "0.0000"


def year_fields(year: int, sales: int, purchases: int, inventory: int, tax_paid: int) -> dict:
    return {
        f"yr{year}_sales": sales,
        f"yr{year}_purchases": purchases,
        f"yr{year}_inventory": inventory,
        f"yr{year}_inv_sales_ratio": ratio(inventory, sales),
        f"yr{year}_inv_purch_ratio": ratio(inventory, purchases),
        f"yr{year}_vat_rate": ratio(sales - purchases, sales),
        f"yr{year}_tax_paid": tax_paid,
    }


def tax_paid(sales: int, purchases: int, tax_method: str, zero_rated: bool, carry_in: int) -> tuple[int, int]:
    """回傳 (已繳稅額, 結轉下年度的留抵稅額)。"""
    if tax_method == ASSESSED:
        return round(sales * 0.01), 0
    if zero_rated:
        return 0, 0
    net = round((sales - purchases) * 0.05) - carry_in
    return (net, 0) if net > 0 else (0, -net)


def build_rows() -> list[dict]:
    rows = []
    for index, (name, industry, method, zero_rated, y111, y112, _scenario) in enumerate(CASES):
        ban = f"BAN{1111 + index}"
        cogs112 = y111[2] + y112[1] - y112[2]
        if cogs112 < 0:
            raise ValueError(f"{name}：112 年銷貨成本為負（{cogs112}），請調整進貨或存貨")
        tax111, carry = tax_paid(y111[0], y111[1], method, zero_rated, 0)
        tax112, _ = tax_paid(y112[0], y112[1], method, zero_rated, carry)
        rows.append({
            "ban": ban,
            "company_name": name,
            "industry_type": industry,
            **year_fields(111, *y111, tax111),
            **year_fields(112, *y112, tax112),
            "tax_method": method,
        })
    return rows


def main() -> None:
    rows = build_rows()
    with OUTPUT.open("w", encoding="utf-8", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    for row, case in zip(rows, CASES):
        print(row["ban"], row["company_name"], "—", case[-1])


if __name__ == "__main__":
    main()
