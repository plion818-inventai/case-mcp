import csv
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp-server"))

import server  # noqa: E402


def test_list_cases_by_company_name():
    result = server.list_cases(keyword="甲申五金")
    assert result["status"] == "success"
    assert [c["ban"] for c in result["cases"]] == ["BAN1111"]


def test_list_cases_by_industry():
    result = server.list_cases(industry_type="零售")
    assert result["total"] == 2
    assert all("零售" in c["industry_type"] for c in result["cases"])


def test_list_cases_all_and_limit():
    assert server.list_cases()["total"] == 18
    assert server.list_cases(limit=3)["returned"] == 3


def test_list_cases_not_found_gives_suggestions():
    result = server.list_cases(keyword="甲申五金批發公司")
    assert result["status"] == "not_found"
    assert result["suggestions"][0]["ban"] == "BAN1111"


def test_search_case_case_insensitive_with_field_descriptions():
    result = server.search_case(" ban1111 ")
    assert result["status"] == "success"
    assert result["case"]["company_name"] == "甲申五金批發有限公司"
    assert result["field_descriptions"]["yr112_vat_rate"] == "112 年度加值率"


def test_search_case_not_found_is_structured():
    result = server.search_case("BAN9999")
    assert result["status"] == "not_found"
    assert "suggestions" in result


def test_search_case_requires_ban():
    with pytest.raises(ValueError):
        server.search_case("  ")


def test_get_risk_rules_all_and_filtered():
    all_rules = server.get_risk_rules()
    assert all_rules["rule_count"] == 7
    assert all_rules["available_tax_types"] == ["營業稅"]
    assert server.get_risk_rules("營業稅")["rule_count"] == 7
    missing = server.get_risk_rules("所得稅")
    assert missing["status"] == "not_found"
    assert missing["available_tax_types"] == ["營業稅"]


def test_assess_case_risk_r001_phantom_inventory():
    result = server.assess_case_risk("BAN1111")
    ids = [r["rule_id"] for r in result["triggered_rules"]]
    assert "R001" in ids
    r001 = next(r for r in result["triggered_rules"] if r["rule_id"] == "R001")
    assert r001["evidence"]["yr112_inv_sales_ratio"] == 4.8333
    assert result["highest_risk"] == "High"


def test_assess_case_risk_partitions_all_rules():
    for row in server.list_cases(limit=100)["cases"]:
        result = server.assess_case_risk(row["ban"])
        counted = (
            len(result["triggered_rules"])
            + len(result["not_triggered_rules"])
            + len(result["unable_to_evaluate"])
        )
        assert counted == result["evaluated_rule_count"] == 7


def test_assess_case_risk_not_found():
    assert server.assess_case_risk("NOPE")["status"] == "not_found"


@pytest.mark.parametrize(
    "condition,variables,expected",
    [
        ("a > 1 AND b < 2", {"a": 2, "b": 1}, True),
        ("a > 1 OR b > 5", {"a": 0, "b": 1}, False),
        ("abs(a - b) / a < 0.1", {"a": 100, "b": 95}, True),
        ("NOT a > 1", {"a": 0}, True),
        ("a > 0 AND b / a > 1", {"a": 0, "b": 5}, False),
        ("a == 0 OR b / a > 1", {"a": 0, "b": 5}, True),
    ],
)
def test_evaluate_condition(condition, variables, expected):
    assert server._evaluate_condition(condition, variables) is expected


@pytest.mark.parametrize(
    "condition,variables",
    [
        ("a / b > 1", {"a": 1, "b": 0}),
        ("a > 1", {}),
        ("a > 1", {"a": None}),
        ("__import__('os').system('id')", {}),
        ("a.__class__ > 1", {"a": 1}),
    ],
)
def test_evaluate_condition_rejects_unsafe_or_invalid(condition, variables):
    with pytest.raises(server.RuleEvaluationError):
        server._evaluate_condition(condition, variables)


def test_all_rule_conditions_are_parsable():
    with server.TAX_RULES_FILE.open(encoding="utf-8-sig") as f:
        for rule in csv.DictReader(f):
            assert server._condition_fields(rule["logic_condition"]), rule["rule_id"]


# 每個 demo 案件預期命中的規則；資料或規則變動時要同步更新這張表。
EXPECTED_HITS = {
    "甲申五金批發有限公司": ["R001", "R002"],
    "乙酉電子材料行": ["R002"],
    "丙戌國際實業有限公司": ["R006", "R007"],
    "丁亥精密商行": ["R001", "R003"],
    "丁卯顧問有限公司": ["R004", "R005"],
    "戊寅家電批發有限公司": ["R003"],
    "己卯開發有限公司": ["R005"],
    "庚辰貿易有限公司": ["R007"],
    "戊辰服飾精品店": [],
    "己巳建材行": [],
    "庚午餐飲股份有限公司": [],
    "辛未進出口有限公司": [],
    "壬申科技股份有限公司": [],
    "癸酉文具店": [],
    "甲戌便利商店": [],
    "乙亥工程行": [],
    "丙子設計工作室": [],
    "丁丑物流有限公司": [],
}


def test_demo_cases_hit_expected_rules():
    cases = server.list_cases(limit=100)["cases"]
    assert sorted(c["company_name"] for c in cases) == sorted(EXPECTED_HITS)
    for case in cases:
        result = server.assess_case_risk(case["ban"])
        assert result["unable_to_evaluate"] == [], case
        hits = sorted(r["rule_id"] for r in result["triggered_rules"])
        assert hits == EXPECTED_HITS[case["company_name"]], case["company_name"]


def test_demo_bans_follow_poc_format():
    # Langflow orchestration prompt 規定格式：3 位英文字 + 4 位數字
    for case in server.list_cases(limit=100)["cases"]:
        assert re.fullmatch(r"BAN\d{4}", case["ban"]), case


def test_demo_derived_fields_are_consistent():
    for case in server.list_cases(limit=100)["cases"]:
        row = server.search_case(case["ban"])["case"]
        for y in (111, 112):
            sales, purch, inv = row[f"yr{y}_sales"], row[f"yr{y}_purchases"], row[f"yr{y}_inventory"]
            if sales:
                assert row[f"yr{y}_vat_rate"] == pytest.approx((sales - purch) / sales, abs=1e-4)
                assert row[f"yr{y}_inv_sales_ratio"] == pytest.approx(inv / sales, abs=1e-4)
        # 銷貨成本 = 期初存貨 + 本期進貨 − 期末存貨，不得為負
        assert row["yr111_inventory"] + row["yr112_purchases"] - row["yr112_inventory"] >= 0, case
