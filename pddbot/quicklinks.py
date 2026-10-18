"""管理页快捷入口：用有头浏览器（带登录态）打开后台页面，供人工操作。

为什么不自动化这些页：商品/订单/售后等管理页对无头浏览器反爬严格（滑块），
且改价/上下架/发货属写操作，程序化高频操作易触发风控、有封店风险。
快捷入口 = 人工操作，安全不触发风控，同时复用已保存的登录态免重复登录。
"""
from __future__ import annotations

from . import config

LINK_GROUPS: dict[str, list[tuple[str, str]]] = {
    "商品": [
        ("商品列表", "https://mms.pinduoduo.com/goods/goods_list"),
        ("发布新商品", "https://mms.pinduoduo.com/goods/category"),
        ("价格管理", "https://mms.pinduoduo.com/kit/goods-price-management"),
        ("评价管理", "https://mms.pinduoduo.com/goods/evaluation/index"),
    ],
    "订单 / 发货": [
        ("订单查询", "https://mms.pinduoduo.com/orders/list"),
        ("发货中心", "https://mms.pinduoduo.com/express/batch"),
        ("物流工具", "https://mms.pinduoduo.com/orders/order/carriage/list"),
        ("订单开票", "https://mms.pinduoduo.com/invoice/center"),
    ],
    "售后": [
        ("售后工作台", "https://mms.pinduoduo.com/aftersales/aftersale_list"),
        ("工单管理", "https://mms.pinduoduo.com/aftersales/work_order/list"),
        ("极速退款", "https://mms.pinduoduo.com/aftersales/speed_refund"),
    ],
    "营销": [
        ("营销活动", "https://mms.pinduoduo.com/act/index"),
        ("商品推广", "https://yingxiao.pinduoduo.com/goods/promotion/list"),
        ("全站营销", "https://mms.pinduoduo.com/kit/full-site-marketing"),
        ("短信营销", "https://mms.pinduoduo.com/tool/sms"),
    ],
    "其他": [
        ("后台首页", "https://mms.pinduoduo.com/home/"),
        ("客服工作台", "https://mms.pinduoduo.com/chat-merchant/index"),
    ],
}

_pw = None
_browser = None
_context = None


async def open_in_browser(url: str) -> None:
    """用一个持久的有头浏览器（带登录态）打开 url 的新标签。"""
    global _pw, _browser, _context
    from playwright.async_api import async_playwright

    if _browser is None or not _browser.is_connected():
        _pw = await async_playwright().start()
        _browser = await _pw.chromium.launch(headless=False)
        state = str(config.STATE_FILE) if config.STATE_FILE.exists() else None
        _context = await _browser.new_context(storage_state=state)
    page = await _context.new_page()
    await page.goto(url, wait_until="domcontentloaded")
