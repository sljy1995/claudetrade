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

## Hard rules (never bypass)

- **Live trading needs the owner's explicit approval each time a strategy is promoted to live.** It is never enabled by default. Live mode requires `TRADING_MODE=live` *and* a per-strategy entry in `config/live_approved.yaml` that the owner wrote. Claude may propose a promotion, but never writes that approval itself.
- **Paper before live.** A strategy goes live only after at least 3 months of paper trading (and at least 50 trades) whose performance stays within the confidence band of its out-of-sample backtest.
- **Risk limits live in `config/risk.yaml` and are enforced in `execution/risk.py` before every order.** These include max position size as % of NAV, max sector exposure, max gross/net exposure, a daily loss limit, and a max drawdown kill-switch that flattens positions and halts trading. Strategy code can't override them. Loosening a limit needs the owner's approval.
- **Never touch the test (holdout) period while developing a strategy.** It is evaluated exactly once, at the end. If a strategy is changed after seeing test results, that test window is burned. Record this, and validate on fresh data (for example, paper trading).
- Credentials (IBKR account IDs, API keys) come from `.env`, which is git-ignored. Never log or commit them.

## Validation methodology

Overfitting is the main way this project can fail. Every backtest must:

- **Split by time, never randomly.** The default split is ~60% train, ~20% validation, ~20% test (most recent). Use **walk-forward** (rolling or expanding window) optimisation for any parameter fitting. Put a **purge/embargo gap** between folds that is at least as long as the label horizon.
- **Avoid lookahead.** Use features available at decision time only. Lag fundamentals to their *report date*, not period end. Signals at close t trade at open t+1 or later.
- **Avoid survivorship bias.** Use point-in-time universes that include delisted names where the data allows. If the source (for example, yfinance) can't do this, flag the result as biased in the report.
- **Model costs.** Include commissions (IBKR tiered), slippage/spread, borrow costs for shorts, and liquidity caps (for example, ≤1% of ADV).
- **Report more than one metric:** CAGR, volatility, Sharpe, Sortino, max drawdown, Calmar, turnover, hit rate, average win/loss, exposure, and beta/correlation to SPY. Always compare against benchmarks (SPY and an equal-weight universe).
- **Correct for multiple testing.** Log every variant tried (`research/trials.jsonl`), and report the deflated Sharpe ratio or probability of backtest overfitting alongside the raw Sharpe.
- **Test robustness:** parameter-perturbation sensitivity, performance by regime (bull/bear/high-vol), by sub-period, and with costs doubled. A strategy that works for only one parameter set or one period is rejected.

## Research loop (how Claude works autonomously)

For each idea:

1. **Hypothesis.** Write a one-paragraph economic rationale for *why* the edge should exist, in `research/ideas/<id>.md`. Ideas without a rationale are data-mining.
2. **Build** the signal and strategy, with unit tests.
3. **Backtest** on train, then tune on validation through walk-forward. Log every trial.
4. **Critique.** Act as an adversarial reviewer: look for lookahead, survivorship, cost sensitivity, crowding, regime dependence and correlation with existing strategies. Record findings in the idea file.
5. **Final test** on the holdout, once only.
6. **Decide** whether to promote to paper, iterate or retire, and record the reason. Keep a registry of strategy status in `research/registry.yaml` (`idea → backtested → paper → live → retired`).
7. **Monitor.** Compare paper and live strategies against their backtest expectations, and flag decay (for example, rolling Sharpe below the lower confidence bound).

Look for new alpha across families, such as momentum/trend, mean reversion, quality/value factors, earnings/post-announcement drift, volatility regimes, macro-regime tilts and cross-sectional ranking. A new strategy is valuable only if it adds to the portfolio (low correlation with existing ones), not just if it is good on its own.

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
