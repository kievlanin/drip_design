import math
import unittest

from modules.hydraulic_module.emitter_block_equivalent import (
    block_flow_at_ref,
    equivalent_k_at_ref,
    equivalent_k_from_total_flow,
)
from modules.hydraulic_module.lateral_drip_core import emitter_flow_lph


class TestEmitterBlockEquivalent(unittest.TestCase):
    def test_equivalent_k_matches_sum_for_identical_k(self):
        x = 0.5
        k = 1.2
        pressures = [12.0, 10.0, 8.0, 6.0]
        p_ref = 11.0
        k_eq = equivalent_k_at_ref(pressures=pressures, k_each=k, x=x, p_ref=p_ref)
        q_sum = sum(k * (p**x) for p in pressures)
        q_ref = block_flow_at_ref(k_eq, x, p_ref)
        self.assertAlmostEqual(q_ref, q_sum, places=10)

    def test_equivalent_k_with_per_emitter_k(self):
        x = 0.5
        k_each = [1.0, 1.15, 1.2, 1.3]
        pressures = [10.0, 9.5, 9.0, 8.5]
        p_ref = 9.8
        k_eq = equivalent_k_at_ref(pressures=pressures, k_each=k_each, x=x, p_ref=p_ref)
        expected = sum(k_i * (p_i**x) for k_i, p_i in zip(k_each, pressures)) / (p_ref**x)
        self.assertAlmostEqual(k_eq, expected, places=12)

    def test_equivalent_k_from_total_flow_matches_emitter_flow(self):
        x = 0.5
        k = 1.18
        p_ref = 10.0
        pressures = [11.2, 10.6, 9.9, 9.1]
        q_total = sum(emitter_flow_lph(p, 1.0, compensated=False, k_coeff=k, x_exp=x) for p in pressures)
        k_eq = equivalent_k_from_total_flow(q_total=q_total, x=x, p_ref=p_ref)
        q_pred = block_flow_at_ref(k_eq, x, p_ref)
        self.assertAlmostEqual(q_pred, q_total, places=9)

    def test_handles_nonpositive_reference_pressure(self):
        x = 0.5
        k_eq = equivalent_k_from_total_flow(q_total=12.0, x=x, p_ref=0.0)
        self.assertTrue(math.isfinite(k_eq))
        self.assertGreaterEqual(k_eq, 0.0)

    def test_raises_on_mismatched_lengths(self):
        with self.assertRaises(ValueError):
            equivalent_k_at_ref(pressures=[10.0, 9.0], k_each=[1.0], x=0.5, p_ref=10.0)


if __name__ == "__main__":
    unittest.main()
