"""飞书群自定义机器人通知（单向）。

用途：机器人判定「转人工」时，把客户问题推送到飞书群并 @指定的人。
只需群里添加一个「自定义机器人」，复制它的 Webhook 地址填到设置里即可。
@特定人需要对方的 open_id（ou_xxx）；填 all 则 @所有人。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time

import httpx

from . import config


def _sign(secret: str, timestamp: str) -> str:
    """自定义机器人「加签」校验。"""
    string_to_sign = f"{timestamp}\n{secret}"
    code = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(code).decode("utf-8")


def _build_at() -> str:
    users = config.FEISHU_AT_USERS
    if not users:
        return ""
    if users == ["all"]:
        return '<at user_id="all">所有人</at>'
    return "".join(f'<at user_id="{u}"></at>' for u in users)


async def send_text(text: str) -> tuple[bool, str]:
    """往飞书群发一条文本消息。返回 (是否成功, 详情)。"""
    webhook = config.FEISHU_WEBHOOK
    if not webhook:
        return False, "未配置飞书 Webhook"
    payload: dict = {"msg_type": "text", "content": {"text": text}}
    if config.FEISHU_SECRET:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = _sign(config.FEISHU_SECRET, ts)
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(webhook, json=payload)
            data = r.json()
            ok = data.get("StatusCode") == 0 or data.get("code") == 0
            return ok, str(data)
    except Exception as e:
        return False, str(e)


def _buyer_line(buyer: str, conv_id: str = "") -> str:
    """买家昵称为主，会话 ID 作备查。"""
    if buyer and conv_id:
        return f"买家：{buyer}（会话 {conv_id}）"
    return f"买家：{buyer or '未知'}" if not conv_id else f"会话：{conv_id}"


async def notify_human(buyer_msg: str, product: str = "", buyer: str = "", conv_id: str = "") -> None:
    """转人工时推送提醒到飞书群（未启用或未配置时静默跳过）。"""
    if not config.FEISHU_ENABLED or not config.FEISHU_WEBHOOK:
        return
    lines = ["🔔 有客户需要人工处理", _buyer_line(buyer, conv_id), f"买家问题：{buyer_msg}"]
    if product:
        lines.append(f"咨询商品：{product}")
    at = _build_at()
    if at:
        lines.append(at)
    await send_text("\n".join(lines))


async def notify_bargain(
    conv_id: str, product: str, offer: float, buyer_msg: str, buyer: str = ""
) -> None:
    """机器人答应让价后，推送飞书提醒人工改价/发券兑现（未启用时静默跳过）。"""
    if not config.FEISHU_ENABLED or not config.FEISHU_WEBHOOK:
        return
    lines = [
        f"💰 机器人已承诺让价 {offer:g} 元，请尽快改价/发券兑现",
        _buyer_line(buyer, conv_id),
        f"买家原话：{buyer_msg}",
    ]
    if product:
        lines.append(f"商品：{product}")
    at = _build_at()
    if at:
        lines.append(at)
    await send_text("\n".join(lines))
