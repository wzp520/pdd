"""NiceGUI 控制台：控制台 / 问答管理 / 模型配置 三个标签页。

运行：python -m pddbot.gui   （自动打开浏览器到 http://localhost:8777）
打包成 exe 后双击即打开，全程无需命令行。
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from collections import deque
from datetime import datetime
from pathlib import Path

from nicegui import ui

from . import config, dashboard, decide, feishu, learn, llm, login, quicklinks
from .bot import Bot

bot = Bot()
_task: asyncio.Task | None = None
_logs: deque[str] = deque(maxlen=500)

# 模型供应商预设（选中自动填 base_url 和默认模型）
PROVIDERS = {
    "DeepSeek（推荐）": {"base": "https://api.deepseek.com", "model": "deepseek-chat"},
    "通义千问": {"base": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus"},
    "OpenAI": {"base": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
}

# 数据概览指标映射（字段名 → 中文标题）
TOP_METRICS = [
    ("payOrdrAmt", "今日支付金额"),
    ("payOrdrCnt", "支付订单"),
    ("payOrdrUsrCnt", "支付买家"),
    ("inqorUsrCnt1d", "咨询人数"),
    ("guvOned", "访客数"),
]
BOTTOM_METRICS = [
    ("waitShipNum", "待发货"),
    ("unShip12H", "超12h未发"),
    ("waitPayNum", "待付款"),
    ("aftersalesNew", "待处理售后"),
    ("unPrint", "待打单"),
    ("pendingOrder", "待处理订单"),
]


def add_log(msg: str) -> None:
    _logs.append(f"[{datetime.now():%H:%M:%S}] {msg}")


def _bot_running() -> bool:
    return _task is not None and not _task.done()


async def start_bot() -> None:
    global _task
    if _bot_running():
        add_log("机器人已在运行")
        return
    if not config.STATE_FILE.exists():
        add_log("⚠️ 还没有登录态，请先点【扫码登录】")
        return
    add_log("启动机器人…")
    _task = asyncio.create_task(bot.run(on_log=add_log))


def stop_bot() -> None:
    if _bot_running():
        bot.stop()
        add_log("已请求停止（本轮结束后退出）")
    else:
        add_log("机器人未在运行")


async def do_login() -> None:
    add_log("打开浏览器，请在弹出窗口扫码登录…")
    try:
        ok = await login.interactive_login(headless=False, wait_enter=False)
        add_log("✅ 登录成功，登录态已保存" if ok else "⚠️ 登录失败或超时，请重试")
    except Exception as e:
        add_log(f"登录异常：{e}")


@ui.page("/")
def main_page():
    ui.add_head_html("<style>body{background:#f5f6fa}</style>")
    ui.label("pddbot · 拼多多客服自动回复").classes("text-2xl font-bold q-pa-md")

    with ui.tabs().classes("w-full") as tabs:
        tab_console = ui.tab("控制台", icon="dashboard")
        tab_dash = ui.tab("数据概览", icon="insights")
        tab_links = ui.tab("快捷入口", icon="open_in_new")
        tab_qa = ui.tab("问答管理", icon="quiz")
        tab_model = ui.tab("模型配置", icon="smart_toy")
        tab_settings = ui.tab("运行设置", icon="tune")
        tab_feishu = ui.tab("飞书通知", icon="notifications")

    with ui.tab_panels(tabs, value=tab_console).classes("w-full"):
        # ===== 控制台 =====
        with ui.tab_panel(tab_console):
            with ui.card().classes("w-full"):
                with ui.row().classes("items-center gap-4"):
                    status_badge = ui.badge("已停止", color="grey")
                    dry_switch = ui.switch(
                        "DRY_RUN 干跑（只打印不真发）",
                        value=config.DRY_RUN,
                        on_change=lambda e: setattr(config, "DRY_RUN", e.value),
                    )
                with ui.row().classes("gap-2"):
                    login_btn = ui.button("扫码登录", on_click=do_login, icon="qr_code")
                    start_btn = ui.button("启动", on_click=start_bot, color="positive", icon="play_arrow")
                    stop_btn = ui.button("停止", on_click=stop_bot, color="negative", icon="stop")

            with ui.row().classes("w-full gap-4"):
                with ui.card().classes("flex-1 items-center"):
                    ui.label("今日自动回复").classes("text-sm text-gray-500")
                    auto_lbl = ui.label("0").classes("text-3xl font-bold")
                with ui.card().classes("flex-1 items-center"):
                    ui.label("今日转人工").classes("text-sm text-gray-500")
                    human_lbl = ui.label("0").classes("text-3xl font-bold")

            ui.label("运行日志").classes("text-sm text-gray-500 mt-2")
            log_view = ui.log(max_lines=500).classes("w-full h-64 bg-black text-green-400 text-xs")

            cursor = {"n": 0}

            def refresh():
                running = _bot_running()
                logged_in = config.STATE_FILE.exists()
                # 状态徽章
                status_badge.set_text("运行中" if running else "已停止")
                status_badge.props(f"color={'green' if running else 'grey'}")
                # 按钮状态反馈：运行中禁用启动/登录，未登录禁用启动，非运行禁用停止
                start_btn.set_enabled(logged_in and not running)
                stop_btn.set_enabled(running)
                login_btn.set_enabled(not running)
                # 计数
                auto_lbl.set_text(str(bot.stats.get("auto", 0)))
                human_lbl.set_text(str(bot.stats.get("human", 0)))
                # 日志增量推送
                logs = list(_logs)
                while cursor["n"] < len(logs):
                    log_view.push(logs[cursor["n"]])
                    cursor["n"] += 1

            ui.timer(1.0, refresh)

        # ===== 数据概览 =====
        with ui.tab_panel(tab_dash):
            with ui.row().classes("items-center gap-3"):
                ui.button("刷新数据", icon="refresh", on_click=lambda: refresh_dash())
                dash_time = ui.label("点「刷新数据」获取最新（需先登录）").classes("text-sm text-gray-500")

            ui.label("今日经营").classes("font-bold mt-2")
            dash_cards: dict = {}
            with ui.row().classes("w-full gap-3 flex-wrap"):
                for key, title in TOP_METRICS:
                    with ui.card().classes("items-center").style("min-width:140px"):
                        ui.label(title).classes("text-xs text-gray-500")
                        dash_cards[key] = ui.label("-").classes("text-2xl font-bold")

            ui.label("待办事项").classes("font-bold mt-2")
            with ui.row().classes("w-full gap-3 flex-wrap"):
                for key, title in BOTTOM_METRICS:
                    with ui.card().classes("items-center").style("min-width:140px"):
                        ui.label(title).classes("text-xs text-gray-500")
                        dash_cards[key] = ui.label("-").classes("text-2xl font-bold")

            async def refresh_dash():
                dash_time.set_text("加载中…")
                try:
                    data = await dashboard.fetch_overview()
                except Exception as e:
                    dash_time.set_text(f"获取失败：{e}")
                    return
                if not data or data.get("err"):
                    dash_time.set_text("获取失败，请确认已登录")
                    return
                top = data.get("top", {})
                bottom = data.get("bottom", {})
                for key, _ in TOP_METRICS:
                    dash_cards[key].set_text(str(top.get(key, 0)))
                for key, _ in BOTTOM_METRICS:
                    if key == "aftersalesNew":
                        val = (bottom.get("aftersalesNew") or {}).get("totalWaitHandleCount", 0)
                    else:
                        val = bottom.get(key, 0)
                    dash_cards[key].set_text(str(val))
                dash_time.set_text(f"更新于 {datetime.now():%H:%M:%S}")

        # ===== 快捷入口 =====
        with ui.tab_panel(tab_links):
            ui.label("点按钮用浏览器打开对应后台页（复用登录态），人工操作——安全、不触发风控。").classes(
                "text-sm text-gray-500"
            )
            ui.label(
                "说明：商品/订单等管理页反爬严格、写操作有封店风险，故不做自动操作，改为一键直达。"
            ).classes("text-xs text-gray-400")

            async def open_link(url: str):
                ui.notify("正在打开浏览器…")
                try:
                    await quicklinks.open_in_browser(url)
                except Exception as e:
                    ui.notify(f"打开失败：{e}", type="negative")

            for group, items in quicklinks.LINK_GROUPS.items():
                ui.label(group).classes("font-bold mt-2")
                with ui.row().classes("gap-2 flex-wrap"):
                    for name, url in items:
                        ui.button(name, on_click=lambda u=url: open_link(u)).props("outline")

        # ===== 问答管理 =====
        with ui.tab_panel(tab_qa):
            ui.label("命中关键词即用固定话术直接回复（不走大模型，省钱稳定）").classes("text-sm text-gray-500")

            qa_container = ui.column().classes("w-full gap-1")

            def del_qa(idx: int):
                items = decide.load_qa()
                if 0 <= idx < len(items):
                    del items[idx]
                    decide.save_qa(items)
                    render_qa()

            def render_qa():
                qa_container.clear()
                items = decide.load_qa()
                with qa_container:
                    with ui.row().classes("w-full font-bold text-gray-600 gap-2"):
                        ui.label("关键词").classes("w-64")
                        ui.label("回复话术").classes("flex-1")
                        ui.label("操作").classes("w-16")
                    if not items:
                        ui.label("暂无问答，可在下方新增或导入 Excel").classes("text-gray-400 q-pa-sm")
                    for idx, it in enumerate(items):
                        with ui.row().classes("w-full items-center gap-2 border-b"):
                            ui.label("，".join(it.get("keywords", []))).classes("w-64 truncate")
                            ui.label(it.get("reply", "")).classes("flex-1 truncate")
                            ui.button(icon="delete", on_click=lambda i=idx: del_qa(i)).props(
                                "flat dense color=negative"
                            ).classes("w-16")

            ui.separator()
            ui.label("新增单条").classes("font-bold mt-2")
            with ui.row().classes("w-full items-center gap-2"):
                kw_input = ui.input("关键词（多个用逗号分隔）").classes("w-64")
                reply_input = ui.input("回复话术").classes("flex-1")

                def add_qa():
                    kw = (kw_input.value or "").strip()
                    reply = (reply_input.value or "").strip()
                    if not kw or not reply:
                        ui.notify("关键词和回复都要填写", type="warning")
                        return
                    keywords = [k.strip() for k in kw.replace("，", ",").split(",") if k.strip()]
                    items = decide.load_qa()
                    items.append({"keywords": keywords, "reply": reply})
                    decide.save_qa(items)
                    kw_input.value = ""
                    reply_input.value = ""
                    render_qa()
                    ui.notify("已添加", type="positive")

                ui.button("添加", on_click=add_qa, icon="add")

            ui.separator()
            ui.label("批量导入 Excel（第一列=关键词，逗号分隔；第二列=回复；首行表头）").classes(
                "font-bold mt-2"
            )

            def download_template():
                p = Path(tempfile.gettempdir()) / "问答导入模板.xlsx"
                decide.make_qa_template(str(p))
                ui.download(str(p))

            def on_excel_upload(e):
                try:
                    tmp = Path(tempfile.gettempdir()) / e.name
                    tmp.write_bytes(e.content.read())
                    n = decide.import_qa_from_excel(str(tmp))
                    render_qa()
                    ui.notify(f"成功导入 {n} 条问答", type="positive")
                except Exception as ex:
                    ui.notify(f"导入失败：{ex}", type="negative")

            ui.button("下载导入模板", icon="download", on_click=download_template).props("outline")
            ui.upload(on_upload=on_excel_upload, auto_upload=True, label="选择 .xlsx 文件").props(
                "accept=.xlsx"
            ).classes("w-full max-w-md")

            render_qa()

            ui.separator()
            with ui.row().classes("items-center gap-2 mt-2"):
                ui.label("🤖 AI 从人工回复中学到的问答（自动生效，可删除纠错）").classes("font-bold")
                ui.button(icon="refresh", on_click=lambda: render_learned()).props("flat dense").tooltip("刷新")
            learned_container = ui.column().classes("w-full gap-1")

            def del_learned(idx: int):
                items = learn.load_learned()
                if 0 <= idx < len(items):
                    del items[idx]
                    learn.save_learned(items)
                    render_learned()

            def render_learned():
                learned_container.clear()
                items = learn.load_learned()
                with learned_container:
                    if not items:
                        ui.label("还没学到内容。转人工后，人工回复的通用问答会自动沉淀到这里。").classes(
                            "text-gray-400 q-pa-sm"
                        )
                    for idx, it in enumerate(items):
                        with ui.row().classes("w-full items-center gap-2 border-b"):
                            ui.label("，".join(it.get("keywords", []))).classes("w-64 truncate")
                            ui.label(it.get("reply", "")).classes("flex-1 truncate")
                            ui.button(icon="delete", on_click=lambda i=idx: del_learned(i)).props(
                                "flat dense color=negative"
                            ).classes("w-16")

            render_learned()

        # ===== 模型配置 =====
        with ui.tab_panel(tab_model):
            ui.label("大模型配置（默认 DeepSeek，兼容 OpenAI 接口的模型都可用）").classes(
                "text-sm text-gray-500"
            )
            with ui.card().classes("w-full max-w-xl gap-2"):
                def on_provider(e):
                    p = PROVIDERS.get(e.value)
                    if p:
                        base_input.value = p["base"]
                        model_input.value = p["model"]

                provider_sel = ui.select(
                    list(PROVIDERS.keys()), value="DeepSeek（推荐）", label="供应商", on_change=on_provider
                ).classes("w-full")
                key_input = ui.input(
                    "API Key", value=config.DEEPSEEK_API_KEY, password=True, password_toggle_button=True
                ).classes("w-full")
                base_input = ui.input("Base URL", value=config.DEEPSEEK_BASE_URL).classes("w-full")
                model_input = ui.input("模型名", value=config.DEEPSEEK_MODEL).classes("w-full")

                def save_model():
                    config.update_env(
                        {
                            "DEEPSEEK_API_KEY": (key_input.value or "").strip(),
                            "DEEPSEEK_BASE_URL": (base_input.value or "").strip(),
                            "DEEPSEEK_MODEL": (model_input.value or "").strip(),
                        }
                    )
                    llm.reset_client()
                    ui.notify("模型配置已保存并生效 ✅", type="positive")

                async def test_model():
                    ui.notify("正在测试…")
                    try:
                        llm.reset_client()
                        r = await llm.generate_reply("在吗")
                        ui.notify(f"测试成功：{r}", type="positive")
                    except Exception as ex:
                        ui.notify(f"测试失败：{ex}", type="negative")

                with ui.row().classes("gap-2"):
                    ui.button("保存", on_click=save_model, icon="save", color="positive")
                    ui.button("测试连接", on_click=test_model, icon="wifi")

        # ===== 运行设置 =====
        with ui.tab_panel(tab_settings):
            ui.label("运行参数（保存后对正在运行的机器人实时生效）").classes("text-sm text-gray-500")
            with ui.card().classes("w-full max-w-xl gap-2"):
                with ui.row().classes("gap-3 w-full"):
                    set_pmin = ui.number(
                        "轮询最小间隔(秒)", value=config.POLL_MIN, min=1, max=60, format="%.0f"
                    ).classes("flex-1")
                    set_pmax = ui.number(
                        "轮询最大间隔(秒)", value=config.POLL_MAX, min=1, max=120, format="%.0f"
                    ).classes("flex-1")
                set_pause = ui.number(
                    "人工静默多久后恢复AI(分钟)",
                    value=config.HUMAN_PAUSE_MINUTES,
                    min=0,
                    max=180,
                    format="%.0f",
                ).classes("w-full")
                ui.label(
                    "说明：轮询间隔越大越拟人、越安全；转人工后以人工最近一次回复计时，"
                    "只要人工还在回复就一直续期，人工静默超过此时长 AI 才恢复接管。"
                ).classes("text-xs text-gray-400")

                def save_settings():
                    pmin = int(set_pmin.value or 3)
                    pmax = int(set_pmax.value or 8)
                    if pmin > pmax:
                        ui.notify("最小间隔不能大于最大间隔", type="negative")
                        return
                    config.update_env(
                        {
                            "POLL_MIN": pmin,
                            "POLL_MAX": pmax,
                            "HUMAN_PAUSE_MINUTES": int(set_pause.value or 10),
                        }
                    )
                    ui.notify("运行设置已保存并生效 ✅", type="positive")

                ui.button("保存", on_click=save_settings, icon="save", color="positive")

        # ===== 飞书通知 =====
        with ui.tab_panel(tab_feishu):
            ui.label("转人工时把客户问题推送到飞书群并 @指定人（单向通知）").classes(
                "text-sm text-gray-500"
            )
            ui.label(
                "用法：飞书群 → 设置 → 群机器人 → 添加「自定义机器人」→ 复制 Webhook 填下面。"
                "@特定人需填对方 open_id（ou_开头），填 all 则 @所有人。"
            ).classes("text-xs text-gray-400")
            with ui.card().classes("w-full max-w-xl gap-2"):
                fs_enabled = ui.switch("启用飞书通知", value=config.FEISHU_ENABLED)
                fs_webhook = ui.input("群机器人 Webhook 地址", value=config.FEISHU_WEBHOOK).classes("w-full")
                fs_secret = ui.input(
                    "签名密钥（机器人开了「加签」才填）",
                    value=config.FEISHU_SECRET,
                    password=True,
                    password_toggle_button=True,
                ).classes("w-full")
                fs_at = ui.input(
                    "@的人 open_id（逗号分隔；填 all 为@所有人）",
                    value=",".join(config.FEISHU_AT_USERS),
                ).classes("w-full")

                def _collect_feishu():
                    return {
                        "FEISHU_ENABLED": "true" if fs_enabled.value else "false",
                        "FEISHU_WEBHOOK": (fs_webhook.value or "").strip(),
                        "FEISHU_SECRET": (fs_secret.value or "").strip(),
                        "FEISHU_AT_USERS": (fs_at.value or "").strip(),
                    }

                def save_feishu():
                    config.update_env(_collect_feishu())
                    ui.notify("飞书配置已保存 ✅", type="positive")

                async def test_feishu():
                    config.update_env(_collect_feishu())
                    ok, info = await feishu.send_text("【pddbot】测试通知：转人工提醒通道正常 ✅")
                    ui.notify(
                        f"发送成功 ✅" if ok else f"发送失败：{info}",
                        type="positive" if ok else "negative",
                    )

                with ui.row().classes("gap-2"):
                    ui.button("保存", on_click=save_feishu, icon="save", color="positive")
                    ui.button("发送测试", on_click=test_feishu, icon="send")


def run() -> None:
    show = os.getenv("PDDBOT_SHOW", "1") != "0"
    ui.run(title="pddbot 控制台", port=8777, show=show, reload=False, favicon="🤖")


if __name__ in {"__main__", "__mp_main__"}:
    run()
