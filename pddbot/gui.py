"""NiceGUI 控制台：控制台 / 数据概览 / 利润测算 / 问答管理 / 模型配置等标签页。

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

from . import bargain, config, dashboard, decide, feishu, learn, llm, login, profit, quicklinks
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
    key_err = llm.check_api_key(config.DEEPSEEK_API_KEY)
    if key_err:
        add_log(f"⚠️ {key_err}。FAQ 仍会回复，其余消息会全部转人工")
    add_log("启动机器人…")
    _task = asyncio.create_task(bot.run(on_log=add_log))


def set_dry_run(value: bool) -> None:
    """切换干跑并写入 .env：否则别的设置页保存时会从 .env 重新加载，把它改回旧值。"""
    if bool(value) == config.DRY_RUN:
        return
    config.update_env({"DRY_RUN": "true" if value else "false"})
    add_log(f"DRY_RUN 已切换为 {'开启（只打印不真发）' if config.DRY_RUN else '关闭（真实发送）'}，立即生效")


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
        tab_profit = ui.tab("利润测算", icon="calculate")
        tab_bargain = ui.tab("自动议价", icon="sell")
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
                        on_change=lambda e: set_dry_run(e.value),
                    )
                with ui.row().classes("gap-2"):
                    login_btn = ui.button("扫码登录", on_click=do_login, icon="qr_code")
                    start_btn = ui.button("启动", on_click=start_bot, color="positive", icon="play_arrow")
                    stop_btn = ui.button("停止", on_click=stop_bot, color="negative", icon="stop")
                    takeover_btn = ui.button(
                        "AI 立即接管", on_click=lambda: take_over(), color="warning", icon="smart_toy"
                    ).tooltip("结束所有转人工会话的等待，下一轮起 AI 直接回复")

                def take_over():
                    n = bot.resume_all()
                    add_log(f"🤖 AI 已立即接管 {n} 个转人工会话" if n else "当前没有转人工等待中的会话")
                    ui.notify(f"已接管 {n} 个会话" if n else "没有等待中的会话", type="positive" if n else "info")

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
                paused = bot.human_paused_count()
                takeover_btn.set_text(f"AI 立即接管（{paused}）" if paused else "AI 立即接管")
                takeover_btn.set_enabled(paused > 0)
                # 开关始终跟随实际配置，避免界面显示与真实状态不一致
                if dry_switch.value != config.DRY_RUN:
                    dry_switch.value = config.DRY_RUN
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

        # ===== 利润测算 =====
        with ui.tab_panel(tab_profit):
            _profit_panel()

        # ===== 自动议价 =====
        with ui.tab_panel(tab_bargain):
            _bargain_panel()

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
                    err = llm.check_api_key(key_input.value)
                    if err:
                        ui.notify(err, type="negative")
                        return
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

def _fmt_pct(x: float | None) -> str:
    return "-" if x is None else f"{x * 100:.1f}%"


def _profit_panel() -> None:
    """利润测算页：填成本结构，实时算毛利率 / 保本 ROI / 目标 ROI / 推广后净利。"""
    ui.label("填入单件成本结构，自动算保本 ROI。推广 ROI 低于保本线 = 每卖一单都在亏。").classes(
        "text-sm text-gray-500"
    )
    ui.label("口径：ROI = 成交金额 ÷ 推广花费；退货订单的推广费照花，退货损耗已摊进每单成本。").classes(
        "text-xs text-gray-400"
    )

    fields = [  # (属性, 标题, 后缀)
        ("price", "售价（到手价）", "元"),
        ("cost", "进货成本", "元"),
        ("shipping", "发货运费", "元"),
        ("packaging", "包材", "元"),
        ("other", "其他（赠品/运费险等）", "元"),
        ("fee_rate", "平台扣点", "%"),
        ("return_rate", "退货率", "%"),
        ("return_loss", "每笔退货额外损耗（退回运费+货损）", "元"),
        ("target_margin", "目标净利率", "%"),
        ("actual_roi", "当前实际推广 ROI（可不填）", ""),
    ]
    inputs: dict = {}
    defaults = profit.ProfitInput()

    with ui.row().classes("w-full gap-4 items-start"):
        with ui.card().classes("gap-1").style("min-width:360px;max-width:420px"):
            name_input = ui.input("商品名（保存用）").classes("w-full")
            for attr, title, suffix in fields:
                inputs[attr] = (
                    ui.number(title, value=getattr(defaults, attr), min=0, format="%.2f", on_change=lambda: recalc())
                    .props(f'suffix="{suffix}"' if suffix else "")
                    .classes("w-full")
                )

        with ui.column().classes("flex-1 gap-3"):
            out: dict = {}
            with ui.row().classes("gap-3 flex-wrap"):
                for key, title in [
                    ("unit_profit", "每单利润（未计推广）"),
                    ("margin", "毛利率"),
                    ("breakeven_roi", "保本 ROI"),
                    ("target_roi", "目标 ROI"),
                    ("net_profit", "推广后每单净利"),
                    ("net_margin", "推广后净利率"),
                ]:
                    with ui.card().classes("items-center").style("min-width:150px"):
                        ui.label(title).classes("text-xs text-gray-500")
                        out[key] = ui.label("-").classes("text-2xl font-bold")
            verdict = ui.label("").classes("text-base")

    def read_input() -> profit.ProfitInput:
        vals = {a: float(inputs[a].value or 0) for a, _, _ in fields}
        return profit.ProfitInput(name=(name_input.value or "").strip(), **vals)

    def recalc():
        try:
            r = profit.calculate(read_input())
        except ValueError as e:
            for lbl in out.values():
                lbl.set_text("-")
            verdict.set_text(str(e))
            verdict.classes(replace="text-base text-gray-400")
            return
        out["unit_profit"].set_text(f"¥{r.unit_profit:.2f}")
        out["margin"].set_text(_fmt_pct(r.margin))
        out["breakeven_roi"].set_text("无法保本" if r.breakeven_roi is None else f"{r.breakeven_roi:.2f}")
        out["target_roi"].set_text("达不到" if r.target_roi is None else f"{r.target_roi:.2f}")
        out["net_profit"].set_text("-" if r.net_profit is None else f"¥{r.net_profit:.2f}")
        out["net_margin"].set_text(_fmt_pct(r.net_margin))

        roi = float(inputs["actual_roi"].value or 0)
        if r.breakeven_roi is None:
            msg, color = "❌ 毛利 ≤ 0，不投推广也在亏，先调价或降成本", "text-red-600"
        elif roi <= 0:
            msg, color = f"推广 ROI 需 ≥ {r.breakeven_roi:.2f} 才不亏", "text-gray-700"
        elif roi < r.breakeven_roi:
            msg, color = f"❌ 实际 ROI {roi:.2f} 低于保本线 {r.breakeven_roi:.2f}，每单亏 ¥{-r.net_profit:.2f}", "text-red-600"
        elif r.target_roi is None or roi < r.target_roi:
            msg, color = f"⚠️ 已保本，但净利率 {_fmt_pct(r.net_margin)} 未达目标", "text-orange-600"
        else:
            msg, color = f"✅ 达标：每单净赚 ¥{r.net_profit:.2f}，可以加大推广", "text-green-600"
        verdict.set_text(msg)
        verdict.classes(replace=f"text-base font-bold {color}")

    def save_current():
        item = read_input()
        if not item.name:
            ui.notify("先填商品名", type="warning")
            return
        try:
            profit.calculate(item)
        except ValueError as e:
            ui.notify(str(e), type="warning")
            return
        profit.upsert_item(item)
        render_saved()
        ui.notify("已保存", type="positive")

    def load_item(item: profit.ProfitInput):
        name_input.value = item.name
        for a, _, _ in fields:
            inputs[a].value = getattr(item, a)
        recalc()

    def del_item(name: str):
        profit.save_items([i for i in profit.load_items() if i.name != name])
        render_saved()

    ui.button("保存到商品清单", icon="save", on_click=save_current, color="positive").classes("mt-2")
    ui.separator()
    ui.label("已保存商品（点「载入」回填修改）").classes("font-bold mt-2")
    saved = ui.column().classes("w-full gap-1")

    def render_saved():
        saved.clear()
        items = profit.load_items()
        with saved:
            if not items:
                ui.label("还没有保存的商品").classes("text-gray-400 q-pa-sm")
                return
            with ui.row().classes("w-full font-bold text-gray-600 gap-2"):
                for t, w in [("商品", "w-48"), ("售价", "w-20"), ("毛利率", "w-20"), ("保本ROI", "w-20"),
                             ("目标ROI", "w-20"), ("实际ROI", "w-20"), ("操作", "w-32")]:
                    ui.label(t).classes(w)
            for it in items:
                try:
                    r = profit.calculate(it)
                except ValueError:
                    continue
                bad = it.actual_roi > 0 and (r.breakeven_roi is None or it.actual_roi < r.breakeven_roi)
                with ui.row().classes("w-full items-center gap-2 border-b"):
                    ui.label(it.name).classes("w-48 truncate")
                    ui.label(f"¥{it.price:.2f}").classes("w-20")
                    ui.label(_fmt_pct(r.margin)).classes("w-20")
                    ui.label("无法保本" if r.breakeven_roi is None else f"{r.breakeven_roi:.2f}").classes("w-20")
                    ui.label("达不到" if r.target_roi is None else f"{r.target_roi:.2f}").classes("w-20")
                    ui.label(f"{it.actual_roi:.2f}" if it.actual_roi else "-").classes(
                        "w-20 " + ("text-red-600 font-bold" if bad else "")
                    )
                    with ui.row().classes("w-32 gap-0"):
                        ui.button("载入", on_click=lambda i=it: load_item(i)).props("flat dense")
                        ui.button(icon="delete", on_click=lambda n=it.name: del_item(n)).props(
                            "flat dense color=negative"
                        )

    render_saved()
    recalc()


def _bargain_panel() -> None:
    """自动议价页：让价规则设置 + 各商品可让区间 + 今日承诺记录（供人工兑现、对账）。"""
    ui.label("买家砍价时，机器人在区间内阶梯让价：先守价 → 小让 → 再让一点 → 守住底线。").classes(
        "text-sm text-gray-500"
    )
    ui.label(
        "答应的优惠由人工改价/发券兑现（会推送飞书）。同一买家只升不降、不超过买家开口金额；干跑模式不记承诺。"
    ).classes("text-xs text-gray-400")

    with ui.card().classes("w-full max-w-xl gap-2"):
        b_on = ui.switch("启用自动议价", value=config.BARGAIN_ENABLED)
        with ui.row().classes("gap-3 w-full"):
            b_min = ui.number("默认最小让价(元)", value=config.BARGAIN_MIN, min=0, format="%.1f").classes("flex-1")
            b_max = ui.number("默认最大让价(元)", value=config.BARGAIN_MAX, min=0, format="%.1f").classes("flex-1")
        with ui.row().classes("gap-3 w-full"):
            b_step = ui.number("取整单位(元)", value=config.BARGAIN_STEP, min=0.1, format="%.1f").classes("flex-1")
            b_budget = ui.number(
                "每日让价预算(元)", value=config.BARGAIN_DAILY_BUDGET, min=0, format="%.0f"
            ).classes("flex-1")
        b_unknown = ui.select(
            {"default": "用默认区间", "human": "不让价，转人工"},
            value=config.BARGAIN_UNKNOWN_PRODUCT,
            label="未录入「利润测算」的商品",
        ).classes("w-full")
        b_ad = ui.number(
            "推广费占售价(%)：算商品可让价时预留，商品填了实际ROI则按ROI算",
            value=config.BARGAIN_AD_SHARE, min=0, max=100, format="%.0f",
        ).classes("w-full")
        b_text = ui.input("答应让价后的兑现说明（接在让价话术后）", value=config.BARGAIN_FULFILL_TEXT).classes(
            "w-full"
        )

        def save_bargain():
            lo, hi = float(b_min.value or 0), float(b_max.value or 0)
            if lo > hi:
                ui.notify("最小让价不能大于最大让价", type="negative")
                return
            config.update_env({
                "BARGAIN_ENABLED": "true" if b_on.value else "false",
                "BARGAIN_MIN": lo,
                "BARGAIN_MAX": hi,
                "BARGAIN_STEP": float(b_step.value or 0.5),
                "BARGAIN_DAILY_BUDGET": float(b_budget.value or 0),
                "BARGAIN_UNKNOWN_PRODUCT": b_unknown.value,
                "BARGAIN_AD_SHARE": float(b_ad.value or 0),
                "BARGAIN_FULFILL_TEXT": (b_text.value or "").strip().replace("\n", " "),
            })
            render_ranges()
            ui.notify("议价设置已保存并生效 ✅", type="positive")

        ui.button("保存", on_click=save_bargain, icon="save", color="positive")

    ui.label("各商品可让区间（来自「利润测算」：每单利润 − 目标净利 − 推广费）").classes("font-bold mt-4")
    ranges = ui.column().classes("w-full gap-1")

    def render_ranges():
        ranges.clear()
        items = profit.load_items()
        with ranges:
            if not items:
                ui.label("还没有录入商品，全部按默认区间让价。到「利润测算」页保存商品即可按利润自动算。").classes(
                    "text-gray-400 q-pa-sm"
                )
                return
            with ui.row().classes("w-full font-bold text-gray-600 gap-2"):
                for t, w in [("商品", "w-48"), ("售价", "w-20"), ("可让区间", "w-32")]:
                    ui.label(t).classes(w)
            for it in items:
                try:
                    hi = bargain.max_allowance(it)
                except ValueError:
                    continue
                lo = min(config.BARGAIN_MIN, hi)
                with ui.row().classes("w-full gap-2 border-b"):
                    ui.label(it.name).classes("w-48 truncate")
                    ui.label(f"¥{it.price:.2f}").classes("w-20")
                    ui.label(f"{lo:g} ~ {hi:g} 元" if hi > 0 else "不让价").classes(
                        "w-32" + ("" if hi > 0 else " text-gray-400")
                    )

    with ui.row().classes("items-center gap-2 mt-4"):
        ui.label("今日承诺记录（需人工改价/发券兑现）").classes("font-bold")
        ui.button(icon="refresh", on_click=lambda: render_promises()).props("flat dense").tooltip("刷新")
    promises = ui.column().classes("w-full gap-1")

    def render_promises():
        promises.clear()
        st = bargain.load_state()
        rows = st.get("promises", [])
        with promises:
            ui.label(
                f"今日已让 ¥{st.get('spent', 0):g} / 预算 ¥{config.BARGAIN_DAILY_BUDGET:g}，共 {len(rows)} 笔"
            ).classes("text-sm text-gray-600")
            if not rows:
                return
            with ui.row().classes("w-full font-bold text-gray-600 gap-2"):
                for t, w in [("时间", "w-20"), ("买家", "w-32"), ("商品", "w-48"), ("累计让价", "w-20"),
                             ("买家原话", "flex-1")]:
                    ui.label(t).classes(w)
            for r in reversed(rows):
                with ui.row().classes("w-full gap-2 border-b"):
                    ui.label(r.get("time", "")).classes("w-20")
                    ui.label(r.get("buyer") or f"会话 {r.get('conv', '')}").classes("w-32 truncate").tooltip(
                        f"会话 ID：{r.get('conv', '')}"
                    )
                    ui.label(r.get("product") or "-").classes("w-48 truncate")
                    ui.label(f"¥{r.get('offer', 0):g}").classes("w-20 font-bold text-orange-600")
                    ui.label(r.get("msg", "")).classes("flex-1 truncate")

    render_ranges()
    render_promises()


def run() -> None:
    show = os.getenv("PDDBOT_SHOW", "1") != "0"
    ui.run(title="pddbot 控制台", port=8777, show=show, reload=False, favicon="🤖")


if __name__ in {"__main__", "__mp_main__"}:
    run()
