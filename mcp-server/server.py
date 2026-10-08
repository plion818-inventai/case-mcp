import ast
import csv
import difflib
import operator
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

DATA_FILE = Path(__file__).parent / "case_data.csv"
TAX_RULES_FILE = Path(__file__).parent / "tax_rules.csv"

mcp = FastMCP("case-search-light")

READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)

# 案件資料欄位說明，讓 LLM 讀得懂 yr111_* 這類縮寫。
CASE_FIELD_DESCRIPTIONS = {
    "ban": "統一編號",
    "company_name": "公司名稱",
    "industry_type": "行業別",
    "yr{y}_sales": "{y} 年度銷項金額",
    "yr{y}_purchases": "{y} 年度進項金額",
    "yr{y}_inventory": "{y} 年度存貨金額",
    "yr{y}_inv_sales_ratio": "{y} 年度存貨對銷項比率",
    "yr{y}_inv_purch_ratio": "{y} 年度存貨對進項比率",
    "yr{y}_vat_rate": "{y} 年度加值率",
    "yr{y}_tax_paid": "{y} 年度已繳營業稅額",
    "tax_method": "課稅方式",
}

SUMMARY_FIELDS = ("ban", "company_name", "industry_type", "tax_method")

# 識別碼欄位一律保留字串：統編是純數字，轉成 int 會掉前導 0。
TEXT_FIELDS = frozenset({"ban", "rule_id"})


def _normalize_value(value: str | None) -> int | float | str | None:
    if value is None:
        return None

    value = value.strip()
    if value == "":
        return None

    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def _read_csv_rows(file_path: Path) -> list[dict]:
    try:
        with file_path.open("r", encoding="utf-8-sig", newline="") as file_obj:
            reader = csv.DictReader(file_obj)
            return [
                {
                    key: value.strip() if key in TEXT_FIELDS else _normalize_value(value)
                    for key, value in row.items()
                }
                for row in reader
            ]
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Missing file: {file_path}") from exc


def _load_case_data() -> list[dict]:
    return _read_csv_rows(DATA_FILE)


def _load_tax_rules() -> list[dict]:
    return _read_csv_rows(TAX_RULES_FILE)


def _describe_field(field: str) -> str | None:
    if field in CASE_FIELD_DESCRIPTIONS:
        return CASE_FIELD_DESCRIPTIONS[field]
    if field.startswith("yr") and "_" in field:
        year, rest = field[2:].split("_", 1)
        template = CASE_FIELD_DESCRIPTIONS.get(f"yr{{y}}_{rest}")
        if template and year.isdigit():
            return template.format(y=year)
    return None


def _find_case_by_ban(ban: str) -> dict | None:
    target_ban = ban.strip().upper()
    for row in _load_case_data():
        row_ban = row.get("ban")
        if isinstance(row_ban, str) and row_ban.upper() == target_ban:
            return row
    return None


def _case_summary(row: dict) -> dict:
    return {field: row.get(field) for field in SUMMARY_FIELDS}


def _suggest_cases(query: str, limit: int = 5) -> list[dict]:
    """依統編或公司名稱給出最接近的候選案件。"""
    rows = _load_case_data()
    query = query.strip().upper()
    scored = []
    for row in rows:
        candidates = [str(row.get("ban") or "").upper(), str(row.get("company_name") or "").upper()]
        score = max(difflib.SequenceMatcher(None, query, c).ratio() for c in candidates)
        if any(query and query in c for c in candidates):
            score = max(score, 0.9)
        scored.append((score, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [_case_summary(row) for score, row in scored[:limit] if score >= 0.4]


def _available_tax_types() -> list[str]:
    seen: list[str] = []
    for row in _load_tax_rules():
        tax_type = row.get("tax_type")
        if isinstance(tax_type, str) and tax_type not in seen:
            seen.append(tax_type)
    return seen


# ---------------------------------------------------------------------------
# 規則條件求值：只允許算術、比較、AND/OR/NOT 與 abs()，不使用 eval。
# ---------------------------------------------------------------------------

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}
_CMP_OPS = {
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
}
_FUNCS = {"abs": abs}


class RuleEvaluationError(Exception):
    pass


def _to_python_expr(condition: str) -> str:
    expr = condition
    for keyword, replacement in ((" AND ", " and "), (" OR ", " or "), ("NOT ", "not ")):
        expr = expr.replace(keyword, replacement)
    return expr


def _eval_node(node: ast.AST, variables: dict[str, Any]) -> Any:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, variables)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in variables:
            raise RuleEvaluationError(f"未知欄位：{node.id}")
        value = variables[node.id]
        if not isinstance(value, (int, float)):
            raise RuleEvaluationError(f"欄位 {node.id} 不是數值（值為 {value!r}）")
        return value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        left = _eval_node(node.left, variables)
        right = _eval_node(node.right, variables)
        if isinstance(node.op, ast.Div) and right == 0:
            raise RuleEvaluationError("條件中出現除以零")
        return _BIN_OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand, variables)
        if isinstance(node.op, ast.USub):
            return -operand
        if isinstance(node.op, ast.Not):
            return not operand
    if isinstance(node, ast.BoolOp):
        # 短路求值：讓 `x > 0 AND y / x > 1` 這類前置條件能擋住後面的除法。
        if isinstance(node.op, ast.And):
            return all(_eval_node(v, variables) for v in node.values)
        return any(_eval_node(v, variables) for v in node.values)
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, variables)
        for op, comparator in zip(node.ops, node.comparators):
            if type(op) not in _CMP_OPS:
                break
            right = _eval_node(comparator, variables)
            if not _CMP_OPS[type(op)](left, right):
                return False
            left = right
        else:
            return True
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _FUNCS
        and len(node.args) == 1
        and not node.keywords
    ):
        return _FUNCS[node.func.id](_eval_node(node.args[0], variables))
    raise RuleEvaluationError(f"不支援的條件語法：{ast.dump(node)[:80]}")


def _evaluate_condition(condition: str, variables: dict[str, Any]) -> bool:
    try:
        tree = ast.parse(_to_python_expr(condition), mode="eval")
    except SyntaxError as exc:
        raise RuleEvaluationError(f"條件語法錯誤：{exc.msg}") from exc
    return bool(_eval_node(tree, variables))


def _condition_fields(condition: str) -> list[str]:
    try:
        tree = ast.parse(_to_python_expr(condition), mode="eval")
    except SyntaxError:
        return []
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id not in _FUNCS and node.id not in names:
            names.append(node.id)
    return names


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
def list_cases(
    keyword: Annotated[
        str | None,
        Field(description="關鍵字，比對統一編號、公司名稱或行業別（部分比對、不分大小寫）。留空則列出全部。"),
    ] = None,
    industry_type: Annotated[
        str | None,
        Field(description="依行業別篩選（部分比對），例如「批發」「零售」。"),
    ] = None,
    limit: Annotated[int, Field(ge=1, le=100, description="最多回傳幾筆。")] = 20,
) -> dict:
    """搜尋或列出案件清單（只回傳摘要：統編、公司名稱、行業別、課稅方式）。

    使用者只給公司名稱、部分名稱或行業時，先用本工具找到統一編號，
    再用 search_case 取得該案件的完整財務資料。
    """
    rows = _load_case_data()
    kw = (keyword or "").strip().upper()
    industry = (industry_type or "").strip().upper()

    matched = []
    for row in rows:
        haystack = " ".join(
            str(row.get(field) or "") for field in ("ban", "company_name", "industry_type")
        ).upper()
        if kw and kw not in haystack:
            continue
        if industry and industry not in str(row.get("industry_type") or "").upper():
            continue
        matched.append(_case_summary(row))

    result: dict[str, Any] = {
        "status": "success" if matched else "not_found",
        "total": len(matched),
        "returned": min(len(matched), limit),
        "cases": matched[:limit],
    }
    if not matched and kw:
        result["suggestions"] = _suggest_cases(kw)
        result["message"] = f"找不到符合「{keyword}」的案件，可參考 suggestions 中的相近案件。"
    elif not matched:
        result["message"] = "沒有符合條件的案件。"
    return result


@mcp.tool(annotations=READ_ONLY)
def search_case(
    ban: Annotated[str, Field(description="案件的統一編號，例如 BAN1111（不分大小寫）。")],
) -> dict:
    """以統一編號查詢單一案件的完整資料，包含 111、112 年度的銷項、進項、存貨、
    存貨對銷項／進項比率、加值率、已繳稅額與課稅方式。

    回傳的 field_descriptions 說明每個欄位的中文意義。
    不知道統編時，先用 list_cases 以公司名稱搜尋。
    """
    if not ban or not ban.strip():
        raise ValueError("ban（統一編號）為必填")

    case_info = _find_case_by_ban(ban)
    if case_info is None:
        return {
            "status": "not_found",
            "message": f"找不到統一編號 {ban} 的案件，可參考 suggestions 或改用 list_cases 搜尋。",
            "ban": ban,
            "suggestions": _suggest_cases(ban),
        }

    return {
        "status": "success",
        "case": case_info,
        "field_descriptions": {
            field: desc for field in case_info if (desc := _describe_field(field))
        },
    }


@mcp.tool(annotations=READ_ONLY)
def get_risk_rules(
    tax_type: Annotated[
        str | None,
        Field(description="稅目，例如「營業稅」。留空則回傳全部規則與可用稅目。"),
    ] = None,
) -> dict:
    """查詢查核風險規則清單：規則編號、名稱、說明、風險等級、判斷條件與建議查核作為。

    只列出規則本身；要判斷某個案件命中哪些規則，請用 assess_case_risk。
    """
    rules = _load_tax_rules()
    available = _available_tax_types()

    if tax_type and tax_type.strip():
        target = tax_type.strip()
        rules = [row for row in rules if row.get("tax_type") == target]
        if not rules:
            return {
                "status": "not_found",
                "message": f"找不到稅目「{target}」的規則。",
                "tax_type": target,
                "available_tax_types": available,
                "rules": [],
            }

    return {
        "status": "success",
        "tax_type": tax_type,
        "available_tax_types": available,
        "rule_count": len(rules),
        "rules": rules,
    }


@mcp.tool(annotations=READ_ONLY)
def assess_case_risk(
    ban: Annotated[str, Field(description="要評估的案件統一編號，例如 BAN1111。")],
    tax_type: Annotated[
        str | None,
        Field(description="只評估這個稅目的規則，例如「營業稅」。留空則評估全部規則。"),
    ] = None,
) -> dict:
    """以案件的實際資料逐條比對風險規則，回傳命中的規則、每條規則用到的欄位值與建議查核作為。

    結果依風險等級（High → Medium → Low）排序。無法判斷的規則（如資料缺漏、除以零）
    會列在 unable_to_evaluate，不會當成未命中。
    """
    if not ban or not ban.strip():
        raise ValueError("ban（統一編號）為必填")

    case_info = _find_case_by_ban(ban)
    if case_info is None:
        return {
            "status": "not_found",
            "message": f"找不到統一編號 {ban} 的案件。",
            "ban": ban,
            "suggestions": _suggest_cases(ban),
        }

    rules = _load_tax_rules()
    if tax_type and tax_type.strip():
        rules = [row for row in rules if row.get("tax_type") == tax_type.strip()]
        if not rules:
            return {
                "status": "not_found",
                "message": f"找不到稅目「{tax_type}」的規則。",
                "available_tax_types": _available_tax_types(),
            }

    triggered, not_triggered, unable = [], [], []
    for rule in rules:
        condition = str(rule.get("logic_condition") or "")
        evidence = {field: case_info.get(field) for field in _condition_fields(condition)}
        base = {
            "rule_id": rule.get("rule_id"),
            "rule_name": rule.get("rule_name"),
            "risk_category": rule.get("risk_category"),
        }
        try:
            hit = _evaluate_condition(condition, case_info)
        except RuleEvaluationError as exc:
            unable.append({**base, "reason": str(exc), "evidence": evidence})
            continue
        if hit:
            triggered.append(
                {
                    **base,
                    "description": rule.get("description"),
                    "logic_condition": condition,
                    "evidence": evidence,
                    "suggested_action": rule.get("suggested_action"),
                }
            )
        else:
            not_triggered.append(base)

    severity_order = {"High": 0, "Medium": 1, "Low": 2}
    triggered.sort(key=lambda r: severity_order.get(str(r["risk_category"]), 99))

    return {
        "status": "success",
        "case": _case_summary(case_info),
        "evaluated_rule_count": len(rules),
        "triggered_count": len(triggered),
        "highest_risk": triggered[0]["risk_category"] if triggered else None,
        "triggered_rules": triggered,
        "not_triggered_rules": not_triggered,
        "unable_to_evaluate": unable,
    }


@mcp.tool(annotations=READ_ONLY)
def math_calculate(
    operation: Annotated[
        Literal["add", "subtract", "multiply", "divide"],
        Field(description="運算：add 加、subtract 減、multiply 乘、divide 除。"),
    ],
    a: Annotated[float, Field(description="第一個運算元。")],
    b: Annotated[float, Field(description="第二個運算元。")],
) -> dict:
    """對兩個數字做加減乘除，用於計算比率、差額等需要精確數值的場合。"""
    if operation == "add":
        result, symbol = a + b, "+"
    elif operation == "subtract":
        result, symbol = a - b, "-"
    elif operation == "multiply":
        result, symbol = a * b, "*"
    elif operation == "divide":
        if b == 0:
            raise ValueError("除數不可為 0")
        result, symbol = a / b, "/"
    else:
        raise ValueError("operation 只能是 add、subtract、multiply、divide")

    return {
        "operation": operation,
        "operands": {"a": a, "b": b},
        "result": result,
        "expression": f"{a} {symbol} {b} = {result}",
    }


if __name__ == "__main__":
    mcp.run()
