"""利润测算：按单件成本结构算毛利率、保本 ROI、目标 ROI、推广后净利。

口径（与拼多多推广后台一致）：ROI = 成交金额 ÷ 推广花费，成交金额按售价计，
退货订单的推广费照样花掉，所以退货损耗要摊进每单成本里。

每单期望利润（未计推广）=
    (1 − 退货率) × (售价 − 扣点 − 进货成本 − 运费 − 包材 − 其他)
  − 退货率 × (运费 + 包材 + 退货额外损耗)
退货时货品退回（成本收回）、扣点退回，但发出的运费/包材和退回运费、货损要自己承担。

毛利率 m = 每单期望利润 ÷ 售价
保本 ROI = 1 ÷ m
目标 ROI（要保留净利率 t）= 1 ÷ (m − t)
推广后每单净利（实际 ROI = R）= 每单期望利润 − 售价 ÷ R
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import yaml

from . import config

PROFIT_FILE = config.ROOT / "data" / "profit.yaml"


@dataclass
class ProfitInput:
    name: str = ""
    price: float = 0.0          # 售价（到手价，元）
    cost: float = 0.0           # 进货成本（元）
    shipping: float = 0.0       # 发货运费（元）
    packaging: float = 0.0      # 包材（元）
    other: float = 0.0          # 其他成本：赠品、运费险等（元）
    fee_rate: float = 0.6       # 平台扣点（%），拼多多技术服务费默认按 0.6% 估
    return_rate: float = 0.0    # 退货率（%）
    return_loss: float = 0.0    # 每笔退货额外损耗：退回运费 + 货损（元）
    target_margin: float = 10.0  # 目标净利率（%）
    actual_roi: float = 0.0     # 当前实际推广 ROI，0 表示不填


@dataclass
class ProfitResult:
    fee: float                  # 平台扣点金额
    unit_profit: float          # 每单期望利润（未计推广）
    margin: float               # 毛利率（0~1）
    breakeven_roi: float | None  # 保本 ROI；毛利 ≤ 0 时为 None（怎么投都亏）
    target_roi: float | None    # 达到目标净利率所需 ROI；达不到时为 None
    net_profit: float | None    # 按实际 ROI 推广后每单净利；未填实际 ROI 时为 None
    net_margin: float | None    # 推广后净利率（0~1）


def calculate(p: ProfitInput) -> ProfitResult:
    if p.price <= 0:
        raise ValueError("售价必须大于 0")
    r = min(max(p.return_rate, 0.0), 100.0) / 100
    fee = p.price * p.fee_rate / 100
    kept = p.price - fee - p.cost - p.shipping - p.packaging - p.other
    returned = -(p.shipping + p.packaging + p.return_loss)
    unit_profit = (1 - r) * kept + r * returned
    margin = unit_profit / p.price

    breakeven = 1 / margin if margin > 0 else None
    t = p.target_margin / 100
    target = 1 / (margin - t) if margin - t > 0 else None

    net_profit = net_margin = None
    if p.actual_roi > 0:
        net_profit = unit_profit - p.price / p.actual_roi
        net_margin = net_profit / p.price

    return ProfitResult(
        fee=round(fee, 2),
        unit_profit=round(unit_profit, 2),
        margin=margin,
        breakeven_roi=round(breakeven, 2) if breakeven else None,
        target_roi=round(target, 2) if target else None,
        net_profit=round(net_profit, 2) if net_profit is not None else None,
        net_margin=net_margin,
    )


def load_items() -> list[ProfitInput]:
    try:
        with open(PROFIT_FILE, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or []
    except Exception:
        return []
    fields = ProfitInput.__dataclass_fields__
    return [ProfitInput(**{k: v for k, v in d.items() if k in fields}) for d in raw if isinstance(d, dict)]


def save_items(items: list[ProfitInput]) -> None:
    PROFIT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PROFIT_FILE, "w", encoding="utf-8") as f:
        yaml.safe_dump([asdict(i) for i in items], f, allow_unicode=True, sort_keys=False)


def upsert_item(item: ProfitInput) -> None:
    """按商品名保存：同名覆盖，否则追加。"""
    items = [i for i in load_items() if i.name != item.name]
    items.append(item)
    save_items(items)
