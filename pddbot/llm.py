"""DeepSeek 封装：根据买家消息 + 商品上下文生成客服回复。

DeepSeek 完全兼容 OpenAI 的接口，所以直接用 openai 的 AsyncOpenAI 客户端，
只是把 base_url 指到 https://api.deepseek.com。
"""
from __future__ import annotations

from openai import AsyncOpenAI

from . import config

# 客服人设 / 系统提示词。可按店铺风格修改。
DEFAULT_SYSTEM_PROMPT = """你是一名专业、热情的拼多多网店客服。请遵守：
1. 用简短口语化中文回复，语气亲切，适当用"亲""哦""呢""～"，但不要太啰嗦。
2. 只回答和本店商品、订单、物流、售后相关的问题。
3. 不确定、不知道、涉及价格改动/退款金额/投诉等敏感问题时，不要乱承诺，
   回复"这边帮您转接人工客服为您处理哦～"。
4. 不要暴露你是 AI 或机器人。
5. 单条回复不超过 60 字。
"""

_client: AsyncOpenAI | None = None


def reset_client() -> None:
    """配置变更后调用，使下次请求用新配置重建客户端。"""
    global _client
    _client = None


def check_api_key(key: str) -> str | None:
    """校验 API Key 格式，有问题返回中文原因，没问题返回 None。

    HTTP 请求头只能是 ASCII：Key 里混入中文（如没替换模板里的「sk-你的key」）、
    全角字符或空格，会在发请求时报 'ascii' codec can't encode。
    """
    key = (key or "").strip()
    if not key:
        return "未配置 API Key，请在「模型配置」页填写"
    if not key.isascii():
        return "API Key 含中文或全角字符（可能还是模板里的「sk-你的key」），请填入真实 Key"
    if any(c.isspace() for c in key):
        return "API Key 中间含空格，请检查是否复制完整"
    return None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        err = check_api_key(config.DEEPSEEK_API_KEY)
        if err:
            raise RuntimeError(err)
        _client = AsyncOpenAI(
            api_key=config.DEEPSEEK_API_KEY.strip(),
            base_url=config.DEEPSEEK_BASE_URL,
            timeout=30,
        )
    return _client


async def generate_reply(
    user_message: str,
    product_context: str = "",
    history: list[dict] | None = None,
    system_prompt: str | None = None,
) -> str:
    """调用 DeepSeek 生成一条回复。

    user_message: 买家最新消息
    product_context: 当前咨询商品的关键信息（标题/价格/规格/常见问题）
    history: 之前的对话 [{"role": "user"/"assistant", "content": "..."}]
    """
    client = _get_client()

    system = system_prompt or DEFAULT_SYSTEM_PROMPT
    if product_context:
        system += f"\n\n【当前商品信息】\n{product_context}"

    messages: list[dict] = [{"role": "system", "content": system}]
    if history:
        messages.extend(history[-6:])  # 只带最近几轮，省 token
    messages.append({"role": "user", "content": user_message})

    resp = await client.chat.completions.create(
        model=config.DEEPSEEK_MODEL,
        messages=messages,
        temperature=0.7,
        max_tokens=200,
    )
    return (resp.choices[0].message.content or "").strip()
