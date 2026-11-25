# STRATEGIES.md

This is the logbook of every strategy idea: tested, promoted or retired. It is the human-readable record, and the machine-readable state lives in `research/registry.yaml` and `research/trials.jsonl`. Claude updates this file at every status change. **Nothing is ever deleted.** Retired and rejected entries stay, so future research knows what has been tried and the trial count (N_eff) stays honest.

## Before proposing an idea: prior-art check

Search this log first, by family, keywords and signal definition. Then run the quantitative check:

1. **Signal correlation.** Compute the rank correlation of the new signal's cross-sectional scores with every existing entry's scores, over the development period.
2. **Return correlation.** Compute the correlation of a quick prototype's return stream with existing entries' CPCV return streams.

**Treat it as a variant if** either correlation is above 0.7 with an existing entry. A variant is not a new idea:

- It gets its own ID, but must link to the parent under **Related prior ideas**.
- Its trials count in the parent's N_eff cluster.
- It needs a stated reason why it should succeed where the parent failed.

A re-parameterised idea must never be logged as fresh, because that hides earlier attempts and inflates significance.

## Status lifecycle

`idea → in_development → critic_review → holdout_tested → {rejected | inconclusive | paper} → live_candidate → live → retired`

- `rejected`: failed a RESEARCH.md test with adequate evidence. That specification is final.
- `inconclusive`: insufficient evidence. It may continue only with prospective paper trading as its holdout.
- `live_candidate`: passed the RISK.md §10 gates and is awaiting the owner's approval in `config/live_approved.yaml`.
- `retired`: removed after decay or a demotion trigger. The reason is recorded.
- **No status other than `live` with an owner entry in `config/live_approved.yaml` authorises real-money trading.** A `paper` or `live_candidate` status is not approval.

## Portfolio summary

*Updated at each rebalance review.*

| Metric | Paper book | Live book |
|---|---|---|
| Strategies (count) | 0 | 0 |
| Deflated portfolio Sharpe (target ≥ 1.2) | — | — |
| Effective number of bets (≥ 8) | — | — |
| Current σ*_t / ES*_t / f_t (RISK.md §1) | — | — |
| Risk state (ACTIVE / REDUCE / REVIEW / HALTED) | — | — |
| Pairwise strategy correlation (max / mean) | — | — |

## Lessons learned (cross-strategy summary)

*Claude rewrites this section whenever an entry reaches a decision: rejected, inconclusive, paper, live or retired. It is the first thing to read before proposing a new idea.*

**What has worked**

| Pattern / finding | Evidence (entry IDs, key stats) | Conditions it depends on (regime, universe, costs) |
|---|---|---|
| | | |

**What has failed**

| Failure mode | Entries affected | Stage it was caught (dev / critic / holdout / paper / live) | How to avoid it next time |
|---|---|---|---|
| | | | |

**Scorecard by family:** ideas tried, how many reached paper or live, and the most common reason for rejection.

| Family | Tried | Paper | Live | Most common rejection reason |
|---|---|---|---|---|
| | | | | |

**Open questions:** hypotheses the evidence suggests but that haven't been tested yet.

## Index

| ID | Name | Family | Status | Deflated SR | DSR | PBO | Marginal ΔSR to portfolio | Last update |
|---|---|---|---|---|---|---|---|---|
| | | | | | | | | |

**Running trial count:** total trials 0, N_eff 0 (from `research/trials.jsonl`).

---

## Entry template

Copy this for each new idea (`S###` IDs, never reused). The **Pre-registration** section is written and committed *before* any backtest is run.

```markdown
### S001 · <short name>

**Status:** idea · **Family:** <momentum / reversal / quality / PEAD / low-vol / macro-rotation / ...> · **Created:** YYYY-MM-DD · **Last updated:** YYYY-MM-DD
**Related prior ideas:** <IDs, with max signal/return correlation> · **Parent (if variant):** <ID or none>

#### Pre-registration (frozen before testing; commit hash: …)
- **Hypothesis & economic rationale:** who is on the other side, why the edge persists, the literature.
- **Expected regime:** where it should work and where it should fail.
- **Universe, frequency & intended holding period:**
- **Signal definition & decision-time information** (exactly what is known when, and the publication lags):
- **Data sources & point-in-time limitations:**
- **Benchmark (exposure-matched) & cost assumptions:**
- **Entry / exit families (RISK.md §4):**
- **Selection rule for the final configuration:** (RESEARCH.md §2)
- **Holdout boundary:** (from `research/holdout.yaml`)
- **Kill criteria:** results that would reject the idea.
- **Expected correlation to existing strategies:**

#### Development results (CPCV)
- Trials run / cluster contribution to N_eff:
- Median and 5–95% across paths: return, excess return vs benchmark, Sharpe, max DD, turnover, exposure, trade count, avg holding period:
- Deflated SR; DSR; PBO:
- Factor-adjusted alpha t-stat (raw / HLZ-adjusted):
- Robustness: parameter stability ratio, costs ×1.5/×2, delay +1 bar, universe subsamples, regimes, leave-one-year/sector-out:
- Drawdown distribution DD₅₀ / DD₉₅ / DD₉₉; stop-slippage p95:
- Flags: SURVIVORSHIP_BIASED / LIMITED_EVIDENCE / none
- Report: `research/reports/<id>/<commit>.md`

#### Critic review
| # | Severity | Finding | Response / evidence | Resolved |
|---|---|---|---|---|

#### Holdout (one-time)
- Date run, commit:
- Holdout Sharpe (90% CI); inside the development predictive interval?:
- Decision: reject / inconclusive / paper, with the reason.

#### Paper trading
- Start date, allocation, Sharpe SE (target ≤ 0.5), consistency test p-value, implementation shortfall vs model:

#### Live
- Owner approval date & allocation step (25 / 50 / 100%):
- Live vs paper consistency at each step:

#### What went well / what failed
- **Went well:** what held up, and why we think so, with the evidence.
- **Failed:** what broke, the stage it was caught at, and its root cause (data, overfitting, costs, regime, execution, thesis wrong).
- **Lesson:** the one transferable takeaway, which is copied into the Lessons learned summary.

#### Next step
- The single next action and what result would change the plan. Keep it current whenever the status changes.

#### Retirement
- Date, trigger (DD₉₉ / exception test / CUSUM / posterior P(SR>0) < 0.2 / owner), lessons learned:
```

---

## Entries

*(none yet)*
