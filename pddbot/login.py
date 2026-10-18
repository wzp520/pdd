"""扫码登录拼多多商家后台，把登录态保存到 storage_state.json。

用法（开发期）：
    python -m pddbot.login

原理：Playwright 打开一个真实 Chromium 窗口（必须真实窗口，无头会被滑块验证
拦截）。扫码登录成功后：
  - 自动检测页面已离开登录页 → 自动保存；
  - 或你登录完成后在终端按 Enter → 手动立即保存（双保险）。
登录态存进 storage_state.json；GUI 的「扫码登录」按钮会复用 interactive_login()。
"""
from __future__ import annotations

import asyncio
import threading
import time

from playwright.async_api import async_playwright

from .config import HEADLESS, PDD_HOME, STATE_FILE


def _wait_enter(evt: threading.Event) -> None:
    try:
        input()
    except Exception:
        pass
    finally:
        evt.set()


async def interactive_login(
    headless: bool = False, timeout: float = 300.0, wait_enter: bool = True
) -> bool:
    """弹出真实浏览器让用户扫码登录，自动或手动保存登录态。

    wait_enter: True（命令行）额外支持按 Enter 手动保存；
                False（GUI）只走自动检测，避免无终端时误触发。
    返回 True=登录并保存成功，False=超时/未完成。
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(PDD_HOME, wait_until="domcontentloaded")

        print(">>> 已弹出浏览器窗口，请扫码登录（遇到滑块就用鼠标滑过去）")
        print(">>> 登录成功后会【自动保存】；若迟迟没保存，登录完成后在此按 Enter 也可手动保存")

        # 守护线程监听回车（仅命令行用；GUI 传 wait_enter=False 走纯自动检测）
        enter_evt = threading.Event()
        if wait_enter:
            threading.Thread(target=_wait_enter, args=(enter_evt,), daemon=True).start()

        deadline = time.monotonic() + timeout
        manual = False
        last_log = 0.0
        while time.monotonic() < deadline:
            if wait_enter and enter_evt.is_set():
                manual = True
                break
            await asyncio.sleep(1.0)
            try:
                url = page.url
            except Exception:
                break
            now = time.monotonic()
            if now - last_log > 5:
                print(f"[检测中] 当前页面：{url}")
                last_log = now
            if "/login" not in url:
                await asyncio.sleep(2.0)  # 二次确认，避免中转瞬间误判
                try:
                    if "/login" not in page.url:
                        break
                except Exception:
                    break

        # 判断最终是否已登录
        try:
            logged_in = "/login" not in page.url
        except Exception:
            logged_in = False

        if logged_in:
            await context.storage_state(path=str(STATE_FILE))
            print(f"\n✅ 登录成功，已保存登录态到：{STATE_FILE}")
            print(f"   （触发方式：{'手动回车' if manual else '自动检测'}）")
        else:
            print("\n⚠️  未检测到登录成功（页面仍停在登录页）。请重新运行重试。")

        await browser.close()
        return logged_in


def main() -> None:
    print("=" * 56)
    print("  拼多多商家后台 · 扫码登录并保存登录态")
    print("=" * 56)
    asyncio.run(interactive_login(headless=HEADLESS))


if __name__ == "__main__":
    main()
