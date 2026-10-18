"""机器人主循环：轮询今日接待的会话，对最后一条是买家消息的会话自动回复。

- DRY_RUN=true 时只在日志里打印要发的回复，不实际发送（安全测试）。
- 发送后会话最后一条变成客服消息，天然不会重复回复。
"""
from __future__ import annotations

import asyncio
import random

from playwright.async_api import async_playwright

from . import chat, config, decide, feishu


class Bot:
    def __init__(self) -> None:
        self._running = False
        self.stats = {"auto": 0, "human": 0}
        self._recent_replied: set[str] = set()  # 本次运行已回复过的买家消息，防重复
        self._human_handled: set[str] = set()  # 已转人工的会话 ID，AI 不再接管

    async def run(self, on_log=None) -> None:
        """启动主循环。on_log 可选，用于把日志推给 GUI。"""
        self._running = True
        log = on_log or (lambda s: print(s))

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=config.HEADLESS)
            context = await browser.new_context(storage_state=str(config.STATE_FILE))
            page = await chat.open_chat_page(context)
            await chat.dismiss_popups(page)
            log(
                f"机器人已启动（DRY_RUN={config.DRY_RUN}，"
                f"随机轮询 {config.POLL_MIN:.0f}~{config.POLL_MAX:.0f}s）"
            )

            try:
                while self._running:
                    try:
                        await self._poll_once(page, log)
                    except Exception as e:
                        log(f"[轮询异常] {e}")
                    await asyncio.sleep(random.uniform(config.POLL_MIN, config.POLL_MAX))
            finally:
                await browser.close()
                log("机器人已停止")

    def stop(self) -> None:
        self._running = False

    async def _poll_once(self, page, log) -> None:
        try:
            await chat.switch_to_today(page)
            items = page.locator(chat.SEL_CHAT_ITEM)
            n = await items.count()
        except Exception as e:
            log(f"[本轮跳过] 读取会话列表失败：{str(e)[:80]}")
            return

        for i in range(n):
            if not self._running:
                return
            try:
                if not await chat.open_conversation(page, i):
                    continue

                conv_id = await chat.get_conversation_id(page)
                if conv_id and conv_id in self._human_handled:
                    continue  # 已转人工，交给人工处理，AI 不再接管该会话

                msg = await chat.get_pending_buyer_message(page)
                if not msg:
                    continue  # 最后一条是客服或无买家消息，无需回复

                text = msg["text"]
                dedup_key = f"{msg['kind']}:{text}"
                if dedup_key in self._recent_replied:
                    continue  # 本次运行已回复过，避免重复

                product = ""
                if msg["kind"] == "image":
                    action, reply = "auto", "亲，图片收到啦～请问有什么可以帮您的呢？"
                elif msg["kind"] == "card":
                    action, reply = (
                        "auto",
                        f"亲，这款「{text}」有什么想了解的呢？尺码、发货、优惠都可以问我哦～",
                    )
                else:  # 普通文字，带上商品上下文交给决策层
                    product = await chat.get_current_product(page)
                    action, reply = await decide.decide(text, product=product)

                self.stats[action] = self.stats.get(action, 0) + 1
                log(f"[会话{i}] 买家({msg['kind']})：{text!r} → [{action}] {reply!r}")

                if action == "auto":
                    await chat.send_reply(page, reply)
                    # 发送后随机停顿，模拟人工节奏，避免触发"操作太快"风控
                    await asyncio.sleep(random.uniform(1.5, 4.0))
                elif action == "human":
                    if conv_id:
                        self._human_handled.add(conv_id)  # 标记已转人工，后续不再接管
                    await chat.send_reply(page, reply)  # 先告知买家正在转人工
                    await asyncio.sleep(random.uniform(1.5, 4.0))
                    try:
                        await feishu.notify_human(text, product)
                    except Exception as fe:
                        log(f"[飞书通知失败] {fe}")

                self._recent_replied.add(dedup_key)
                if len(self._recent_replied) > 300:
                    self._recent_replied.clear()
            except Exception as e:
                # 单个会话出错（多为页面重渲染导致元素失效），跳过不影响整轮
                log(f"[跳过会话{i}] {type(e).__name__}: {str(e)[:80]}")
                continue


async def main() -> None:
    await Bot().run()


if __name__ == "__main__":
    asyncio.run(main())
