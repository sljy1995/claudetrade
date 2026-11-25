---
name: research-iteration
description: Run one autonomous research iteration. Choose the next idea, pre-register it, develop it, have it critiqued, test the holdout once, decide, and log it. Use for scheduled research runs, or whenever asked to "find new alpha" or "keep improving the strategy".
---

# One research iteration

The goal is to raise the **deflated Sharpe of the diversified portfolio** toward the owner's target (≥ 1.2, RESEARCH.md §5a), without fooling ourselves. Follow RESEARCH.md and RISK.md exactly. Where this skill and those files disagree, they win.

One iteration = one idea taken to a decision, or to a clearly recorded stopping point if the run's time runs out. Never leave a half-finished result unlogged.

## 0. Orient (always)

1. Read the **Lessons learned** summary, the Index and the Portfolio summary in `STRATEGIES.md`.
2. Check `research/registry.yaml` for anything mid-flight: an entry in `in_development` or `critic_review`, or paper strategies due for monitoring. **Finishing in-flight work comes before starting new ideas.**
3. If there are paper or live strategies, run the monitoring checks first (RISK.md §7, RESEARCH.md §8). Record any demotion or decay finding, and tell the owner about any state change.

## 1. Choose the idea

Choose by **expected marginal value to the portfolio**, not by expected standalone Sharpe:

- Prefer families that are under-explored in the scorecard, or likely uncorrelated with current paper and live strategies.
- Prefer open questions listed in Lessons learned.
- Avoid failure modes already listed in "What has failed", unless you can say concretely why this attempt is different.
- Run the **prior-art check** (STRATEGIES.md): if the idea correlates above 0.7 with an existing entry, log it as a variant of that entry.

## 2. Pre-register (commit before any backtest)

1. Create `research/ideas/<id>.md` and a new STRATEGIES.md entry from the template.
2. Fill in **Pre-registration** completely: hypothesis and rationale, expected regime, universe, signal and timing, data limitations, benchmark, exits, the **selection rule**, the holdout boundary and the kill criteria.
3. Commit it. The commit hash goes in the entry. Anything changed after this point is a new trial, and is recorded as one.

## 3. Develop

1. Implement the signal and strategy under `src/claudetrade/`, with unit and leakage tests. Run the relevant tests (not the full suite unless it's needed).
2. Run CPCV on the development set only. **Every run appends to `research/trials.jsonl`**, including crashed and abandoned ones.
3. Produce the full report (RESEARCH.md §4).
4. If a kill criterion triggers, stop and go to step 6 as `rejected`.
5. Iterate only within the pre-registered design space. A bigger change means a new pre-registration (variant ID).

## 4. Critique

1. Launch the `strategy-critic` subagent with the ID and stage `pre-holdout`.
2. Record every finding and your response in the entry's Critic review table.
3. Fix Criticals and re-run (each re-run is a trial). Fix Majors, or mark them "needs owner acceptance". **Don't proceed with an open Critical.**

## 5. Holdout (once)

Only after all rules are frozen, the critic verdict isn't `BLOCK`, and the development results pass RESEARCH.md §5:

1. Run the holdout **once**, and record the date and commit.
2. Never re-run it after a change. If anything changes later, the holdout is burned, and that is recorded.

## 6. Decide and log

1. Choose the outcome: `rejected`, `inconclusive` or `paper`, with the reason, per RESEARCH.md §5.
2. Fill in **What went well / what failed**, with the root cause and one lesson.
3. Update the STRATEGIES.md Index, the running trial count, the Portfolio summary (recompute the portfolio's deflated Sharpe with and without this strategy), and **rewrite the Lessons learned summary**.
4. Update `research/registry.yaml`.
5. Commit on a branch and open a PR titled `research: <id> <outcome>`.

**Never** write `config/live_approved.yaml`, never loosen RISK.md primitives or circuit breakers, and never place a non-paper order. If a strategy meets the RISK.md §10 paper → live gates, set it to `live_candidate`, run the critic with stage `pre-live`, and **ask the owner**.

## 7. Report

End with a short summary for the owner:

- What was tried, and the outcome with key numbers (deflated SR, DSR, PBO, marginal ΔSR to the portfolio).
- The current portfolio deflated Sharpe against the 1.2 target.
- What's next, and anything needing the owner's decision.

Keep it short. The detail lives in STRATEGIES.md.
