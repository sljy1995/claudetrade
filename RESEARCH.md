# RESEARCH.md

This file is the research and validation protocol. Every strategy must pass it before it reaches paper trading (RISK.md §10). Its purpose is to separate real, persistent edges from the backtest overfitting that comes with searching many ideas.

A backtest is evidence about a historical simulation. It is not proof of future performance. Reports distinguish **fact** (what the simulation produced), **estimate** (modelled quantities and their uncertainty) and **interpretation**. A strategy is never described as "proven" or "guaranteed".

**Principles**

1. **The hypothesis comes first.** Every strategy starts from an economic rationale: who is on the other side of the trade, and why the edge persists. A pattern found by search without a rationale counts as noise until it is proven otherwise, with a stricter bar (§5).
2. **Every trial counts.** Every variant ever run is logged. Significance is judged against the whole search, not the best result.
3. **Thresholds are statistical.** Pass criteria are expressed as significance levels, posterior probabilities and confidence intervals. Their required size scales with the amount of evidence and the number of trials. The one owner-set performance target, a Sharpe of 1.2, applies to the **diversified portfolio** (§5a). It is measured on the *deflated* estimate, so it rises with the number of trials, not just with raw backtest luck.
4. **The holdout is sacred.** It is evaluated once. Looking at it and then changing the strategy invalidates it.

---

## 1. Data hygiene (point-in-time)

- Use **only information available at decision time.** Prices are as-traded and adjusted with factors known at the time. Fundamentals are keyed to their **filing/report timestamp**, never period end. Macro series use **first-release (vintage) values** where they exist (ALFRED), because revised GDP or payrolls weren't known then.
- **Universe membership is point-in-time** and includes delisted names. Delisting returns use the source's value, or −30% if unknown for performance-related delistings. If the data source can't do this, the report must be flagged `SURVIVORSHIP_BIASED`, and the strategy can't pass to paper on that evidence alone.
- **Corporate actions and identifiers.** Handle splits, dividends, spin-offs, mergers, ticker changes and CUSIP/identifier changes, using a permanent security ID rather than the ticker. Missing values are imputed only with information available at the time, or the observation is dropped. Forward-filling across gaps longer than the data's normal publication lag isn't allowed.
- **Execution timing.** A signal computed on the close of t trades at the open of t+1 or later, at modelled cost (RISK.md §3). Same-bar execution is a test failure.
- **Automated leakage tests** in `tests/` cover these cases:
  - Shifting all inputs forward by one bar must not improve results.
  - Features must be invariant to appending future data.
  - Fundamentals must never be used before their timestamp.

## 2. Data partitioning

The split isn't a fixed percentage. It's sized so each stage has the statistical power it needs.

- **Holdout (test) set.** This is the most recent contiguous block, which is the period most like the future.
  - Its length is the **Minimum Track Record Length** (Bailey & López de Prado, 2012). That is the length needed to reject SR ≤ 0 at 95% confidence, given the strategy's development-stage Sharpe, skew and kurtosis.
  - The floor is 18 months, and the block should contain at least one high-volatility regime where the history allows.
  - The holdout boundary is **fixed and recorded before development starts** (`research/holdout.yaml`). It is never moved.
- **Development set.** Everything before the holdout, minus an embargo gap.
  - It is evaluated with **Combinatorial Purged Cross-Validation (CPCV)**: N groups, with k as test groups, producing a distribution of out-of-sample paths, not one backtest.
  - **Purge** training observations whose label windows overlap the test window. Apply an **embargo** equal to the maximum label horizon (or holding period) after each test block.
  - Parameter fitting inside a fold is **walk-forward** (expanding or rolling), so no fold ever trains on its own future.
- **Model selection is predeclared.** The rule that picks the final configuration from the CPCV results is written in the idea file **before** the runs. The default is the highest median-path DSR, subject to the stability criterion. Choosing after inspection by any other rule is an extra trial and counts toward N_eff.
- **Fit everything inside the fold.** Scalers, imputers, winsorisation bounds, feature selectors, PCA and hyperparameters are fit on each fold's training data only, then applied to its test data. A transformer fit on the full sample is leakage.
- **Minimum data.** If development + holdout can't give the holdout its required MinTRL, the strategy can't be validated on this frequency or universe. Don't shorten the holdout. Instead, widen the universe or raise the frequency. Failing those, mark the idea **inconclusive** and use prospective paper trading as its holdout, with the report stating the reduced confidence.

## 3. Trial registry and multiple testing

- Every run appends to `research/trials.jsonl`. Each entry records:
  - strategy ID, hypothesis ID, parameters and code commit
  - data range, the out-of-sample return series path, and the metrics
- **Effective number of trials (N_eff).** Cluster the trial return series (hierarchical clustering on correlation). N_eff is the number of clusters, so near-duplicate variants don't count as independent tests.
- **Deflated Sharpe Ratio (DSR).** Compute the probability that the true Sharpe is > 0, after correcting for N_eff, the variance of Sharpe across trials, sample length, skew and kurtosis.
- **Probability of Backtest Overfitting (PBO).** Use Combinatorially Symmetric Cross-Validation over the trial set for that hypothesis. PBO is the probability that the in-sample-best configuration ranks below median out-of-sample.
- **Factor-adjusted alpha.** Regress returns on Fama–French 5 + momentum + the market. Report the alpha t-statistic with Newey–West errors, and apply the **Harvey–Liu–Zhu** multiple-testing hurdle (Holm or BHY adjustment for N_eff).

## 4. Required analyses (every strategy report)

Each report is written to `research/reports/<strategy>/<commit>.md` and contains:

1. **Performance distribution across CPCV paths.** Give the median and 5th–95th percentiles of CAGR, vol, Sharpe, Sortino, max drawdown, Calmar, hit rate, average win/loss, turnover, average holding period and exposure. Always compare against benchmarks (SPY, the equal-weight universe, and a simple factor proxy for the same idea). Benchmarks are chosen in advance and use **the same dates, the same universe and matched average exposure/beta**. Returns include dividends and interest earned on cash.
2. **Significance:** DSR, PBO, factor-adjusted alpha t-stat (raw and multiple-testing-adjusted), and MinTRL.
3. **Robustness:**
   - **Parameter stability.** Plot the performance surface over each parameter's neighbourhood (±20–50%), and report the ratio of the neighbourhood median to the optimum.
   - **Cost stress.** Costs ×1.5 and ×2, plus the break-even cost level.
   - **Execution delay.** Delay by +1 bar.
   - **Universe perturbation.** Random 80% subsamples, and the effect of removing the top-5 contributors.
   - **Regime breakdown.** Performance by vol regime, rate regime, market direction and decade/sub-period.
   - **P&L concentration.** Report the share of total P&L from the top year, sector and 5 names, and the leave-one-year-out and leave-one-sector-out Sharpe.
   - **Trade-level evidence.** Give the hit rate and payoff ratio with 95% confidence intervals. If the hit-rate CI half-width is above 10 percentage points (about 100 trades at a 50% hit rate), trade-level conclusions are marked `LIMITED_EVIDENCE`.
4. **Risk-model outputs from RISK.md:** DD₅₀/DD₉₅/DD₉₉, the stop-slippage distribution, the ES profile, **forecast versus realised volatility and ES exceptions** across folds, and confirmation that the RISK.md budgets were never breached in simulation.
5. **Portfolio contribution:**
   - correlation with existing paper and live strategies
   - marginal change in portfolio deflated Sharpe at the proposed risk budget
   - capacity (AUM at which impact consumes 50% of alpha)
6. **Critic review** (§6) findings and responses.
7. **Known limitations**: data gaps, bias flags, untested regimes, and assumptions the result depends on.
8. **Decision**: reject, inconclusive or promote to paper, with the reason.

## 5. Pass criteria: backtest → paper

All of these must be true. These are significance levels and posterior probabilities, so the size of edge required automatically scales with the evidence available and the number of trials run.

| Test | Pass condition |
|---|---|
| Deflated Sharpe (development, CPCV) | DSR ≥ 0.95, i.e. P(true SR > 0), corrected for N_eff, skew, kurtosis and sample length |
| Overfitting | PBO ≤ 0.25 |
| Factor-adjusted alpha | Multiple-testing-adjusted p < 0.05. Without a documented economic rationale, the hurdle tightens to adjusted p < 0.01. |
| Holdout (one-time, after all rules are frozen) | Holdout SR > 0 at 90% confidence (bootstrap), **and** inside the 90% predictive interval implied by the development CPCV distribution. A holdout far *above* development is also a red flag: investigate it for leakage. |
| Parameter stability | Neighbourhood median ≥ 70% of the optimum. Isolated peaks fail. |
| Costs | Still DSR ≥ 0.90 at costs ×2 |
| Universe and regime robustness | Positive median in ≥ 80% of universe subsamples, and in a majority of regimes (at least 2 distinct ones), with no regime loss outside DD₉₉ |
| P&L concentration | Leave-one-year-out and leave-one-sector-out Sharpe stay > 0 at 90% confidence. No single year or sector supplies most of the P&L. |
| Benchmark | Exposure-matched excess return > 0, and RISK.md budgets never breached in simulation |
| Portfolio value | Marginal portfolio deflated Sharpe > 0 at its proposed budget |
| Data | No `SURVIVORSHIP_BIASED` flag, and all leakage tests pass |
| Critic | No open critical findings (§6) |

## 5a. Portfolio target (owner-set)

The goal is a **diversified multi-equity portfolio**, built from one or more strategies, each holding many names. The owner's performance target applies to that combined portfolio:

- **SR_target = 1.2**, annualised and net of all costs. It is measured as the median CPCV-path Sharpe of the combined portfolio **after deflation**, meaning the expected maximum Sharpe under the null for the total N_eff of all trials behind its components is subtracted.
- The portfolio must also meet the RISK.md diversification constraints (effective number of bets ≥ 8, issuer, sector and cluster risk-contribution caps).
- An individual strategy doesn't need SR ≥ 1.2 on its own. It qualifies by passing the per-strategy tests in §5 and **raising** the portfolio's deflated Sharpe. Uncorrelated strategies combine: three uncorrelated sleeves at 0.8 give roughly 1.4.
- **Gate:** the live book (RISK.md §10) may only run combinations whose deflated portfolio Sharpe is ≥ 1.2. Until enough strategies have been found to reach that, research and paper trading continue, but nothing goes live.

**Outcomes.** These are three explicit states, always recorded with a reason:

- **Reject.** It failed a test with adequate evidence.
- **Inconclusive.** The evidence is insufficient: too short a history, `LIMITED_EVIDENCE`, or `SURVIVORSHIP_BIASED`. The idea may continue, but only with prospective paper trading as its holdout.
- **Promote to paper.** It passed all the tests above.

- **Fail on holdout.** The hypothesis is retired at this specification. A materially new hypothesis can be tested later, but only with a new holdout period, which means waiting for fresh data or using paper trading as the holdout.
- **Fail before holdout.** Iterate, but every iteration counts toward N_eff.

## 6. Adversarial critique (self-review)

Before any holdout evaluation, the strategy critic (a separate agent or skill) reviews the report. Its job is to break the strategy, not to approve it. Every finding is answered with evidence or a fix, and recorded in the STRATEGIES.md entry. The checklist covers these areas:

- **Leakage.** Lookahead, survivorship, restated data, or a timestamp misuse that the tests missed.
- **Selection.** Is this the best of many? Is N_eff honest? Were parameters, universe, period or costs chosen after seeing results?
- **Fragility.** Is the result concentrated in a few names, dates or one regime? Are results driven by microcaps or illiquid names? Does it depend on precise parameters?
- **Economics.** Is the rationale real and specific? Who is on the other side? Why hasn't the edge been arbitraged away? Is it a known factor in disguise (check the alpha regression)?
- **Implementation.** Is the cost model realistic for its turnover? How do stops behave through gaps? Is capacity enough? Is the signal stale by execution time?
- **Crowding and decay.** Is performance declining over time (a rolling-Sharpe trend test)? Has the idea been published (post-publication decay)?

Each finding is classed as **Critical** (blocks promotion), **Major** (must be addressed or explicitly accepted by the owner) or **Minor**.

## 7. Alpha search

The search is continuous and runs on a portfolio basis. A new strategy's value is its **marginal** contribution, so prefer ideas likely to be uncorrelated with existing ones.

- **Families to explore:**
  - time-series and cross-sectional momentum
  - short-term reversal and mean reversion (with liquidity conditioning)
  - quality, value and profitability factors
  - post-earnings-announcement drift and earnings revisions
  - low-volatility and betting-against-beta
  - volatility-regime and trend filters
  - macro-regime sector rotation (rates, curve, credit, inflation, PMI)
  - seasonality, analyst/insider signals, and combinations through ensemble/meta-labelling
- **Macro and fundamental layer.** For each equity, estimate its sensitivities (rate beta, inflation beta, USD beta, credit beta, cyclicality) by rolling regression against macro factors. Use these both as features and as risk-budget conditioners. Strategies must state which regime they expect to work in, and the regime analysis (§4.3) must confirm it.
- **Meta-labelling and ensembles** are allowed, but the combination layer is itself a trial. It is validated with the same CPCV, DSR and PBO steps, on top of the base signals' trial count.
- **Idea flow.** Each idea gets `research/ideas/<id>.md` with its hypothesis, expected regime, the prior evidence (literature), the planned test, and the kill criteria, all written **before** testing.

## 8. Post-deployment learning

- Paper and live results are compared against the development predictive distribution, using the tests in RISK.md §7 and §10.
- Decay detection: a CUSUM on daily returns versus the expected mean, and a Bayesian update of the strategy's Sharpe posterior. When the posterior P(SR > 0) falls below 0.5, the risk budget shrinks through the Kelly fraction (RISK.md §1). Below 0.2, the strategy is retired.
- Retired strategies stay in STRATEGIES.md with the reason. Their trials stay in the registry, so N_eff for related future ideas remains honest.

## 9. Reproducibility

- Every report records the code commit, data snapshot hash, config, random seeds and environment lock (`uv.lock`). Anyone can rerun a report to the same numbers from cached data.
- Results produced from uncommitted code don't count toward promotion.
