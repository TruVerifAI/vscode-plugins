# Example — auditing a strategy backtest before committing capital

**Situation:** a backtest looks great; verify it before sizing real money.

**Call:**
```json
{
  "tool": "audit_financial",
  "arguments": {
    "proposed_action": "Deploy the momentum strategy: rank S&P 500 by 12-1 month return, long top decile, monthly rebalance. Backtest 2010-2025 shows 14% CAGR, Sharpe 1.3, max DD 22%.",
    "relevant_data": "[Universe = current S&P 500 constituents. Prices from vendor X.]",
    "assumptions": "Frictionless rebalance assumed; no borrow/slippage modeled.",
    "tests_backtests": "[In-sample 2010-2025; no out-of-sample / walk-forward; no transaction costs.]",
    "constraints": "Risk limit 25% max drawdown; $20M to deploy."
  }
}
```

**What good output looks like:** the audit names the structural backtest defects, severity-tagged:

> **[CRITICAL] Survivorship bias** — "current S&P 500 constituents" backfills today's winners into 2010; delisted/removed names are dropped. This alone can manufacture most of the apparent edge. **[MAJOR] No transaction costs / slippage** — monthly decile rebalance turns over heavily; 14% CAGR before costs may be far lower after. **[MAJOR] In-sample only** — no walk-forward / OOS; Sharpe 1.3 is not validated out-of-sample. **[MINOR] Crowding/decay** — 12-1 momentum is among the most-harvested factors; live edge is likely decayed.

`verdict: reject` (survivorship is disqualifying as run) → `action: escalate_to_human` (a `critical` finding floors it there). Findings tagged `data_quality` (survivorship/lookahead), `liquidity` (costs), `edge_quality`/`crowding_decay` (decayed factor).

**What to do next:** rebuild the backtest with a point-in-time universe, model costs, and a walk-forward split **before** any capital — exactly the failure modes the profile targets. Then `record_outcome`. **Critique, not advice.**
