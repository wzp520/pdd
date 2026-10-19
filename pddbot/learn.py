"""从人工回复中学习：把「买家问题 + 人工回复」提炼成可复用 FAQ，存入 learned_qa.yaml。

下次遇到相同/相似问题，decide.match_qa 会命中这条学到的答案，直接自动回复。
大模型提炼时会过滤个性化/敏感信息（订单号、金额、人名等），只沉淀通用问答。
"""
from __future__ import annotations

import json
import re

import yaml

from . import config, llm

LEARNED_FILE = config.ROOT / "data" / "learned_qa.yaml"

EXTRACT_PROMPT = """你是客服知识库整理助手。给你一组「买家问题」和「人工客服的回复」，\
判断它是否适合沉淀为通用 FAQ（能复用到其他买家的相同问题）。

不适合（返回 ok=false）：
- 回复含特定订单号/快递单号/具体金额/人名/地址等个性化或一次性信息；
- 回复只是闲聊、情绪安抚，没有实质信息；
- 问题过于具体、无法复用到别的买家。

适合则提炼：keywords 是能命中同类问题的 2-5 个关键词；\
reply 是去掉个性化信息后的规范通用答案（简短、口语、不超过 60 字）。

只输出 JSON，不要解释：
适合：{"ok": true, "keywords": ["关键词1","关键词2"], "reply": "通用答案"}
不适合：{"ok": false}"""


def load_learned() -> list:
    try:
        with open(LEARNED_FILE, encoding="utf-8") as f:
            return yaml.safe_load(f) or []
    except Exception:
        return []


def save_learned(items: list) -> None:
    LEARNED_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LEARNED_FILE, "w", encoding="utf-8") as f:
        yaml.safe_dump(items, f, allow_unicode=True, sort_keys=False)


def _parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {}


async def learn_from_human(buyer_q: str, human_reply: str, log=print) -> bool:
    """用大模型判断并提炼人工回复为通用 FAQ，存入学习库。返回是否学到。"""
    if not buyer_q or not human_reply:
        return False
    try:
        raw = await llm.generate_reply(
            f"买家问题：{buyer_q}\n人工客服回复：{human_reply}",
            system_prompt=EXTRACT_PROMPT,
        )
    except Exception as e:
        log(f"[学习失败] 大模型不可用：{e}")
        return False

    data = _parse_json(raw)
    if not data.get("ok"):
        return False
    keywords = [str(k).strip() for k in data.get("keywords", []) if str(k).strip()]
    reply = str(data.get("reply", "")).strip()
    if not keywords or not reply:
        return False

    items = load_learned()
    for it in items:  # 去重：答案相同或关键词重叠则跳过
        if it.get("reply") == reply or set(it.get("keywords", [])) & set(keywords):
            return False
    items.append({"keywords": keywords, "reply": reply, "source": "learned", "from_q": buyer_q})
    save_learned(items)
    log(f"[已学习] {keywords} → {reply}")
    return True
