"""交易费用与下单数量规则。费率按日期生效。"""
from __future__ import annotations

from dataclasses import dataclass

STAMP_CUT_DATE = "20230828"      # 印花税由 0.1% 降为 0.05%，仅卖出收取
TRANSFER_CUT_DATE = "20220429"   # 过户费由 0.002% 降为 0.001%，双向收取


@dataclass(frozen=True)
class CostModel:
    commission_rate: float = 0.00025   # 佣金费率，双向；以券商实际费率为准
    min_commission: float = 5.0        # 单笔最低佣金（元）
    slippage: float = 0.001            # 单边滑点，相对开盘价
    statutory_scale: float = 1.0       # 印花税与过户费的倍数；1 为真实费率，0 仅用于测试和对账

    def stamp_rate(self, date: str) -> float:
        return (0.001 if date < STAMP_CUT_DATE else 0.0005) * self.statutory_scale

    def transfer_rate(self, date: str) -> float:
        return (0.00002 if date < TRANSFER_CUT_DATE else 0.00001) * self.statutory_scale

    def commission(self, amount: float) -> float:
        return max(self.min_commission, amount * self.commission_rate) if amount > 0 else 0.0

    def buy_fees(self, date: str, amount: float) -> float:
        return self.commission(amount) + amount * self.transfer_rate(date)

    def sell_fees(self, date: str, amount: float) -> float:
        return self.commission(amount) + amount * (self.stamp_rate(date) + self.transfer_rate(date))


def lot_rule(board: str | None) -> tuple[int, int]:
    """返回（最小买入股数，递增单位）。"""
    if board == "科创板":
        return 200, 1
    if board == "北交所":
        return 100, 1
    return 100, 100


def round_buy_shares(shares: float, board: str | None) -> int:
    """把想买的股数向下取到合规数量，不足最小买入量返回 0。"""
    minimum, step = lot_rule(board)
    if shares < minimum:
        return 0
    return int(minimum + ((int(shares) - minimum) // step) * step)
