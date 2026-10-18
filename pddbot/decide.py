"""决策层：决定一条买家消息怎么回复。

顺序：
  1) 命中「转人工」关键词 → 转人工
  2) 命中 FAQ 关键词 → 直接用预设话术（省钱、稳定）
  3) 其余 → 交给 DeepSeek 大模型生成
"""
from __future__ import annotations

import yaml

from . import config, llm

QA_FILE = config.ROOT / "data" / "qa.yaml"
PRODUCTS_FILE = config.ROOT / "data" / "products.yaml"

# 命中即转人工（敏感/高风险问题，不让 AI 乱答）
HUMAN_KEYWORDS = [
    "投诉", "差评", "退款", "转人工", "人工", "工商", "12315",
    "曝光", "起诉", "假货", "骗", "报警", "赔偿",
]

# 大模型回复里出现这些词，说明它自己判断需要转人工 → 升级为 human 以触发飞书通知
HUMAN_REPLY_HINTS = ["转接人工", "人工客服", "转人工", "人工为您", "人工处理", "人工帮您"]


def _load_yaml(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or default
    except Exception:
        return default


def load_qa() -> list:
    return _load_yaml(QA_FILE, [])


def save_qa(items: list) -> None:
    """把问答列表写回 qa.yaml。"""
    with open(QA_FILE, "w", encoding="utf-8") as f:
        yaml.safe_dump(items, f, allow_unicode=True, sort_keys=False)


def import_qa_from_excel(path: str) -> int:
    """从 Excel 批量导入问答。第一列=关键词（逗号/顿号分隔），第二列=回复。
    返回新增条数。首行按表头跳过。"""
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True)
    ws = wb.active
    items = load_qa()
    added = 0
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or not row[0]:
            continue
        raw = str(row[0]).replace("，", ",").replace("、", ",").replace(";", ",").replace("；", ",")
        keywords = [k.strip() for k in raw.split(",") if k.strip()]
        reply = str(row[1]).strip() if len(row) > 1 and row[1] else ""
        if keywords and reply:
            items.append({"keywords": keywords, "reply": reply})
            added += 1
    save_qa(items)
    return added


def make_qa_template(path: str) -> None:
    """生成问答导入 Excel 模板（带表头和示例行），供用户下载后照着填。"""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "问答"
    ws.append(["关键词（多个用英文逗号分隔）", "回复话术"])
    ws.append(["发货,什么时候发,几天发", "亲，48 小时内发货哦～"])
    ws.append(["退货,怎么退,能退吗", "亲，支持 7 天无理由退换哦～"])
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 40
    wb.save(path)


def load_products() -> dict:
    data = _load_yaml(PRODUCTS_FILE, {})
    return data if isinstance(data, dict) else {}


def match_qa(text: str) -> str | None:
    for item in load_qa():
        if any(k in text for k in item.get("keywords", [])):
            return item.get("reply")
    return None


def need_human(text: str) -> bool:
    return any(k in text for k in HUMAN_KEYWORDS)


def product_context(product_name: str = "") -> str:
    """组合给大模型的商品上下文：买家当前咨询的商品名 + products.yaml 里的详情。"""
    data = load_products()
    parts: list[str] = []
    if product_name:
        parts.append(f"买家当前咨询的商品：{product_name}")
        for p in data.get("products", []) or []:
            title = p.get("title", "")
            if title and (title in product_name or product_name[:8] in title):
                parts.append(f"该商品详情：{p.get('info', '')}")
                break
    default = data.get("default", "")
    if default:
        parts.append(default)
    return "\n".join(parts)


async def decide(
    buyer_msg: str, product: str = "", history: list | None = None
) -> tuple[str, str]:
    """返回 (action, reply)。action ∈ {"auto", "human"}。product=买家咨询的商品名。"""
    if need_human(buyer_msg):
        return "human", "这边帮您转接人工客服为您处理哦～"

    qa = match_qa(buyer_msg)
    if qa:
        return "auto", qa

    try:
        reply = await llm.generate_reply(
            buyer_msg, product_context=product_context(product), history=history
        )
        # 大模型若在回复里表达了转人工意图，升级为 human，以触发飞书通知
        if any(k in reply for k in HUMAN_REPLY_HINTS):
            return "human", reply
        return "auto", reply
    except Exception as e:
        # 大模型不可用时兜底转人工，绝不乱答
        return "human", f"[转人工] 自动回复暂不可用（{e}）"
