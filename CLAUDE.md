# HyperOil v2 — Quantitative Pair Trading System

## Identity

You are building a **production-grade quantitative trading system** for statistical arbitrage (pair trading) on Hyperliquid DEX. This is NOT a demo, script, or experiment. Every line of code must be written as if real money is at risk — because it will be.

## Core Principles

1. **Robustness over features** — A system that never breaks is worth more than one with 50 indicators
2. **Risk before performance** — Every new feature must pass: "can this lose money if it fails?"
3. **Observability is mandatory** — If you can't see it, you can't fix it. Structured logs, health checks, metrics
4. **Execution safety first** — Never leave a leg unhedged. Hedge emergency is not optional
5. **State must survive restarts** — Persist everything. Recovery from crash must be automatic
6. **Idempotency everywhere** — Same input, same result. No side effects from retries
7. **Small, safe changes** — One module at a time. Test before moving forward
8. **Real fills, real fees, real slippage** — Paper P&L means nothing. Track actual execution quality

## Architecture Rules

- **Python 3.12+ with asyncio** as the foundation — no threading for I/O
- **Pydantic v2** for all configuration and data validation
- **SQLAlchemy 2.0 async** with aiosqlite for storage
- **structlog** for JSON structured logging — never use print() or basic logging
- **asyncio.Queue** for inter-module communication
- **Direct async calls** for latency-critical paths (execution, risk)

## Module Boundaries

```
market_data → signal_engine → strategy → execution
                                  ↓
                              risk_engine (validates every action)
                                  ↓
                              storage (persists everything)
                                  ↓
                              observability (logs + health + dashboard)
```

- Modules communicate via typed dataclasses, never raw dicts
- Each module owns its state — no shared mutable state
- Risk engine is a gate, not a suggestion — if it says no, the action does not happen

## Trading System Rules

- **Pair:** CL (WTI) / BRENTOIL on Hyperliquid
- **Strategy:** Mean reversion of spread via Z-score grid
- **Hedge:** Always two legs. Never one-sided
- **Grid:** Configurable levels with multipliers. Max levels enforced
- **Stops:** By Z-score extreme, monetary loss, time, correlation break, regime change
- **Kill switch:** Always available — file-based and HTTP endpoint

## What NOT To Do

- Never hardcode hedge ratio as 1:1 everywhere
- Never assume correlation is permanent
- Never skip error handling on exchange API calls
- Never leave an order in unknown state — reconcile
- Never operate on stale data (>30s without update = STALE)
- Never add cosmetic features before core safety is solid
- Never optimize for backtest P&L alone — penalize drawdown and tail risk
- Never mix research logic with live execution logic
- Never use `time.sleep()` — use `asyncio.sleep()` or proper async patterns

## Code Style

- Type hints on all functions
- Dataclasses or Pydantic models for structured data
- No magic numbers — use config
- Error handling at system boundaries (exchange API, WebSocket, user input)
- Trust internal code — don't over-validate between modules
- Keep functions short and focused
- Prefer explicit over clever

## Testing Philosophy

- Test as if real money is at risk
- Edge cases matter more than happy paths
- Critical scenarios: partial fill, leg failure, stale feed, mid-execution z-score change, WebSocket reconnect with inconsistent state
- Backtest must include fees and slippage
- Replay must be deterministic and reproducible

## Config

- All parameters in `config.yaml` — never hardcoded
- Secrets in `.env` — never in code or config
- Environment-specific overrides via env vars

## Commit Style

- Small, focused commits
- Prefix: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `infra:`
- Message explains WHY, not WHAT
