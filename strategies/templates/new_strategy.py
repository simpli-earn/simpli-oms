"""{{TITLE}}
Describe the idea in one sentence here; the app shows it under the name.

Strategy `{{STRATEGY_ID}}` for SimpliEarn Desk. The docstring's first line is the name.

How the app runs this file
  * Top to bottom with the strategies Python environment, from this file's folder.
  * It sets SIMPLI_STRATEGY_ID and, for a test run, OMS_DRY_RUN=1. OMS() reads both.
  * Everything you print shows up in the run log. An uncaught exception marks the run failed.

The only rule: decide target weights, then call oms.rebalance(). Everything else is yours.
"""
import os

from oms_client import OMS, latest_prices

STRATEGY_ID = os.environ.get("SIMPLI_STRATEGY_ID", "{{STRATEGY_ID}}")
MARKET_HOURS_ONLY = True   # outside market hours, print the plan instead of queueing orders
MIN_TRADE = 1.00           # skip changes under $1


def signal() -> dict[str, float]:
    """Return {symbol: fraction of this strategy's equity}. Leftover weight stays in cash.

    Anything the strategy holds that is not in the result is sold.
    """
    return {
        # "SPY": 0.5,
        # "QQQ": 0.5,
    }


def main() -> None:
    oms = OMS(STRATEGY_ID)
    if MARKET_HOURS_ONLY and not oms.market_is_open():
        oms.plan_only("Market is closed")

    target = signal()
    print("Target:", target or "all cash")
    if oms.strategy() is None:
        raise SystemExit(f"{STRATEGY_ID} is not registered in the OMS. Register it on the Strategies tab.")

    prices = latest_prices(set(target) | set(oms.holdings()))
    oms.rebalance(target, prices, min_trade=MIN_TRADE)


if __name__ == "__main__":
    main()
