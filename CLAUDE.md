# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project goal

This is a Python research and trading system. Claude develops, validates and runs **short- to medium-term equity strategies** in it, mostly without supervision. The owner wants:

1. **Selection.** Find equities with steady, risk-adjusted growth. Prefer consistency (Sharpe, Sortino, low drawdown) over raw return.
2. **Timing.** Enter and exit around opportune moments: cut before expected drawdowns and take gains. Holding periods run from days to a few months, not buy-and-hold.
3. **Diversification.** Build portfolios across sectors, factors and correlation clusters, not just a list of tickers.
4. **Method.** Statistical and quantitative signals, combined with a macro and fundamental view of each equity and of the regime it trades in.
5. **Validation.** Backtest every strategy with strict train / validation / test splits over time.
6. **Self-critique.** Keep generating new alpha ideas, try to break your own results, and retire what doesn't hold up.
7. **Execution.** Connect to Interactive Brokers. Paper trade first, and trade live automatically only after the gates below are passed.

The repo started empty on 2026-11-25, so the layout and commands below are the **intended** design. Build to them, and update this file when reality differs.

## Companion docs

Read these before working in the area they cover. They override anything more general in this file.

- [RISK.md](RISK.md) covers risk model, sizing, stops, drawdown control, halt states, circuit breakers and the paper → live promotion rules. Only the owner changes its primitives, breakers and promotion rules.
- [RESEARCH.md](RESEARCH.md) covers data hygiene, partitioning, multiple-testing corrections, required reports, pass criteria, the critic checklist and alpha search. The owner sets the portfolio Sharpe target.
- [STRATEGIES.md](STRATEGIES.md) is the logbook of every idea (never delete entries), with a lessons-learned summary at the top. Read it and run the prior-art check before proposing a new idea. Update it at every status change.

## Hard rules (never bypass)

- **Live trading needs the owner's explicit approval each time a strategy is promoted to live.** It is never enabled by default. Live mode requires `TRADING_MODE=live` *and* a per-strategy entry in `config/live_approved.yaml` that the owner wrote. Claude may propose a promotion, but never writes that approval itself.
- **Paper before live.** A strategy goes live only after passing the statistical paper → live gates in RISK.md §10. The system never increases real-money risk on its own.
- **Risk controls follow [RISK.md](RISK.md).** They are configured in `config/risk.yaml` and enforced in `execution/risk.py` before every order and after every fill and mark. Strategy code can't override them. Changes to the owner primitives, circuit breakers or promotion rules need the owner's approval.
- **Never touch the holdout while developing a strategy** (RESEARCH.md §2). If a strategy is changed after seeing holdout results, that window is burned.
- Credentials (IBKR account IDs, API keys) come from `.env`, which is git-ignored. Never log or commit them.

## Validation and research loop

[RESEARCH.md](RESEARCH.md) is the full protocol. In short:

- **Partitioning.** The holdout is the most recent period, sized by minimum track record length (≥ 18 months). Its boundary is fixed before development starts and it is evaluated once. Development uses combinatorial purged cross-validation (CPCV) with walk-forward fitting, purge and embargo.
- **Point-in-time data only.** This covers lookahead, survivorship, report-date-lagged fundamentals and vintage macro data. Execution is at t+1 or later.
- **Multiple testing.** Every variant goes in `research/trials.jsonl`. Promotion requires Deflated Sharpe ≥ 0.95 and PBO ≤ 0.25, plus robustness to parameters, costs, regimes and P&L concentration.
- **Portfolio target.** The goal is a diversified multi-equity portfolio with a deflated Sharpe ≥ 1.2 net of costs. Individual strategies qualify by raising it.

For each idea:

1. **Hypothesis first.** Write `research/ideas/<id>.md` before testing it. Include the economic rationale, expected regime, the selection rule and the kill criteria.
2. **Build** the signal and strategy, with unit and leakage tests.
3. **Develop** with CPCV on the development set. Log every trial.
4. **Critique.** An adversarial review using the RESEARCH.md §6 checklist. Critical findings block promotion.
5. **Final test** on the holdout, once, after all rules are frozen.
6. **Decide** whether to reject, mark inconclusive or promote to paper, and record the outcome. Track status in `research/registry.yaml` (`idea → backtested → paper → live → retired`).
7. **Monitor** paper and live performance against the predictive distribution, and shrink or retire strategies as their Sharpe posterior decays.

Search continuously across alpha families (RESEARCH.md §7). Prefer ideas likely to be uncorrelated with existing strategies, because a strategy's value is its marginal contribution to the portfolio.

## Intended architecture

```
data/            # loaders + local cache (parquet). Market data, fundamentals, macro (FRED)
features/        # pure functions: prices/fundamentals -> point-in-time features
signals/         # features -> scores/positions per strategy
strategies/      # strategy classes composing signals + entry/exit rules
portfolio/       # selection, sizing (vol targeting / risk parity), diversification constraints
backtest/        # event-driven engine, cost model, splits/walk-forward, metrics, reports
execution/       # broker abstraction, IBKR adapter, risk checks, order management
research/        # idea notes, trial log, strategy registry, generated reports
config/          # risk.yaml, universe definitions, live_approved.yaml (owner-written)
tests/
```

Key design points:

- **One strategy interface for backtest, paper and live.** A strategy emits target weights. The backtest engine and the live executor both consume that same output. Never put separate backtest-only logic in strategy code.
- **Broker abstraction.** `execution/broker.py` defines the interface. It has a `SimBroker` for backtests and an `IBKRBroker` for paper and live trading. Paper versus live is a config or port difference, not a code difference.
- **Macro layer.** Regime indicators (rates, yield curve, credit spreads, VIX, PMI, inflation) feed both features and portfolio-level risk scaling. Per-equity fundamental and macro sensitivity (for example, rate beta and sector cyclicality) informs selection.

## Interactive Brokers

- Use the `ib_async` library (the maintained successor to `ib_insync`) against TWS or IB Gateway running locally.
- Ports: **paper** 7497 (TWS) / 4002 (Gateway), **live** 7496 (TWS) / 4001 (Gateway). Default to paper. The adapter must refuse a live port unless the hard-rule conditions above are met.
- Reconcile positions with the broker at startup and periodically. Treat the broker as the source of truth for positions and fills.

## Commands (intended)

```bash
uv sync                                   # install deps (pyproject.toml)
uv run pytest                             # all tests
uv run pytest tests/test_backtest.py::test_no_lookahead -q   # single test
uv run ruff check . && uv run ruff format .                    # lint / format
uv run python -m backtest.run --strategy <name> --split validation
uv run python -m execution.run --strategy <name> --mode paper
```

## Data sources (starting point)

- Prices: yfinance for prototyping. It is free but has survivorship bias and adjustment quirks. Upgrade to a point-in-time source (for example, IBKR historical data, Polygon, or Norgate) before relying on results.
- Macro: FRED (`fredapi`).
- Fundamentals: point-in-time when available. Otherwise lag conservatively (for example, 45–90 days after period end).

Cache raw downloads under `data/cache/` (git-ignored). Make backtests reproducible from cached data and fixed seeds.
