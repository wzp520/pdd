"""客服接待工作台页面操作封装（Playwright async）。

选择器来自对 https://mms.pinduoduo.com/chat-merchant/index 的实地探索：
  - 接待页是 Vue + Element UI 应用
  - 会话列表项：.chat-item；消息行：.msg-list .onemsg
  - 买家消息：.onemsg 内含 .buyer-item；客服消息：.onemsg 内含 .cs-item
    （用 class 判断买卖家，比按气泡左右位置可靠得多）
  - 买家发的商品链接 = 一张商品卡片，文本形如「商品ID：xxx 商品名…」
  - 输入框：textarea.custom-scroll；发送按钮：.send-btn

拼多多随时可能改版，选择器集中放在这里，便于后续维护。
"""
from __future__ import annotations

from playwright.async_api import BrowserContext, Page

from . import config

CHAT_URL = "https://mms.pinduoduo.com/chat-merchant/index"

# ---- 选择器集中管理 ----
SEL_CHAT_ITEM = ".chat-item"          # 左侧会话列表项
SEL_MSG_ROW = ".msg-list .onemsg"     # 消息行
SEL_BUYER = ".buyer-item"             # 买家消息（行内）
SEL_CS = ".cs-item"                   # 客服消息（行内）
SEL_INPUT = "textarea.custom-scroll"  # 回复输入框
SEL_SEND_BTN = ".send-btn"            # 发送按钮


async def open_chat_page(context: BrowserContext) -> Page:
    """打开接待工作台页面，等待 SPA 渲染完成。"""
    page = await context.new_page()
    await page.goto(CHAT_URL, wait_until="domcontentloaded", timeout=40000)
    await page.wait_for_timeout(8000)  # 等 Vue 应用和会话数据加载
    return page


async def dismiss_popups(page: Page) -> None:
    """关闭各种引导弹窗/遮罩，避免挡住点击。"""
    for txt in ["今天不再提示", "我知道了", "知道了", "下次再说"]:
        try:
            await page.get_by_text(txt, exact=True).first.click(timeout=1200)
            await page.wait_for_timeout(300)
        except Exception:
            pass
    try:
        await page.evaluate(
            "() => document.querySelectorAll('.fullscreen-dialog-manager,.layer')"
            ".forEach(e => { e.style.display = 'none'; })"
        )
    except Exception:
        pass


async def switch_to_all_sessions(page: Page) -> None:
    """切到「全部会话」标签。"""
    try:
        await page.evaluate(
            "() => { const t=[...document.querySelectorAll('*')]"
            ".find(e => e.children.length===0 && (e.innerText||'').trim()==='全部会话');"
            " if(t) t.click(); }"
        )
        await page.wait_for_timeout(2500)
    except Exception:
        pass


async def switch_to_today(page: Page) -> None:
    """切到「今日接待」标签（机器人日常接待看这里的新消息）。"""
    try:
        await page.evaluate(
            "() => { const t=[...document.querySelectorAll('*')]"
            ".find(e => e.children.length===0 && (e.innerText||'').trim()==='今日接待');"
            " if(t) t.click(); }"
        )
        await page.wait_for_timeout(2000)
    except Exception:
        pass


async def open_conversation(page: Page, index: int) -> bool:
    """按序号打开会话，先滚动到可见，再真实点击，失败则 JS 兜底。"""
    loc = page.locator(SEL_CHAT_ITEM).nth(index)
    try:
        await loc.scroll_into_view_if_needed(timeout=3000)
    except Exception:
        pass
    try:
        await loc.click(force=True, timeout=4000)
    except Exception:
        try:
            await loc.evaluate("el => el.click()")
        except Exception:
            return False
    await page.wait_for_timeout(1200)
    return True


async def get_conversation_id(page: Page) -> str:
    """当前打开会话的唯一标识（优先 data-random 里的数字，兜底买家昵称）。
    用于标记"已转人工"的会话，避免 AI 之后再去抢答。"""
    return await page.evaluate(
        r"""() => {
            const el = document.querySelector(
                '.chat-item-box.active, .chat-item-box[selecting="true"], .chat-item.active'
            );
            if (!el) return '';
            const dr = el.getAttribute('data-random') || '';
            const m = dr.match(/^(\d+)/);
            if (m) return m[1];
            return (el.innerText || '').trim().split(/\s+/)[0] || '';
        }"""
    )


async def get_current_product(page: Page) -> str:
    """提取买家当前咨询的商品名（买家发的商品卡片 / 系统「来自商品详情页」）。"""
    return await page.evaluate(
        r"""() => {
            const rows=[...document.querySelectorAll('.msg-list .onemsg')];
            for(let i=rows.length-1;i>=0;i--){
                const t=(rows[i].innerText||'').replace(/\s+/g,' ').trim();
                let m=t.match(/商品ID[：:]\s*\d+\s*复制?\s*(.+)/);
                if(m) return m[1].slice(0,80);
                m=t.match(/当前用户来自\s*商品详情页\s*(.+)/);
                if(m) return m[1].slice(0,80);
            }
            return '';
        }"""
    )


async def get_pending_buyer_message(page: Page) -> dict | None:
    """返回当前会话待回复的买家消息，或 None（最后一条是客服/无买家消息）。

    返回 {"kind": "text"|"card"|"image", "text": ...}。
    从最后一条往上找：先遇到客服(.cs-item)说明已回复 → None；
    先遇到买家(.buyer-item)则判断类型返回。
    """
    return await page.evaluate(
        r"""() => {
            const rows=[...document.querySelectorAll('.msg-list .onemsg')];
            for(let i=rows.length-1;i>=0;i--){
                const c=rows[i].querySelector('.cs-item');
                const b=rows[i].querySelector('.buyer-item');
                if(c) return null;                      // 最后的实质消息是客服
                if(b){
                    const t=(b.innerText||'').replace(/\s+/g,' ').trim();
                    if(!t) return {kind:'image', text:''};
                    if(/商品ID[：:]\s*\d+/.test(t)){
                        return {kind:'card',
                                text:t.replace(/.*?商品ID[：:]\s*\d+\s*复制?\s*/,'').slice(0,80)};
                    }
                    return {kind:'text', text:t};
                }
                // 系统消息，继续向上找
            }
            return null;
        }"""
    )


async def _dismiss_chat_modals(page: Page) -> None:
    """隐藏遮挡输入框的各种弹窗/遮罩层（通知弹窗、引导层、Element 对话框）。

    只隐藏遮罩、绝不点击任何按钮——之前批量点击"关闭/取消"文字按钮、以及对被
    覆盖的输入框做 force 点击，会误触发页面功能（如弹出"帮消费者申请售后"）。
    """
    try:
        await page.evaluate(
            """() => {
                document.querySelectorAll(
                    '.modal,.modal-box,.el-dialog__wrapper,.el-dialog,.layer,.fullscreen-dialog-manager,.el-overlay'
                ).forEach(e => { e.style.display = 'none'; });
            }"""
        )
        await page.wait_for_timeout(200)
    except Exception:
        pass


async def fill_reply(page: Page, text: str) -> None:
    """把回复填进输入框（不发送）。先隐藏遮挡弹窗，再普通点击聚焦、一次性填入并触发 Vue v-model。"""
    await _dismiss_chat_modals(page)
    box = page.locator(SEL_INPUT).first
    await box.click()  # 遮罩已隐藏，普通点击即可落到输入框，不再用 force（避免误点覆盖层）
    await box.fill(text)  # 一次性填入，不逐字，避免密集键盘事件触发"操作太快"
    await box.dispatch_event("input")  # 手动触发 input 事件，让 Vue 的 v-model 同步


async def send_reply(page: Page, text: str) -> bool:
    """发送一条回复。DRY_RUN 下只打印不发送。返回是否真的发送了。"""
    if config.DRY_RUN:
        print(f"[DRY_RUN] 将回复：{text!r}（干跑模式，未实际发送）")
        return False
    await fill_reply(page, text)
    await page.locator(SEL_SEND_BTN).first.click()
    # 确认回复已上屏（最后一条变成客服消息）再返回，避免重复回复
    for _ in range(16):
        await page.wait_for_timeout(500)
        try:
            if await get_pending_buyer_message(page) is None:
                return True
        except Exception:
            pass  # 发送后页面正在刷新，读取失败属正常，继续等待确认
    return True


# ---------- 自测：只读验证读取待回复买家消息 ----------
async def _selftest() -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(storage_state=str(config.STATE_FILE))
        page = await open_chat_page(context)
        await dismiss_popups(page)
        await switch_to_all_sessions(page)
        ok = await open_conversation(page, 0)
        print("打开会话:", ok)
        print("当前商品:", repr(await get_current_product(page)))
        print("待回复买家消息:", await get_pending_buyer_message(page))
        await browser.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(_selftest())
