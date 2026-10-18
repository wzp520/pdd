"""后台首页经营数据概览（只读）。

数据来自 /home/ 页面的 Next.js 注入数据 window.__NEXT_DATA__，
里面 coreData.data.topnums / bottomnums 就是首页顶部/底部的经营指标。
"""
from __future__ import annotations

from playwright.async_api import async_playwright

from . import config

HOME_URL = "https://mms.pinduoduo.com/home/"


async def fetch_overview() -> dict:
    """抓取首页经营数据。返回 {"top": {...}, "bottom": {...}} 或 {"err": ...}。"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(storage_state=str(config.STATE_FILE))
        page = await ctx.new_page()
        try:
            await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=40000)
            await page.wait_for_timeout(3500)
            return await page.evaluate(
                """() => {
                    try {
                        const d = window.__NEXT_DATA__.props.pageProps.coreData.data;
                        return {top: d.topnums || {}, bottom: d.bottomnums || {}};
                    } catch (e) { return {err: String(e)}; }
                }"""
            )
        finally:
            await browser.close()


if __name__ == "__main__":
    import asyncio
    import json

    print(json.dumps(asyncio.run(fetch_overview()), ensure_ascii=False, indent=1))
