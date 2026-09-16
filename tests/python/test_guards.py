import unittest
from tests.python.conftest import block_network

class TestLiquidityGuards(unittest.TestCase):
    def setUp(self):
        block_network()

    def test_ideal_contract_passes_all_guards(self):
        from src.leaps_scanner.strategies.guards import evaluate_liquidity_guard, GuardStatus
        # Mid=10.0, Bid=9.8, Ask=10.2 -> Spread=0.4, rel=4% <= 6% (Pass)
        # Half-spread = 0.20 <= 0.75 (Pass)
        # OI = 500 >= 300 (Pass), Vol = 100 >= 50 (Pass), multiplier=100
        res = evaluate_liquidity_guard(
            bid=9.8,
            ask=10.2,
            open_interest=500,
            volume=100,
            avg_volume_20d=60,
            bid_size=20,
            ask_size=20,
            quote_age_seconds=5.0,
            multiplier=100,
            is_adjusted=False,
            is_rth=True
        )
        self.assertEqual(res.status, GuardStatus.PASS)
        self.assertFalse(res.is_rejected)

    def test_absolute_half_spread_exemption(self):
        from src.leaps_scanner.strategies.guards import evaluate_liquidity_guard, GuardStatus
        # High priced LEAPS: Mid = $50.0. Bid=$47.0, Ask=$53.0.
        # Spread = 6.0, rel_spread = 12% (> 10% Reject in naive relative test!)
        # But half_spread = 3.0 (> $1.50 -> Reject).
        res_reject = evaluate_liquidity_guard(
            bid=47.0, ask=53.0, open_interest=500, volume=100, multiplier=100
        )
        self.assertEqual(res_reject.status, GuardStatus.REJECT)

        # Another case: Mid = $5.0. Bid=$4.60, Ask=$5.40.
        # Spread = 0.80, relative = 16% (> 10%), BUT half_spread = $0.40 <= $0.75 (Pass)!
        # Relative or absolute entering Pass yields Pass!
        res_exempt = evaluate_liquidity_guard(
            bid=4.60, ask=5.40, open_interest=500, volume=100, multiplier=100
        )
        self.assertEqual(res_exempt.status, GuardStatus.PASS)

    def test_absolute_spread_scales_with_premium(self):
        from src.leaps_scanner.strategies.guards import evaluate_liquidity_guard, GuardStatus
        # $100 LEAPS, $2 half-spread (4% relative) must not die on the old $0.75 abs cap.
        res = evaluate_liquidity_guard(
            bid=96.0, ask=104.0, open_interest=500, volume=80, multiplier=100
        )
        self.assertNotEqual(res.status, GuardStatus.REJECT)

    def test_non_standard_multiplier_rejected(self):
        from src.leaps_scanner.strategies.guards import evaluate_liquidity_guard, GuardStatus
        # Split adjusted contract with multiplier=1000 or adjusted=True
        res = evaluate_liquidity_guard(
            bid=9.8, ask=10.2, open_interest=500, volume=100, multiplier=1000
        )
        self.assertEqual(res.status, GuardStatus.REJECT)
        self.assertIn("NON_STANDARD", res.reasons)

        res_adj = evaluate_liquidity_guard(
            bid=9.8, ask=10.2, open_interest=500, volume=100, multiplier=100, is_adjusted=True
        )
        self.assertEqual(res_adj.status, GuardStatus.REJECT)
        self.assertIn("ADJUSTED_CONTRACT", res_adj.reasons)

    def test_post_market_trash_quote_rejected(self):
        from src.leaps_scanner.strategies.guards import evaluate_liquidity_guard, GuardStatus
        # Bid=0, Ask=10.0, OI=20 (<100) -> Direct Reject
        res = evaluate_liquidity_guard(
            bid=0.0, ask=10.0, open_interest=20, volume=0, is_rth=False
        )
        self.assertEqual(res.status, GuardStatus.REJECT)
        self.assertIn("ZERO_BID_LOW_LIQUIDITY", res.reasons)

    def test_crossed_market_rejected(self):
        from src.leaps_scanner.strategies.guards import evaluate_liquidity_guard, GuardStatus
        # Bid > Ask
        res = evaluate_liquidity_guard(
            bid=11.0, ask=10.0, open_interest=500, volume=100
        )
        self.assertEqual(res.status, GuardStatus.REJECT)
        self.assertIn("CROSSED_MARKET", res.reasons)

    def test_watch_status_bucket(self):
        from src.leaps_scanner.strategies.guards import evaluate_liquidity_guard, GuardStatus
        # Relative spread = 8% (Watch), Half-spread = $1.00 (Watch) -> Result is WATCH
        res = evaluate_liquidity_guard(
            bid=23.0, ask=25.0, open_interest=150, volume=15, multiplier=100
        )
        self.assertEqual(res.status, GuardStatus.WATCH)

if __name__ == "__main__":
    unittest.main()
