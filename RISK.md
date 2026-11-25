# RISK.md

This file defines how every strategy in this repo measures and controls risk. The rules apply in backtest, paper and live modes alike. Estimation and sizing are implemented in `portfolio/risk_model.py`. Enforcement is in `execution/risk.py`, which runs **before every order and again after every fill and mark-to-market update**. Parameters live in `config/risk.yaml`.

**Principles**

1. **Limits are expressed in units of risk**, such as volatility, ATR, expected shortfall and drawdown relative to its expected distribution. Fixed dollar or percentage amounts aren't used, except for the sanity backstops in §8. The owner sets only two loss-tolerance primitives (§1). Everything else is derived from them and from point-in-time market and strategy estimates.
2. **Risk estimates are estimates.** They are built from historical and modelled data, and can understate losses during gaps, regime changes, illiquidity, and correlations converging. Stops are instructions, not guarantees of a maximum loss. Every control here is designed with that in mind.
3. **Fail closed.** If a required input is missing, stale, unstable or fails validation, block new risk and flag the issue for review. Never silently fall back to an unconstrained weight.
4. **Predeclare, don't tune.** Risk appetite and risk-model settings are fixed before an experiment starts. They are never adjusted to make a backtest look better, and a trigger is never loosened because it stopped a strategy.

**Only the owner changes the primitives in §1, the circuit breakers (§8) or the promotion rules (§10).** Claude may propose changes to estimation methods (§2–§7) in a PR. The PR must include walk-forward evidence that the change doesn't increase realised tail risk or forecast exceptions.

---

## 1. Risk appetite and derived risk budget

Statistics can measure risk and the opportunity set, but it can't decide how much loss the owner can bear. That is a preference. So the owner supplies **exactly two primitives**, and **every other risk parameter is estimated dynamically** from point-in-time data.

**Owner primitives** (the only fixed inputs):

| Primitive | Symbol | Meaning |
|---|---|---|
| Loss capacity | DD_max | The peak-to-trough loss the owner can't tolerate |
| Tolerated probability | α | The acceptable probability of reaching DD_max over the horizon H (default H = 12 months) |

**Current owner settings** (set 2026-11-25): **DD_max = 15%**, **α = 10%**, **H = 12 months**. In words: at most a 10% chance per year of a 15% peak-to-trough loss.

**Derived dynamically** (re-estimated at each monthly risk review, and immediately on a regime change; see "Update rules" below):

1. **Kelly fraction f_t (Bayesian, uncertainty-adjusted).** f_t = SR̂² / (SR̂² + SE(SR̂)²).
   - SR̂ is the portfolio's out-of-sample Sharpe from walk-forward results, deflated for multiple testing (RESEARCH.md).
   - SE(SR̂) is its Lo (2002) autocorrelation-adjusted standard error.
   - The effect: with little or noisy evidence, f → 0. As the track record grows and stays consistent, f rises toward 1. It is capped at 0.5, because full Kelly has an unacceptable drawdown profile even when its inputs are known exactly.
2. **Growth-optimal volatility.** For a portfolio with Sharpe SR, the growth-optimal (Kelly) volatility is σ_Kelly = SR. The opportunity-driven target is σ_opp,t = f_t × SR̂_t.
3. **Drawdown-constrained volatility σ_DD,t.** This is the largest σ such that P(max drawdown over H ≥ DD_max) ≤ α.
   - It is solved numerically by **stationary block bootstrap** of the portfolio's walk-forward return stream, rescaled to each candidate σ.
   - A heavier-tailed variant is run alongside it (Student-t with ν fitted by MLE on residuals, floored at ν = 3), and the smaller σ of the two is used.
4. **Target volatility.** σ*_t = min(σ_opp,t, σ_DD,t). The more restrictive of "how much the edge justifies" and "how much the loss capacity allows" applies.
5. **ES budget.** ES*_t is the 1-day 97.5% ES of a portfolio scaled to σ*_t under the binding tail model (§2). It is a consequence of σ*_t, not a separate input, so the vol and tail budgets are always mutually consistent.
6. **Per-strategy budgets.** The portfolio risk budget is split across strategies by **risk budgeting on their shrunk expected Sharpe and correlation** (maximising the portfolio's deflated Sharpe). New strategies are capped by their own f.

**Update rules.**

- **Hysteresis.** Only act on a change of more than 10% in σ*_t, and step at most 25% per month, so estimation noise doesn't create turnover.
- **One-way exception.** Decreases triggered by a REDUCE/REVIEW state (§7) apply immediately.
- **Cold start.** Before any out-of-sample record exists, SE(SR̂) is large, so f_t ≈ 0. The system therefore starts **small by construction**. Paper trading builds the evidence that lets f_t grow.
- **Logging.** Every run logs DD_max, α, H, f_t, SR̂_t, SE_t, σ_opp,t, σ_DD,t, σ*_t and ES*_t, and which constraint was binding. The run also includes a **sensitivity table** of results at DD_max ±25% and α ∈ {5%, 10%, 15%}. A strategy whose verdict flips within that range is fragile, and that goes in its STRATEGIES.md entry.

## 2. Risk estimation

**Inputs.** Use daily, time-aligned, split- and dividend-adjusted returns, only those available at the decision time. Recompute before each decision point.

- **Volatility.** Per asset, take the max of an EWMA vol (half-life ~20 days) and a 120-day vol. Taking the max makes the estimate conservative when vol is rising.
- **Covariance.** Use a 252-day sample covariance with **Ledoit–Wolf** shrinkage. Cross-check it against a factor-model covariance (market, sector, size, value, momentum). If the two disagree badly, use the more conservative one (higher portfolio vol) and log the disagreement.
- **Tail risk.** Use two views:
  - **Ordinary conditions:** filtered historical simulation (FHS) ES, using returns rescaled by the current vol.
  - **Tails:** stress ES from historical windows (2008, Mar 2020, 2022) and synthetic shocks. The synthetic shocks are a vol spike (×2.5), correlation convergence (all pairwise ρ → 0.8), a gap of −3σ overnight, and spreads widening ×3.
  - The binding tail estimate is **max(FHS ES, 0.5 × stress ES)**.
- **Regime.** A vol and correlation regime indicator (VIX percentile, average pairwise correlation, HY credit spread) feeds the exposure scaler in §5. It is calibrated walk-forward.

**Fallbacks (fail closed).**

| Condition | Action |
|---|---|
| < 252 aligned observations for a name | Use its sector-ETF vol and correlation as a proxy, with ×1.5 vol haircut, and cap it at half the normal risk budget. If there are < 60 observations, block the name. |
| Covariance ill-conditioned (condition number > 10⁴ after shrinkage) or vol estimate jumps > 3× day-on-day without a matching price move | Treat it as a data error. Block new risk in the affected names and flag for review. |
| Market data stale (> 60s intraday, or a missing daily bar) | Block new risk in that name. |

## 3. Position sizing

The target weight for name *i* is the **minimum** of three caps:

1. **Risk-budget weight.** Risk budgeting: each position's component contribution to portfolio variance is ∝ its signal strength, scaled so the portfolio sits at σ*_t (§5).
2. **Stop-risk weight.** Shares = (ES*_t / N_target) × NAV / (k × ATRᵢ), where *k* is the stop multiple from §4. A stop-out then costs at most one name's share of the ES budget. The calculation includes a gap-through-stop allowance (§4).
3. **Fractional Kelly ceiling.** wᵢ ≤ f_t × μᵢ / σᵢ², where μᵢ is the strategy's out-of-sample expected return, **shrunk toward zero** in proportion to its standard error.

**Concentration** is limited by risk contribution, not by weight:

- No single **issuer** contributes more than **2/N_eff** of portfolio variance. Share classes of the same issuer count as one.
- No **sector, industry or correlated cluster** contributes more than **30%** of portfolio variance. Clusters come from hierarchical clustering on the shrunk correlation matrix. This stops several nominally different stocks from using up the same hidden risk budget.
- No single **style factor** (size, value, momentum, quality, low-vol) has an exposure above ±1.0 standardised units.
- The **effective number of bets** (from the eigen-decomposition of risk contributions) is ≥ 8 when fully invested.
- **Market beta** (to SPY) stays within [0.5, 1.1], unless the regime scaler is de-risking.
- **Stressed check.** Under the correlation-convergence scenario, no cluster's contribution to stress ES exceeds 40%.

**Liquidity and capacity.**

- Order size ≤ 5% of expected volume over the execution window.
- Expected impact uses the square-root model (c · σ · √(Q/ADV)) plus half the spread. Skip the trade if its cost is more than 25% of the expected alpha.
- Compute the **days-to-liquidate** for each position at 10% participation under stressed (×0.5) volume. Cap it at 3 days.

**Report every rebalance.** For each holding, record its weight, marginal and component risk contribution, stressed ES contribution, liquidity usage (% ADV, days-to-liquidate), and which of the three sizing caps was binding.

## 4. Stops and exits

Each strategy defines its exits from the families below, justified by its thesis. Parameters are calibrated **only within development folds**. Fixed-percentage stops for all equities aren't allowed.

- **Volatility stop.** Initial stop at entry − k × ATR(14), using a point-in-time ATR. *k* (typically 2–3) is chosen to maximise development-fold expectancy.
- **Trailing stop.** A chandelier exit (highest close since entry − k × current ATR). It adapts as vol changes, and is reported separately as "giving back unrealised gain" versus "realised loss".
- **Thesis exit.** Exit when the measurable condition that justified entry no longer holds. Examples are a signal z-score crossing back, a factor rank dropping below its threshold, or a fundamental input being revised.
- **Time exit.** Exit if the trade hasn't reached +1R within the strategy's predeclared holding horizon. The default horizon is 1.5 × the median holding time of winners in development folds.
- **Profit-taking.** Optionally scale out at statistically defined targets, such as +2R or the upper band of a volatility channel.
- **Event exit.** Cut or close positions before earnings if the development-fold backtest shows negative expectancy through earnings, or if the implied earnings move exceeds k × ATR.
- **Portfolio-level de-risking** when aggregate risk exceeds budget (§5–§6).

**Execution realism in backtest and paper trading.** Stops fill at the **next available price**, not the stop price. Overnight gaps fill at the open, and stops fill after a one-bar delay plus spread and slippage. Report the distribution of **stop slippage (realised loss ÷ planned 1R)**. If its 95th percentile exceeds 2R, the strategy's stop design is invalid.

## 5. Portfolio exposure scaling

Gross exposure = min(1, σ*_t / σ̂_portfolio) × regime_scaler × drawdown_scaler. It is capped at 1.0, so there is no leverage.

- `regime_scaler` ∈ [0.25, 1.0]. It is a predeclared function of the regime indicator, calibrated walk-forward.
- `drawdown_scaler` is defined in §6.
- If the binding ES (§2) is above ES*_t after scaling, trim the positions with the highest marginal ES contribution until it fits.

## 6. Daily loss and drawdown control

**Loss measurement.** P&L includes realised **and** mark-to-market P&L on open positions, plus fees, slippage and borrow costs. Drawdown is measured from the **peak marked-to-market NAV**, net of costs and adjusted for cash flows.

**Daily loss threshold.** The session threshold is L_day = 1.5 × the forecast 1-day ES (§2), set at the open. It is never reset or recomputed during the session.

- If the loss exceeds L_day, block new risk for the session. Only the predeclared risk-reducing exits (§4) are allowed, and they are simulated with realistic costs.
- The system then enters a **REVIEW** state that persists into the next session, until the review conditions in §7 are met.

**Drawdown versus expectation.**

- For each strategy and for the portfolio, run a **stationary block bootstrap** of walk-forward out-of-sample returns, alongside a heavier-tailed variant (Student-t, ν = 4, rescaled). Together these give the max-drawdown distribution over the trading horizon. Record DD₅₀, DD₉₅ and DD₉₉, taking the worse of the two methods.
- **Portfolio drawdown scaler:** once DD_current > DD₅₀, drawdown_scaler = max(0, 1 − (DD_current − DD₅₀) / (DD_max − DD₅₀)). Exposure falls linearly to zero at DD_max. The schedule is predeclared and evaluated only in development and walk-forward folds.
- **Strategy-level triggers:**
  - DD > DD₉₅ → halve that strategy's allocation and enter REVIEW.
  - DD > DD₉₉ → pause the strategy and **demote it to paper**.
- **Recovery.** Exposure re-scales upward only as the drawdown recovers, with at least 5 sessions between increases.

## 7. Model monitoring, halt states and review

**Monitor** on a rolling 250-day window, with review thresholds stored in the run config:

- Forecast versus realised volatility: the bias ratio should stay within [0.8, 1.25].
- **ES/VaR exceptions:** use the Kupiec proportion-of-failures test and the Christoffersen independence test at 97.5% VaR. Rejection at p < 0.05 means the model has failed.
- Drawdown versus its DD distribution, average pairwise correlation, concentration (N_eff), turnover, liquidity usage, and **implementation shortfall** versus the modelled cost.

**State machine.** State persists across restarts, in `state/risk_state.json`.

| State | Entered when | Allowed |
|---|---|---|
| `ACTIVE` | Normal operation | All orders that pass checks |
| `REDUCE` | DD > DD₉₅, ES over budget, or regime de-risking | Only orders that reduce risk |
| `REVIEW` | Daily loss > L_day, a failed model-exception test, or a data-quality failure | No new risk. Only predeclared exits. |
| `HALTED` | Any circuit breaker (§8) | No orders. **Only the owner can resume.** |

**Restart from REVIEW** requires all of the following, predeclared and logged:

1. Data validated.
2. P&L attribution explains the loss (market, factor, idiosyncratic, execution).
3. Regime assessment.
4. If the model failed an exception test, an out-of-sample re-evaluation on data **after** the breach.

The model is never modified *in response to* a single breach to make it pass. Changes go through a PR as described in the principles above.

**Audit log.** Every alert, state transition and halt records its inputs, the calculation, the threshold, the resulting state and the action taken (`logs/risk_events.jsonl`).

## 8. Circuit breakers (hard backstops)

These are deliberately **absolute** limits. They aren't risk-model outputs. They exist to catch model failure and software bugs, and are set wide enough that they rarely bind in normal operation.

| Trigger | Action |
|---|---|
| Portfolio drawdown ≥ DD_max | Flatten everything → `HALTED` |
| 1-day loss > 3 × forecast 1-day ES | `HALTED`. The model is wrong by too much. |
| Any position > 10% of NAV | Trim immediately and log a sizing bug |
| Gross exposure > 100% | Reject the order |
| File `config/HALT` exists | `HALTED` |
| Broker/data reconciliation mismatch | `HALTED` until the positions are reconciled |
| > 50 orders/day across all strategies | `HALTED` (treated as a runaway loop) |

**Order hygiene.** Use limit or marketable-limit orders only, with the limit within 0.5 × ATR of the last price. Reject duplicate orders, and trade only in the regular session.

## 9. Instruments and account

- US-listed common stocks and ETFs. Long-only and no leverage until the owner changes this. No options, futures, or leveraged/inverse ETFs.
- Universe filter: price ≥ $5 and 20-day ADV ≥ $10M, so the impact model in §3 stays meaningful.
- If the account is a margin account under $25k, respect the Pattern Day Trader rule (≤ 3 day-trades per 5 business days). *(Owner to confirm account type and size.)*

## 10. Promotion: backtest → paper → live

**No automatic increase in real-money risk.** The system can demote automatically. Only the owner can promote a strategy or scale it up live.

**Backtest → paper.** The strategy passes the thresholds in RESEARCH.md, including the one-time holdout test.

**Paper → live.** All of these must be true:

1. **Sample large enough:** the paper Sharpe's standard error (Lo 2002, autocorrelation-adjusted) is ≤ 0.5. The minimum is 3 months and 30 round trips.
2. **Consistent with the backtest:** a bootstrap test of paper versus out-of-sample backtest Sharpe isn't rejected (p > 0.10), paper DD ≤ DD₉₅, and the paper period passes the Kupiec/Christoffersen tests.
3. **Execution quality:** implementation shortfall ≤ 1.5 × the modelled cost, the stop-slippage 95th percentile is ≤ 2R, and there are no unresolved incidents.
4. **Adds to the portfolio:** marginal Sharpe contribution to the existing live book > 0 at the proposed allocation.
5. Claude writes a promotion summary in STRATEGIES.md.
6. **The owner adds the strategy to `config/live_approved.yaml`**, with its allocation. Claude never writes this file.

**Live ramp-up.** The risk budget starts at 25% of target, then steps to 50% and 100%. Each step requires the item 2 tests to pass on the live sample **and** the owner to update the allocation in `live_approved.yaml`.

**Live → paper (automatic demotion).** Demote on any of the following:

- DD > DD₉₉.
- A failed exception test.
- A CUSUM break in mean return versus the backtest.
- Turnover or holding period drifting > 2× from the backtest.
