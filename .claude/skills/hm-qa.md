---
name: hm-qa
description: Test the trading bot as if real money is at risk — find untested scenarios, edge cases, and false safety
triggers:
  - /hm-qa
  - test bot
  - quality assurance
  - run qa
---

# HyperOil Quality Assurance Skill

You are testing a **quantitative trading bot that will operate with real money**. Your job is to find every scenario where the bot could behave incorrectly, lose money silently, or give false confidence through superficial tests.

## What You Must Test

### Mathematical Correctness
- [ ] Spread calculation matches manual computation
- [ ] Z-score matches offline calculation on same data
- [ ] Hedge ratio (OLS, rolling OLS, Kalman) produces correct values
- [ ] Rolling mean/std handles edge cases (insufficient data, zero std)
- [ ] Correlation calculation is correct
- [ ] P&L calculation includes fees and slippage correctly

### Grid Logic
- [ ] Entry triggers at correct z-score levels
- [ ] Grid levels respect max_levels limit
- [ ] Size multipliers are applied correctly per level
- [ ] Cooldown prevents rapid re-entry
- [ ] Anti-repetition blocks re-entry after recent stop at same level
- [ ] Exit triggers at correct z-score reversion
- [ ] Partial exit reduces both legs proportionally

### Execution Edge Cases (CRITICAL)
- [ ] **One leg fills, other fails** — hedge emergency fires?
- [ ] **Both legs fail** — state clean, no phantom position?
- [ ] **Partial fill on one leg** — position state correct?
- [ ] **Fill timeout exceeded** — what happens?
- [ ] **Order cancelled by exchange** — state updated?
- [ ] **Duplicate signal during pending execution** — blocked?
- [ ] **Z-score changes while execution is in-flight** — still safe?
- [ ] **Exchange returns unexpected error format** — handled?

### Risk Scenarios
- [ ] **Max levels reached** — new entry blocked?
- [ ] **Max daily loss hit** — all new entries blocked, existing managed?
- [ ] **Max cycle loss hit** — cycle closed?
- [ ] **Correlation drops below minimum** — entries blocked?
- [ ] **Regime changes to BAD mid-cycle** — appropriate action?
- [ ] **Kill switch activated** — all activity stops?
- [ ] **Kill switch deactivated** — safe resumption?

### Connectivity Scenarios
- [ ] **WebSocket disconnects** — state preserved, reconnects?
- [ ] **WebSocket reconnects with stale subscription** — re-subscribes?
- [ ] **Feed stale >30s** — detected, trading paused?
- [ ] **REST API returns 500** — circuit breaker activates?
- [ ] **REST API returns rate limit** — backoff applied?

### State Recovery
- [ ] **Bot restarts with open position** — recovers correctly?
- [ ] **Bot restarts with pending orders** — reconciles?
- [ ] **Database corrupted** — graceful degradation?
- [ ] **State snapshot out of sync with exchange** — reconciliation?

### Backtest Integrity
- [ ] **Fees applied correctly** — not zero or underestimated?
- [ ] **Slippage model realistic** — not assuming mid-price fills?
- [ ] **Walk-forward prevents overfitting** — out-of-sample tested?
- [ ] **Results reproducible** — same data, same params = same result?
- [ ] **Metrics correct** — Sharpe, drawdown, profit factor verified?

## Test Scenarios (Must Implement)

### Scenario 1: Orphaned Leg
```
Signal: LONG_SPREAD at z=-2.0
Action: Buy CL, Sell BRENTOIL
Result: CL fills, BRENTOIL rejected
Expected: Hedge emergency market order on BRENTOIL within fill_timeout_sec
Verify: No net directional exposure after resolution
```

### Scenario 2: Feed Delay
```
State: Position open at z=-1.8
Event: BRENTOIL feed stops for 45 seconds
Expected: STALE detected at 30s, trading paused, incident logged
Verify: No new entries or exits on stale data
```

### Scenario 3: Mid-Execution Z-Score Shift
```
Signal: Entry at z=2.0
Action: Orders sent for both legs
Event: Z-score drops to 1.2 before fills complete
Expected: Orders still execute (committed), no cancellation mid-flight
Verify: Position opened correctly, state consistent
```

### Scenario 4: Cascade Stop
```
State: 3 grid levels filled, z at 3.5
Event: Z-score hits 4.5 (stop_z)
Expected: All 3 levels closed, both legs per level
Verify: No residual position, P&L includes all fees, cycle logged
```

### Scenario 5: Restart Recovery
```
State: 2 levels open, pending order for level 3
Event: Process killed (SIGKILL)
Action: Bot restarts
Expected: Loads state snapshot, reconciles with exchange, resumes
Verify: Position matches exchange, no duplicate orders
```

### Scenario 6: Correlation Break
```
State: Idle, monitoring
Event: Rolling correlation drops from 0.85 to 0.55
Expected: Entries blocked, existing positions get tighter stops
Verify: No new cycles opened, risk log entry
```

## What to Look For

### Tests That Give False Confidence
- Tests that only check happy path
- Tests with mocked exchange that always succeeds
- Tests without fees/slippage
- Tests that don't verify state after failure scenarios
- Tests that pass but don't assert the right thing

### Missing Test Coverage
- Run coverage report and flag untested critical paths
- Flag any execution/ or risk/ function without tests
- Flag any state transition without test

## Output Format

```
## Test Results

### PASS (X tests)
- Brief list

### FAIL (X tests)
- Test name: what failed and why

### MISSING (X scenarios)
- Scenario: why it matters and what could go wrong

### FALSE CONFIDENCE (X tests)
- Test name: why it gives false safety
```

## Mindset

> "Test as if real money is at risk. Every untested edge case is a potential loss. Every superficial test is a false sense of security."
