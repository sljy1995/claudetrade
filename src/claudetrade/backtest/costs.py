"""Transaction cost model: commission + half-spread + square-root market impact.

All costs are returned in dollars for a single fill. Defaults are conservative retail
assumptions (IBKR tiered-style commissions); calibrate against realised implementation
shortfall once paper trading starts (RISK.md §10).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class CostModel:
    commission_per_share: float = 0.0035
    min_commission: float = 0.35
    max_commission_pct: float = 0.01  # cap as a fraction of trade value
    half_spread_bps: float = 5.0
    impact_coef: float = 0.7  # c in  c · σ_daily · sqrt(Q / ADV)
    multiplier: float = 1.0  # cost stress (RESEARCH.md: ×1.5, ×2)

    def commission(self, shares: float, price: float) -> float:
        shares = abs(shares)
        if shares == 0:
            return 0.0
        c = max(self.min_commission, self.commission_per_share * shares)
        return min(c, self.max_commission_pct * shares * price)

    def cost(self, shares: float, price: float, adv_shares: float, sigma_daily: float) -> float:
        """Total dollar cost of trading ``shares`` at ``price``."""
        shares = abs(shares)
        if shares == 0:
            return 0.0
        notional = shares * price
        spread = notional * self.half_spread_bps / 1e4
        if self.impact_coef == 0:
            impact = 0.0
        elif adv_shares > 0 and np.isfinite(sigma_daily):
            impact = notional * self.impact_coef * sigma_daily * np.sqrt(shares / adv_shares)
        else:
            impact = notional * 0.01  # unknown liquidity/vol: penalise heavily (fail-safe)
        return self.multiplier * (self.commission(shares, price) + spread + impact)

    def scaled(self, multiplier: float) -> CostModel:
        return CostModel(**{**self.__dict__, "multiplier": self.multiplier * multiplier})


ZERO_COST = CostModel(0.0, 0.0, 0.0, 0.0, 0.0)
