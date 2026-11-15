"""自动议价：买家砍价时，在让价区间内按阶梯让价，答应后通知人工兑现（改价/发券）。

为什么是阶梯而不是纯随机：纯随机会出现同一买家第二次给得比第一次少、
买家之间一比较就投诉；阶梯让价 = 先守价 → 小让 → 再让一点 → 守住底线，
每档内随机取整，同一会话只升不降、不超过买家开口的金额。

区间来源：
- 商品在「利润测算」里录过：最大可让 = 每单利润 − 目标净利 − 推广费，保证不让到亏本；
- 没录过：用全局默认区间（或配置为不让价、转人工）。

兑现：机器人只负责谈，答应后推送飞书让人工改价/发券，并记入当日议价记录方便对账。
"""
from __future__ import annotations

import json
import math
import random
import re
import time
from dataclasses import dataclass
from datetime import date

from . import config, profit

STATE_FILE = config.ROOT / "data" / "bargain_state.json"
KEEP_DAYS = 7  # 会话议价记忆保留天数

BARGAIN_KEYWORDS = [
    "便宜", "优惠", "少点", "少一点", "少些", "少几块", "少个", "能少", "再少",
    "打折", "打个折", "折扣", "抹零", "抹个零", "让点", "让一点", "让个",
    "降价", "砍价", "减点", "减一点", "最低价", "最低多少", "实惠点",
]
# 含这些词不算砍价（拼多多「砍一刀」是平台玩法，不是向商家议价）
NOT_BARGAIN = ["砍一刀", "帮砍"]

_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

HOLD_TEMPLATES = [  # 第一次砍价：先守价，强调价值
    "亲，这已经是活动价啦，品质有保障，性价比很高的哦～",
    "亲，价格已经很实在啦，用料做工都有保证，放心拍哦～",
    "亲，现在就是优惠价呢，好评很多，买了不会后悔的～",
]
OFFER_TEMPLATES = [
    "亲，帮您跟老板申请了一下，给您便宜{x}元～",
    "好的亲，特意给您申请到优惠{x}元哦～",
    "亲，看您诚心要，老板同意给您便宜{x}元啦～",
]
FLOOR_TEMPLATES = [  # 已经让到底，守住
    "亲，真的已经是最低了，给您便宜{x}元已经是老板的底线啦～",
    "亲，{x}元的优惠已经是最大力度了，再低就亏本啦～",
]
REFUSE_TEMPLATES = [  # 这个商品没有让价空间
    "亲，这款利润真的很薄，已经是最低价啦，没法再少了哦～",
    "亲，这款已经是底价了呢，品质放心，实在没法再便宜啦～",
]


@dataclass
class BargainResult:
    action: str           # "auto" 机器人直接回复；"human" 转人工
    reply: str
    offer: float = 0.0    # 当前对该会话承诺的累计让价（元）
    new_promise: bool = False  # 本次是否新增/提高了承诺，需要通知人工兑现
    ask: float | None = None   # 买家开口要的金额
    reason: str = ""      # 日志用的说明


def is_bargain(text: str) -> bool:
    if any(k in text for k in NOT_BARGAIN):
        return False
    return any(k in text for k in BARGAIN_KEYWORDS)


def parse_ask(text: str, price: float | None = None) -> float | None:
    """解析买家要的让价金额：「便宜2块」→2，「少五元」→5，「打9折」→售价×10%。"""
    m = re.search(r"(\d+(?:\.\d+)?|[一二两三四五六七八九十])\s*(?:块|元|圆|快)", text)
    if m:
        v = m.group(1)
        return float(_CN_NUM.get(v, v))
    m = re.search(r"(\d(?:\.\d)?|[一二三四五六七八九])\s*折", text)
    if m and price:
        v = float(_CN_NUM.get(m.group(1), m.group(1)))
        if 0 < v < 10:
            return round(price * (1 - v / 10), 2)
    return None


def _floor_step(x: float) -> float:
    step = config.BARGAIN_STEP if config.BARGAIN_STEP > 0 else 0.5
    return round(math.floor(x / step + 1e-9) * step, 2)


def _match_item(product: str) -> profit.ProfitInput | None:
    if not product:
        return None
    for it in profit.load_items():
        n = it.name
        if n and (n in product or product in n or product[:8] in n):
            return it
    return None


def max_allowance(item: profit.ProfitInput) -> float:
    """按利润测算算商品最大可让价：每单利润 − 目标净利 − 推广费，取整，不为负。"""
    r = profit.calculate(item)
    if item.actual_roi > 0:
        ad = item.price / item.actual_roi
    else:
        ad = item.price * config.BARGAIN_AD_SHARE / 100
    room = r.unit_profit - item.price * item.target_margin / 100 - ad
    return max(0.0, _floor_step(room))


def product_range(product: str) -> tuple[float, float, str, float | None] | None:
    """返回 (最小让价, 最大让价, 来源说明, 售价)；None 表示按配置不让价、转人工。"""
    item = _match_item(product)
    if item:
        try:
            hi = max_allowance(item)
        except ValueError:
            hi = 0.0
        lo = min(config.BARGAIN_MIN, hi)
        return lo, hi, f"利润测算「{item.name}」", item.price
    if config.BARGAIN_UNKNOWN_PRODUCT == "human":
        return None
    hi = _floor_step(config.BARGAIN_MAX)
    return min(config.BARGAIN_MIN, hi), hi, "默认区间", None


# ---------- 状态：每个会话承诺过多少、今天让出去多少 ----------
def load_state() -> dict:
    try:
        st = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        st = {}
    today = date.today().isoformat()
    if st.get("date") != today:
        st["date"] = today
        st["spent"] = 0.0
        st["promises"] = []
    convs = st.setdefault("convs", {})
    cutoff = time.time() - KEEP_DAYS * 86400
    for k in [k for k, v in convs.items() if v.get("ts", 0) < cutoff]:
        del convs[k]
    return st


def save_state(st: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def today_promises() -> list[dict]:
    return load_state().get("promises", [])


def _rand_between(lo: float, hi: float, rng: random.Random) -> float:
    return _floor_step(rng.uniform(lo, hi)) if hi > lo else _floor_step(hi)


def handle(
    conv_id: str, text: str, product: str = "", rng: random.Random | None = None, save: bool = True,
    buyer: str = "",
) -> BargainResult:
    """处理一条砍价消息（调用方先用 is_bargain 判断）。

    save=False 用于干跑：照常计算回复，但不记承诺、不占预算。
    """
    rng = rng or random.Random()
    if not conv_id:
        return BargainResult("human", "", reason="拿不到会话 ID，无法记住承诺，转人工")

    rg = product_range(product)
    if rg is None:
        return BargainResult("human", "", reason="商品未录入利润测算，按设置不自动让价")
    lo, hi, source, price = rg
    ask = parse_ask(text, price)

    st = load_state()
    conv = st["convs"].setdefault(conv_id, {"rounds": 0, "offer": 0.0, "product": product})
    conv["rounds"] += 1
    conv["ts"] = time.time()
    if product:
        conv["product"] = product
    prev = float(conv.get("offer", 0.0))
    rounds = conv["rounds"]

    def done(res: BargainResult) -> BargainResult:
        if save:
            save_state(st)
        res.ask = ask
        res.reason = f"{source}，可让 {lo:g}~{hi:g} 元，第 {rounds} 次砍价" + (
            f"，{res.reason}" if res.reason else ""
        )
        return res

    step = config.BARGAIN_STEP if config.BARGAIN_STEP > 0 else 0.5
    if hi < step and prev <= 0:
        return done(BargainResult("auto", rng.choice(REFUSE_TEMPLATES), reason="无让价空间"))

    if prev <= 0 and rounds == 1:  # 第一次：先守价
        return done(BargainResult("auto", rng.choice(HOLD_TEMPLATES), reason="先守价"))

    # 计算本档目标让价
    mid = _floor_step((lo + hi) / 2) if hi > lo else hi
    if prev <= 0:
        target = _rand_between(max(lo, step), max(mid, step), rng)
    elif rounds <= 3:
        target = _rand_between(max(prev + step, mid), hi, rng)
    else:
        target = prev  # 第 4 次及以后守住底线
    target = min(target, hi)
    if ask is not None:
        target = min(target, _floor_step(ask))  # 不超过买家开口
    target = max(target, prev)  # 同一会话只升不降

    if target <= prev:
        if prev > 0:
            return done(BargainResult("auto", rng.choice(FLOOR_TEMPLATES).format(x=f"{prev:g}"), offer=prev,
                                      reason="已到底线"))
        return done(BargainResult("auto", rng.choice(REFUSE_TEMPLATES), reason="买家要价低于让价单位"))

    inc = target - prev
    if st["spent"] + inc > config.BARGAIN_DAILY_BUDGET + 1e-9:
        if prev > 0:
            return done(BargainResult("auto", rng.choice(FLOOR_TEMPLATES).format(x=f"{prev:g}"), offer=prev,
                                      reason="今日议价预算已用完，守住已承诺金额"))
        return done(BargainResult("human", "", reason="今日议价预算已用完，转人工"))

    st["spent"] = round(st["spent"] + inc, 2)
    conv["offer"] = target
    st["promises"].append({
        "time": time.strftime("%H:%M:%S"), "conv": conv_id, "buyer": buyer, "product": product,
        "offer": target, "ask": ask, "msg": text[:60],
    })
    reply = rng.choice(OFFER_TEMPLATES).format(x=f"{target:g}") + config.BARGAIN_FULFILL_TEXT
    return done(BargainResult("auto", reply, offer=target, new_promise=True))
