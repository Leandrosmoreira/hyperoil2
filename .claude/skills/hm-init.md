---
name: hm-init
description: Initialize HyperOil project with production-grade quantitative trading system structure
triggers:
  - /hm-init
  - initialize hyperoil
  - start project
---

# HyperOil Project Initialization Skill

You are initializing a **production-grade quantitative pair trading bot** for the Hyperliquid DEX. This is not a script or demo — it is a real trading system.

## What You Must Create

### 1. Project Structure
Create the full modular architecture:
```
hyperoil2/
├── CLAUDE.md
├── pyproject.toml
├── config.yaml
├── .env.example
├── Dockerfile
├── docker-compose.yml
├── src/hyperoil/
│   ├── __init__.py, __main__.py, config.py, types.py
│   ├── core/        (orchestrator, event_bus, state)
│   ├── market_data/ (ws_feed, rest_client, orderbook, normalizer)
│   ├── signals/     (spread, zscore, regime_filter, correlation, volatility, cointegration, mean_reversion, signal_engine)
│   ├── strategy/    (grid_pairs, position_plan, lifecycle)
│   ├── execution/   (client, order_manager, fill_tracker, reconcile, hedge_emergency)
│   ├── risk/        (rules, exposure, kill_switch, gate)
│   ├── storage/     (database, models, jsonl_writer)
│   ├── backtest/    (replay_engine, simulator, metrics, optuna_runner)
│   └── observability/ (logger, health, dashboard)
├── scripts/
├── tests/
├── deploy/
└── data/
```

### 2. Stack Decisions (Non-Negotiable)
- **Python 3.12+** with **asyncio** as the async foundation
- **websockets** library for async WebSocket (not websocket-client)
- **aiohttp** for async REST calls
- **Pydantic v2** for config validation
- **SQLAlchemy 2.0 async** + aiosqlite for storage
- **structlog** for structured JSON logging
- **rich** for terminal dashboard
- **pytest + pytest-asyncio** for testing
- **hyperliquid-python-sdk** for exchange integration

### 3. Config System
- `config.yaml` with all trading parameters (symbols, grid, risk, execution, storage)
- `.env.example` with secret placeholders (API keys, wallet)
- `src/hyperoil/config.py` with Pydantic models that validate on load
- Environment variable overrides for deployment

### 4. Foundation Modules
Create working skeletons for:
- **Logger** — structlog configured for JSON (prod) and colorized (dev)
- **Storage** — async SQLAlchemy engine + session factory + base models
- **Types** — shared enums (Direction, OrderStatus, CycleStatus, ConnectionState, Regime)
- **Event bus** — simple async pub/sub for internal events
- **Orchestrator** — main async loop that wires all modules

### 5. Deployment Files
- `Dockerfile` multi-stage (build + slim runtime)
- `docker-compose.yml` with volumes for data/logs
- `deploy/systemd/hyperoil.service`
- `deploy/scripts/setup-vps.sh`

### 6. Documentation
- `CLAUDE.md` with project identity and rules
- Inline docstrings on public interfaces only

## Mindset

> "Create a foundation ready for a production quantitative pair trading bot on Hyperliquid, with production standards from day one."

Every decision must favor:
- Reliability over convenience
- Observability over silence
- Safety over speed
- Explicit over clever
- Modular over monolithic
