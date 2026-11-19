"""链接监控：定时抓全店商品数据，和历史同时段对比，只在异常时推送飞书。

数据来源：数据中心「商品数据」页（/sycm/goods_effect）。
  - 商品明细接口 queryGoodsDetailVOListV2ForMMS：每个商品今日截至当前的访客/浏览/成交/转化；
    数字经反爬字体加密，用 digitfont 解码。
  - 整店概况接口 queryGoodsPageOverviewForMms：整店今日/昨日按小时累计（明文）。
接口带页面现算的反爬参数，不能直接调，只能打开页面截获页面自己发出的请求。

规则（「基线」= 近几天同一时刻的中位数；当天数据是从 0 点起的累计值，只能同时刻比）：
  - 访客骤降：基线 ≥ 最小基数，当前比基线低 X% 以上
  - 访客归零：基线 ≥ 最小基数，当前为 0（或已不在有访客的商品列表里）
  - 转化暴跌：当前访客 ≥ 最小基数，转化率比基线低 Y% 以上
  - 商品状态变化：goodsStatus 与上一次不同
  - 整店访客骤降：整店访客对比昨日同时刻（来自概况接口，第一天就能用）
防误报：连续 N 次都异常才推送；同一商品同一类异常冷却 H 小时内只推一次。
"""
from __future__ import annotations

import asyncio
import json
import random
import sqlite3
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from playwright.async_api import async_playwright

from . import config, digitfont, feishu

GOODS_URL = "https://mms.pinduoduo.com/sycm/goods_effect"
LIST_API = "queryGoodsDetailVOListV2ForMMS"
OVERVIEW_API = "queryGoodsPageOverviewForMms"
DB_FILE = config.ROOT / "data" / "monitor.db"

BASE_DAYS = 7          # 基线回看天数
MIN_BASE_DAYS = 2      # 至少有几天同时刻数据才判单品异常
SAME_TIME_WINDOW = 45  # 「同一时刻」容差（分钟）
PAGE_SIZE = 100        # 把页面的分页请求改成一页拿完


class LoginExpired(Exception):
    pass


@dataclass
class GoodsRow:
    goods_id: int
    name: str
    uv: int
    pv: int
    pay_amt: float
    pay_cnt: int
    vcr: float | None  # 0~1
    status: int | None


@dataclass
class Snapshot:
    ts: datetime
    goods: list[GoodsRow]
    total: int                      # 接口报告的商品总数
    store_uv: int | None = None     # 整店今日累计访客
    store_yes_uv: int | None = None  # 整店昨日同一小时累计访客
    store_yes_total: int | None = None  # 整店昨日全天访客
    yesterday: list[GoodsRow] | None = None  # 昨日全天单品数据（每天只取一次）
    yesterday_day: str | None = None


@dataclass
class Anomaly:
    key: str          # 去重键：goods_id:kind
    goods_id: int
    name: str
    kind: str
    msg: str
    confirm: bool = True  # 是否需要连续多次才推送


@dataclass
class CheckResult:
    ts: datetime
    ok: bool
    msg: str
    goods: list[dict] = field(default_factory=list)   # 每个商品：当前值 + 基线，供界面展示
    alerts: list[Anomaly] = field(default_factory=list)


# ---------------------------------------------------------------- 抓取


def _num(v) -> float | None:
    return digitfont.to_number(v)


def _int(v) -> int:
    x = _num(v)
    return int(round(x)) if x is not None else 0


def _parse_rows(result: dict, mapping: dict[str, str]) -> list[GoodsRow]:
    rows = []
    for g in result.get("goodsDetailList") or []:
        d = {k: digitfont.decode(v, mapping) for k, v in g.items()}
        rows.append(
            GoodsRow(
                goods_id=int(d["goodsId"]),
                name=d.get("goodsName") or "",
                uv=_int(d.get("goodsUv")),
                pv=_int(d.get("goodsPv")),
                pay_amt=_num(d.get("payOrdrAmt")) or 0.0,
                pay_cnt=_int(d.get("payOrdrCnt")),
                vcr=_num(d.get("goodsVcr")),
                status=d.get("goodsStatus"),
            )
        )
    return rows


async def fetch_snapshot(
    state_file: Path | None = None, headless: bool | None = None, need_yesterday: bool = False
) -> Snapshot:
    """打开商品数据页，截获商品明细 + 整店概况，并解码加密数字。

    need_yesterday：再点一下明细区的「昨日」，取昨日全天单品数据（历史不足时用来估算基线）。
    """
    state_file = state_file or config.STATE_FILE
    if not Path(state_file).exists():
        raise LoginExpired("还没有登录态，请先扫码登录")
    headless = config.HEADLESS if headless is None else headless

    lists: list[dict] = []
    yes_lists: list[dict] = []
    overview: dict = {}

    async def rewrite_page_size(route):
        req = route.request
        try:
            body = json.loads(req.post_data or "{}")
        except Exception:
            body = None
        if isinstance(body, dict) and body.get("queryType") in (0, 6) and body.get("pageNum") == 1:
            body["pageSize"] = PAGE_SIZE
            await route.continue_(post_data=json.dumps(body, separators=(",", ":")))
        else:
            await route.continue_()

    async def on_resp(r):
        try:
            if LIST_API in r.url:
                post = json.loads(r.request.post_data or "{}")
                if post.get("queryType") == 6:  # 实时（今日截至当前）
                    lists.append(await r.json())
                elif post.get("queryType") == 0:  # 昨日全天
                    yes_lists.append(await r.json())
            elif OVERVIEW_API in r.url:
                overview.update(await r.json())
        except Exception:
            pass

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless, args=["--start-minimized"])
        try:
            ctx = await browser.new_context(storage_state=str(state_file), viewport={"width": 1440, "height": 900})
            page = await ctx.new_page()
            await page.route(f"**/{LIST_API}*", rewrite_page_size)
            page.on("response", on_resp)
            await page.goto(GOODS_URL, wait_until="domcontentloaded", timeout=45000)
            for _ in range(40):
                if "/login" in page.url:
                    raise LoginExpired("登录已失效，请重新扫码登录")
                if lists and overview:
                    break
                await page.wait_for_timeout(1000)
            if not lists:
                raise RuntimeError("30 秒内没等到商品明细数据（页面没加载完或被风控拦截）")

            if need_yesterday:
                try:
                    btns = page.get_by_text("昨日", exact=True)
                    n = await btns.count()
                    if n:
                        await btns.nth(n - 1).scroll_into_view_if_needed()
                        await btns.nth(n - 1).click()
                        for _ in range(15):
                            if yes_lists:
                                break
                            await page.wait_for_timeout(1000)
                except Exception:
                    pass  # 取不到昨日数据不影响本次检查

            font_url = await page.evaluate(
                """() => { for (const s of document.styleSheets) { try { for (const r of s.cssRules) {
                    if (r.type === 5 && /spider-font/.test(r.style.getPropertyValue('font-family'))) {
                        const m = r.style.getPropertyValue('src').match(/url\\(["']?([^"')]+)/);
                        if (m) return m[1];
                    } } } catch (e) {} } return null; }"""
            )
            if not font_url:
                raise digitfont.DecodeError("页面里没找到数字解码字体 spider-font")
            resp = await ctx.request.get(font_url)
            font_bytes = await resp.body()
        finally:
            await browser.close()

    mapping = digitfont.build_map(font_bytes)
    result = (lists[-1].get("result") or {})
    rows = _parse_rows(result, mapping)
    snap = Snapshot(ts=datetime.now(), goods=rows, total=int(result.get("totalNum") or len(rows)))
    if yes_lists:
        yres = yes_lists[-1].get("result") or {}
        snap.yesterday = _parse_rows(yres, mapping)
        first = (yres.get("goodsDetailList") or [{}])[0]
        snap.yesterday_day = first.get("statDate") or (snap.ts.date() - timedelta(days=1)).isoformat()
    ov = overview.get("result") or {}
    today, yes = ov.get("todayList") or [], ov.get("yesList") or []
    if today:
        last = today[-1]
        # 用「最后一个完整小时」的累计值，和昨日同一小时对齐
        snap.store_uv = _int(last.get("guv"))
        hr = last.get("statHr")
        match = [y for y in yes if y.get("statHr") == hr]
        if match:
            snap.store_yes_uv = _int(match[0].get("guv"))
    if yes:
        snap.store_yes_total = _int(yes[-1].get("guv"))
    return snap


# ---------------------------------------------------------------- 存储


def _db() -> sqlite3.Connection:
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_FILE)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS runs(ts TEXT PRIMARY KEY, day TEXT, minute INT, total INT,
                                        store_uv INT, store_yes_uv INT);
        CREATE TABLE IF NOT EXISTS snap(ts TEXT, day TEXT, minute INT, goods_id INT, name TEXT,
                                        uv INT, pv INT, pay_amt REAL, pay_cnt INT, vcr REAL, status INT);
        CREATE INDEX IF NOT EXISTS snap_gd ON snap(goods_id, day);
        CREATE TABLE IF NOT EXISTS streak(key TEXT PRIMARY KEY, count INT, last_alert TEXT);
        CREATE TABLE IF NOT EXISTS alerts(ts TEXT, goods_id INT, name TEXT, kind TEXT, msg TEXT);
        CREATE TABLE IF NOT EXISTS daily(day TEXT, goods_id INT, name TEXT, uv INT, pv INT, pay_amt REAL,
                                         vcr REAL, PRIMARY KEY(day, goods_id));
        """
    )
    cols = {r[1] for r in con.execute("PRAGMA table_info(runs)")}
    if "store_yes_total" not in cols:
        con.execute("ALTER TABLE runs ADD COLUMN store_yes_total INT")
    return con


def has_daily(con: sqlite3.Connection, day: str) -> bool:
    return con.execute("SELECT 1 FROM daily WHERE day=? LIMIT 1", (day,)).fetchone() is not None


def _minute(ts: datetime) -> int:
    return ts.hour * 60 + ts.minute


def save_snapshot(con: sqlite3.Connection, s: Snapshot) -> None:
    ts, day, m = s.ts.isoformat(timespec="seconds"), s.ts.date().isoformat(), _minute(s.ts)
    con.execute(
        "INSERT OR REPLACE INTO runs(ts, day, minute, total, store_uv, store_yes_uv, store_yes_total) "
        "VALUES(?,?,?,?,?,?,?)",
        (ts, day, m, s.total, s.store_uv, s.store_yes_uv, s.store_yes_total),
    )

    con.executemany(
        "INSERT INTO snap VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        [(ts, day, m, g.goods_id, g.name, g.uv, g.pv, g.pay_amt, g.pay_cnt, g.vcr, g.status) for g in s.goods],
    )
    con.commit()


def save_daily(con: sqlite3.Connection, s: Snapshot) -> None:
    """昨日全天单品数据（evaluate 之前存，第一次检查就能用来估算基线）。"""
    if s.yesterday and s.yesterday_day:
        con.executemany(
            "INSERT OR REPLACE INTO daily VALUES(?,?,?,?,?,?,?)",
            [(s.yesterday_day, g.goods_id, g.name, g.uv, g.pv, g.pay_amt, g.vcr) for g in s.yesterday],
        )
        con.commit()


def _estimate_from_yesterday(con: sqlite3.Connection, s: Snapshot) -> dict[int, dict] | None:
    """历史不足时的基线：昨日全天单品访客 × 整店昨日到这个点的访客占比。

    例：整店昨日到 15 点累计 588 人、全天 1176 人 → 占比 50%；某商品昨日全天 207 人 → 估算昨日同时段约 104 人。
    转化率直接用昨日全天的。
    """
    if not s.store_yes_uv or not s.store_yes_total:
        return None
    day = (s.ts.date() - timedelta(days=1)).isoformat()
    rows = con.execute("SELECT goods_id, uv, vcr FROM daily WHERE day=?", (day,)).fetchall()
    if not rows:
        return None
    share = min(1.0, s.store_yes_uv / s.store_yes_total)
    return {gid: {"uv": round(uv * share), "vcr": vcr if uv else None} for gid, uv, vcr in rows}


def _same_time_runs(con: sqlite3.Connection, now: datetime) -> list[str]:
    """过去 BASE_DAYS 天里，每天最接近当前时刻的那次抓取（ts）。"""
    out = []
    m = _minute(now)
    for k in range(1, BASE_DAYS + 1):
        day = (now.date() - timedelta(days=k)).isoformat()
        row = con.execute(
            "SELECT ts FROM runs WHERE day=? AND ABS(minute-?)<=? ORDER BY ABS(minute-?) LIMIT 1",
            (day, m, SAME_TIME_WINDOW, m),
        ).fetchone()
        if row:
            out.append(row[0])
    return out


def _baselines(con: sqlite3.Connection, now: datetime) -> tuple[int, dict[int, dict]]:
    """返回 (参与基线的天数, {goods_id: {uv, vcr}})。某天抓到了但商品不在列表里 → 当天访客按 0 算。"""
    runs = _same_time_runs(con, now)
    if not runs:
        return 0, {}
    per: dict[int, dict[str, list]] = {}
    for ts in runs:
        rows = con.execute("SELECT goods_id, uv, vcr FROM snap WHERE ts=?", (ts,)).fetchall()
        seen = {}
        for gid, uv, vcr in rows:
            seen[gid] = (uv, vcr)
        for gid in set(seen) | set(per):
            d = per.setdefault(gid, {"uv": [], "vcr": []})
            uv, vcr = seen.get(gid, (0, None))
            d["uv"].append(uv)
            if vcr is not None and uv:
                d["vcr"].append(vcr)
    # 先出现的商品在早些天里没补 0，这里按天数补齐
    n = len(runs)
    base = {}
    for gid, d in per.items():
        uvs = d["uv"] + [0] * (n - len(d["uv"]))
        base[gid] = {
            "uv": statistics.median(uvs),
            "vcr": statistics.median(d["vcr"]) if d["vcr"] else None,
        }
    return n, base


# ---------------------------------------------------------------- 规则


@dataclass
class Rules:
    drop_pct: float = 50      # 访客比基线低多少 % 算骤降
    min_uv: int = 30          # 基线访客低于这个数不判（小链接波动大）
    cvr_drop_pct: float = 60  # 转化率比基线低多少 % 算暴跌
    confirm: int = 2          # 连续几次异常才推送
    cooldown_h: float = 6     # 同一异常多久内不重复推

    @classmethod
    def from_config(cls) -> "Rules":
        return cls(
            drop_pct=config.MONITOR_DROP_PCT,
            min_uv=config.MONITOR_MIN_UV,
            cvr_drop_pct=config.MONITOR_CVR_DROP_PCT,
            confirm=config.MONITOR_CONFIRM,
            cooldown_h=config.MONITOR_COOLDOWN_H,
        )


def _short(name: str, n: int = 24) -> str:
    return name if len(name) <= n else name[:n] + "…"


def evaluate(con: sqlite3.Connection, s: Snapshot, rules: Rules) -> tuple[list[Anomaly], list[dict]]:
    """对比基线找异常。返回 (异常列表, 每个商品的展示行)。需在 save_snapshot 之前调用（避免把自己算进基线）。"""
    n_days, base = _baselines(con, s.ts)
    usable = n_days >= MIN_BASE_DAYS
    src = f"近 {n_days} 天同时段中位数"
    if not usable:
        est = _estimate_from_yesterday(con, s)
        if est:
            base, usable, src = est, True, "昨日同时段（按整店节奏估算）"
    cur = {g.goods_id: g for g in s.goods}
    anomalies: list[Anomaly] = []
    view: list[dict] = []

    # 每个商品最近一次记录到的名称和状态（中间几次没访客、不在列表里也不影响），用于判断状态变化
    prev_status: dict[int, tuple] = {}
    for gid, name, st in con.execute(
        "SELECT s.goods_id, s.name, s.status FROM snap s JOIN "
        "(SELECT goods_id, MAX(ts) AS ts FROM snap GROUP BY goods_id) l ON s.goods_id=l.goods_id AND s.ts=l.ts"
    ):
        prev_status[gid] = (name, st)

    names = {gid: nm for gid, (nm, _) in prev_status.items()}
    names.update({g.goods_id: g.name for g in s.goods})

    for gid in set(cur) | set(base):
        g = cur.get(gid)
        b = base.get(gid, {})
        uv = g.uv if g else 0
        b_uv = b.get("uv")
        name = names.get(gid) or str(gid)
        view.append({
            "goods_id": gid, "name": name, "uv": uv, "base_uv": b_uv,
            "pay_amt": g.pay_amt if g else 0.0, "vcr": g.vcr if g else None,
            "base_vcr": b.get("vcr"), "status": g.status if g else None, "base_src": src if b else None,
        })
        label = f"{_short(name)}（ID {gid}）"

        if usable and b_uv is not None and b_uv >= rules.min_uv:
            if uv == 0:
                anomalies.append(Anomaly(f"{gid}:zero", gid, name, "访客归零",
                                         f"{label}\n   访客归零：截至现在 0，{src} {b_uv:g}"))
            elif uv < b_uv * (1 - rules.drop_pct / 100):
                pct = (uv - b_uv) / b_uv * 100
                anomalies.append(Anomaly(f"{gid}:drop", gid, name, "访客骤降",
                                         f"{label}\n   访客骤降：截至现在 {uv}，{src} {b_uv:g}（{pct:.0f}%）"))

        b_vcr = b.get("vcr")
        # 按平时转化率，这么多访客本该成交 ≥ 3 单才判，否则 0 单也可能只是运气
        if (g and usable and uv >= rules.min_uv and b_vcr and uv * b_vcr >= 3
                and g.vcr is not None and g.vcr < b_vcr * (1 - rules.cvr_drop_pct / 100)):
            anomalies.append(Anomaly(f"{gid}:cvr", gid, name, "转化暴跌",
                                     f"{label}\n   转化暴跌：访客 {uv} 正常，转化率 {g.vcr:.2%}，平时 {b_vcr:.2%}"))

        if g and gid in prev_status:
            old = prev_status[gid][1]
            if old is not None and g.status is not None and old != g.status:
                anomalies.append(Anomaly(f"{gid}:status:{g.status}", gid, name, "状态变化",
                                         f"{label}\n   商品状态变化：{old} → {g.status}", confirm=False))

    # 整店：对比昨日同一小时（概况接口自带，第一天就能用）
    if s.store_uv is not None and s.store_yes_uv and s.store_yes_uv >= rules.min_uv:
        if s.store_uv < s.store_yes_uv * (1 - rules.drop_pct / 100):
            pct = (s.store_uv - s.store_yes_uv) / s.store_yes_uv * 100
            anomalies.append(Anomaly("store:drop", 0, "整店", "整店访客骤降",
                                     f"整店访客骤降：截至现在 {s.store_uv}，昨日同时段 {s.store_yes_uv}（{pct:.0f}%）"))

    view.sort(key=lambda r: -(r["base_uv"] or r["uv"] or 0))
    return anomalies, view


def apply_streaks(con: sqlite3.Connection, anomalies: list[Anomaly], rules: Rules, now: datetime) -> list[Anomaly]:
    """连续 N 次异常 + 冷却期过滤，返回本次要推送的异常。"""
    keys = {a.key for a in anomalies}
    # 本次没再异常的，清零连续计数（保留冷却时间）
    for (key,) in con.execute("SELECT key FROM streak WHERE count>0").fetchall():
        if key not in keys:
            con.execute("UPDATE streak SET count=0 WHERE key=?", (key,))
    to_send = []
    for a in anomalies:
        row = con.execute("SELECT count, last_alert FROM streak WHERE key=?", (a.key,)).fetchone()
        count = (row[0] if row else 0) + 1
        last = datetime.fromisoformat(row[1]) if row and row[1] else None
        need = rules.confirm if a.confirm else 1
        cooled = last is None or now - last >= timedelta(hours=rules.cooldown_h)
        if count >= need and cooled:
            to_send.append(a)
            last = now
            con.execute("INSERT INTO alerts VALUES(?,?,?,?,?)",
                        (now.isoformat(timespec="seconds"), a.goods_id, a.name, a.kind, a.msg))
        con.execute("INSERT OR REPLACE INTO streak VALUES(?,?,?)",
                    (a.key, count, last.isoformat(timespec="seconds") if last else None))
    con.commit()
    return to_send


def recent_alerts(limit: int = 50) -> list[dict]:
    if not DB_FILE.exists():
        return []
    con = _db()
    try:
        rows = con.execute("SELECT ts, goods_id, name, kind, msg FROM alerts ORDER BY ts DESC LIMIT ?", (limit,))
        return [dict(zip(("ts", "goods_id", "name", "kind", "msg"), r)) for r in rows]
    finally:
        con.close()


# ---------------------------------------------------------------- 推送 / 主循环


def format_alerts(alerts: list[Anomaly], now: datetime) -> str:
    lines = [f"📉 链接异常提醒（{now:%m-%d %H:%M}）"]
    for i, a in enumerate(alerts, 1):
        lines.append(f"{i}. {a.msg}")
    lines.append(f"查看：{GOODS_URL}")
    return "\n".join(lines)


async def check_once(on_log=None, state_file: Path | None = None, notify: bool = True) -> CheckResult:
    log = on_log or print
    now = datetime.now()
    yesterday = (now.date() - timedelta(days=1)).isoformat()
    con = _db()
    try:
        need_yesterday = not has_daily(con, yesterday)
    finally:
        con.close()
    try:
        snap = await fetch_snapshot(state_file=state_file, need_yesterday=need_yesterday)
    except Exception as e:
        log(f"[链接监控] 抓取失败：{e}")
        return CheckResult(now, False, str(e))

    rules = Rules.from_config()
    con = _db()
    try:
        save_daily(con, snap)
        anomalies, view = evaluate(con, snap, rules)
        save_snapshot(con, snap)
        to_send = apply_streaks(con, anomalies, rules, snap.ts)
    finally:
        con.close()

    msg = f"抓到 {len(snap.goods)}/{snap.total} 个商品，异常 {len(anomalies)} 项，推送 {len(to_send)} 项"
    if len(snap.goods) < snap.total:
        msg += "（⚠️ 没拿全，后面的商品本次未检查）"
    log(f"[链接监控] {msg}")
    if to_send and notify:
        text = format_alerts(to_send, snap.ts)
        if config.FEISHU_ENABLED and config.FEISHU_WEBHOOK:
            ok, detail = await feishu.send_text(text)
            log("[链接监控] 已推送飞书" if ok else f"[链接监控] 飞书推送失败：{detail}")
        else:
            log("[链接监控] 飞书未启用，异常只记录在界面：\n" + text)
    return CheckResult(snap.ts, True, msg, view, to_send)


class Monitor:
    """后台定时检查。只在活跃时段内跑（夜里数据量小、没意义）。"""

    def __init__(self) -> None:
        self._running = False
        self.last: CheckResult | None = None
        self.next_at: datetime | None = None
        self._fail_notified = False

    @property
    def running(self) -> bool:
        return self._running

    def stop(self) -> None:
        self._running = False

    def _in_active_hours(self, now: datetime) -> bool:
        return config.MONITOR_START_HOUR <= now.hour < config.MONITOR_END_HOUR

    async def run(self, on_log=None) -> None:
        log = on_log or print
        self._running = True
        log(f"[链接监控] 已启动：每 {config.MONITOR_INTERVAL:g} 分钟检查一次，"
            f"{config.MONITOR_START_HOUR}:00~{config.MONITOR_END_HOUR}:00 之间运行")
        try:
            while self._running:
                now = datetime.now()
                if self._in_active_hours(now):
                    self.last = await check_once(on_log=log)
                    await self._notify_failure(self.last, log)
                # 随机 ±10% 间隔，避免固定频率
                wait = config.MONITOR_INTERVAL * 60 * random.uniform(0.9, 1.1)
                self.next_at = datetime.now() + timedelta(seconds=wait)
                while self._running and datetime.now() < self.next_at:
                    await asyncio.sleep(5)
        finally:
            self._running = False
            self.next_at = None
            log("[链接监控] 已停止")

    async def _notify_failure(self, r: CheckResult, log) -> None:
        """抓取失败（多为登录失效）只提醒一次，恢复后重置。"""
        if r.ok:
            self._fail_notified = False
            return
        if self._fail_notified or not (config.FEISHU_ENABLED and config.FEISHU_WEBHOOK):
            return
        self._fail_notified = True
        await feishu.send_text(f"⚠️ 链接监控抓取失败：{r.msg}\n请打开控制台检查（登录失效需重新扫码）")
