# ARCHITECTURE.md

This is the code map and design for the system. It describes the **intended** design until the code exists. Whoever changes the structure updates this file in the same PR.

## Design principles

1. **One code path for backtest, paper and live.** Strategies emit target weights from point-in-time data. The same portfolio, risk and execution logic consumes them in every mode. Only the clock, the data feed and the broker adapter differ.
2. **Point-in-time by construction.** All data access goes through a store that takes an `as_of` timestamp and can't return anything published after it. Avoiding lookahead is a property of the API, not something left to discipline.
3. **Risk is central and can't be bypassed.** `execution/risk.py` is the only path to the broker. Strategies never talk to the broker directly.
4. **Everything is reproducible.** Every run is keyed by code commit, data snapshot hash, config hash and seed.
5. **Fail closed.** Invalid or stale inputs produce "no new risk", never a default weight (RISK.md principles).
6. **Macro must be testable.** Macro views enter the system only as time-correct, testable features or regime variables. A narrative ("rates will fall") is context for a hypothesis, never an input.
7. **Build when needed.** Create modules as concrete work requires them, not as empty scaffolding. The map below is the target, not a to-do list.

## Data flow

```
 ┌──────────── data/ ────────────┐
 │ sources → validate → PIT store │  (prices, corporate actions, fundamentals w/ report dates,
 └──────────────┬────────────────┘   macro vintages, universe membership incl. delisted)
                │ as_of(t)
        features/  (pure functions; fit-in-fold transformers)
                │
        signals/   (per-strategy scores, z-scores, probabilities)
                │
       strategies/ (combine signals + entry/exit rules → desired positions / weights)
                │
       portfolio/  (risk_model: vol, Ledoit–Wolf cov, FHS/stress ES, regime;
                │   allocator: risk budgeting, Kelly cap, concentration & factor limits,
                │   strategy-level budget split, exposure & drawdown scalers)
                │ target weights
       execution/  (risk.py pre-trade checks + state machine → order manager → broker)
                │                                           │
        SimBroker (backtest)                     IBKRBroker (paper / live)
                │                                           │
                └──────── fills, marks → risk.py post-trade checks → state/, logs/
```

## Repository layout

All packages live under `src/claudetrade/` (a src layout, so tests run against the installed package, not stray local files). `research/`, `config/`, `state/` and `logs/` sit at the repo root because they hold data, not code.

## Modules

| Package | Responsibility | Key interfaces |
|---|---|---|
| `data/` | Download, validate, normalise and cache. Permanent security IDs, corporate actions, delisting returns, macro vintages (ALFRED). **Every stored value carries `source` and `available_at`** (when it could first have been known). Snapshots are immutable and content-hashed, and revisions create a new snapshot rather than overwriting. | `PITStore.get(fields, ids, as_of)`, `Universe.members(as_of)` |
| `features/` | Stateless feature functions, plus `FoldTransformer`s (scalers, imputers, selectors) fit on train folds only. | `feature(df_as_of) -> Series` |
| `signals/` | Signal definitions, each tied to a STRATEGIES.md ID. Each output is stored with signal timestamp, input snapshot hash and strategy version, so any decision can be replayed. No sizing or execution logic here. | `Signal.score(as_of) -> Series[id → score]` |
| `strategies/` | Compose signals with entry, exit and stop rules (RISK.md §4). Declare holding horizon and expected regime. | `Strategy.target(as_of, state) -> Series[id → weight]` |
| `portfolio/` | `risk_model.py` (estimation, fallbacks), `allocator.py` (sizing and constraints), `dynamic_budget.py` (f_t, σ*_t, ES*_t from RISK.md §1), `regime.py`. | `RiskModel.estimate(as_of)`, `Allocator.allocate(targets, risk) -> weights` |
| `backtest/` | Event-driven engine on a simulated clock, with deterministic accounting of cash, dividends and corporate actions. Includes a cost and impact model, **partial and rejected fills** (participation cap, limit not reached, halts), stop/gap fill simulation, CPCV/purge/embargo splitter, metrics, DSR/PBO/MinTRL, bootstrap drawdown distributions, report generator. | `run(strategy, split, config) -> RunResult` |
| `execution/` | `risk.py` (pre- and post-trade checks, state machine, circuit breakers), `orders.py` (limit/marketable-limit logic, dedupe, order-rate guard), `broker.py` (abstract), `sim.py`, `ibkr.py` (`ib_async`), `reconcile.py`. | `Broker.place(order)`, `Broker.positions()`, `Risk.check(order, state)` |
| `research/` | Idea files, trial registry, strategy registry, holdout boundaries, reports. These are data, not code. Every run writes machine-readable metadata (`run.json`: commit, snapshot hash, config hash, seed, STRATEGIES.md ID) next to its human-readable report. **Results are append-only: existing runs are never overwritten.** | `trials.jsonl`, `registry.yaml`, `holdout.yaml` |
| `monitoring/` | Forecast-vs-realised tracking, Kupiec/Christoffersen tests, CUSUM decay, Sharpe posteriors, alerts. | `Monitor.daily(as_of)` |
| `config/` | `risk.yaml` (owner primitives and breakers), `universe.yaml`, `strategies.yaml`, `live_approved.yaml` (**owner-written only**). | |
| `state/`, `logs/` | Persistent risk state, positions snapshot, `risk_events.jsonl`, order/fill journal. Git-ignored. | |

## Run modes

| Mode | Clock | Data | Broker | Entry point |
|---|---|---|---|---|
| `backtest` | Simulated | Cached PIT store | `SimBroker` | `python -m claudetrade.backtest.run` |
| `paper` | Real (exchange calendar) | PIT store + live/delayed quotes | `IBKRBroker`, paper port | `python -m claudetrade.execution.run --mode paper` |
| `live` | Real | Same | `IBKRBroker`, live port, **only** for strategies in `live_approved.yaml` | `python -m claudetrade.execution.run --mode live` |

The mode is chosen by config and environment (`TRADING_MODE`). The live adapter refuses to connect unless every RISK.md §10 condition is met.

**Broker boundary (fail closed).**

- `IBKRBroker` checks after connecting that the account IDs reported by IBKR (`managedAccounts`) match the mode. Paper accounts begin with `DU`. In `paper` mode, the presence of a non-`DU` account is a hard failure: disconnect and halt.
- In `live` mode, the account must match the one in `live_approved.yaml`, and the strategy must be listed there.
- Tests assert that a mode/port/account mismatch never reaches order submission.
- Credentials and account IDs come only from `.env` (git-ignored) or the OS keychain.

## Daily cycle (paper/live, US/Eastern)

| Time | Step |
|---|---|
| 06:30 | Data refresh and validation (fail closed). Corporate actions. Reconcile positions with IBKR. |
| 07:00 | Risk model re-estimate, regime update, monthly dynamic-budget review if due (RISK.md §1). Set the session loss threshold L_day. |
| 07:30 | Strategies compute targets. Allocator produces weights. Pre-trade risk checks. Order list is generated. |
| 09:30–10:00 | Execute: marketable-limit orders, sliced to the participation cap. |
| Intraday (every 15 min) | Marks, post-trade checks, stop and trailing-exit monitoring, reconciliation, staleness checks. |
| 16:15 | End-of-day marks, P&L attribution, monitoring tests, STRATEGIES.md portfolio summary refresh. Alerts to the owner if the state changes. |

Rebalance frequency is per strategy (declared in its pre-registration). The cycle simply skips strategies with no rebalance due.

## Tech stack

- Python 3.12, managed with `uv` (`pyproject.toml` and `uv.lock`)
- Data: `pandas`, `pyarrow`/parquet, `polars` where speed matters
- Numerics and statistics: `numpy`, `scipy`, `statsmodels`, `scikit-learn` (Ledoit–Wolf, clustering), `arch` (volatility models)
- Data sources: `yfinance` (prototype only), `fredapi`/ALFRED, IBKR historical data via `ib_async`; a point-in-time vendor later
- Broker: `ib_async` against TWS or IB Gateway
- Config validation: `pydantic`
- Testing and linting: `pytest`, `hypothesis` (property tests for leakage and risk invariants), `ruff`

## Testing layers

1. **Unit:** features, signals, metrics (DSR, PBO, MinTRL against known reference values).
2. **Leakage:** forward-shift, future-append invariance, and fundamentals-timestamp tests (RESEARCH.md §1).
3. **Accounting and timing invariants:** event timestamps are monotonic, and no split leaks between train and test. The accounting identity holds every bar (cash + positions = NAV, after costs and dividends). Costs are applied to every fill, and corporate actions are handled correctly.
4. **Risk invariants (property-based):** no order passes that breaks a RISK.md limit, the state machine never leaves `HALTED` without the owner, and fallbacks never produce unconstrained weights.
5. **Calibration:** forecast vs realised vol and ES-exception tests run on every backtest (RISK.md §7). `HALTED`/`REVIEW` state persists across a simulated process restart.
6. **Integration:** a full backtest on a small fixed dataset, with snapshot-tested results.
7. **Broker:** `IBKRBroker` against a recorded/mock session, plus a paper-account smoke test (manual, on the owner's machine).

## Where it runs

- Research and backtests: anywhere (cloud sessions or locally).
- Paper and live: a machine that runs IB Gateway logged in to the owner's account. That's the owner's computer, or later an always-on VM. Credentials come from `.env` only.
