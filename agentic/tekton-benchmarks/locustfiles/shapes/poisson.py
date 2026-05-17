"""
Poisson PMF load shape — bell curve that rises, holds at peak, then falls.

The Poisson PMF models a natural burst pattern: load starts near zero,
rises to a peak at k ≈ λ, holds there for a configurable duration,
then gradually falls back down. This tests how a system handles
increasing load, sustained peak, and scale-down.

The test is split into three phases:
  1. Rise:  PMF from k=0 to k=λ  (load climbs to USERS)
  2. Hold:  Sustain at USERS for POISSON_HOLD_SECONDS
  3. Fall:  PMF from k=λ to k=3λ (load drops back to min)

Configuration via environment variables:
    USERS:                 Peak users at the top of the bell curve (default: 50)
    SPAWN_RATE:            Max users to spawn per second (default: 10)
    RUN_TIME_SECONDS:      Total test duration in seconds (default: 600)
    POISSON_LAMBDA:        λ parameter — controls curve steepness (default: 10)
    POISSON_RISE_SECONDS:  Seconds for the rise phase (default: 0 = auto from remaining time)
    POISSON_HOLD_SECONDS:  Seconds to hold at peak between rise and fall (default: 120)
    POISSON_MIN_USERS:     Minimum users at the tails (default: 1)
    POISSON_FALL_K_MULT:   Fall tail extends to λ * this multiplier (default: 3).
                           Lower values = gentler decay. 1.8 gives ~6% at end.
                           3.0 gives ~0% by 40% through fall (original behavior).
"""
import os
from scipy.stats import poisson
from locust import LoadTestShape


class PoissonShape(LoadTestShape):
    """
    Poisson PMF with hold — rise → hold at peak → fall.

    Rise phase maps time to PMF k=0..λ (ascending half).
    Hold phase keeps users at peak.
    Fall phase maps time to PMF k=λ..3λ (descending half).
    """

    def __init__(self):
        super().__init__()

        self.peak_users = int(os.environ.get("USERS", "50"))
        self.spawn_rate = int(os.environ.get("SPAWN_RATE", "10"))
        self.run_time = int(os.environ.get("RUN_TIME_SECONDS", "600"))
        self.lam = float(os.environ.get("POISSON_LAMBDA", "10"))
        self.hold_seconds = int(os.environ.get("POISSON_HOLD_SECONDS", "120"))
        self.min_users = int(os.environ.get("POISSON_MIN_USERS", "1"))
        self.fall_k_mult = float(os.environ.get("POISSON_FALL_K_MULT", "3"))

        self._dist = poisson(mu=self.lam)
        self._pmf_peak = self._dist.pmf(int(self.lam))

        rise_env = int(os.environ.get("POISSON_RISE_SECONDS", "0"))
        remaining = self.run_time - self.hold_seconds
        if rise_env > 0:
            self.rise_seconds = rise_env
            self.fall_seconds = remaining - rise_env
        else:
            self.rise_seconds = remaining // 2
            self.fall_seconds = remaining - self.rise_seconds

        self.t_hold_start = self.rise_seconds
        self.t_fall_start = self.rise_seconds + self.hold_seconds
        self.t_end = self.run_time

        self.k_rise_max = self.lam
        self.k_fall_start = self.lam
        self.k_fall_end = self.fall_k_mult * self.lam

        print(f"PoissonShape plan (PMF with hold):")
        print(f"  Phase 1 [0s-{self.t_hold_start}s]:     Rise (k=0..{self.k_rise_max:.0f})")
        print(f"  Phase 2 [{self.t_hold_start}s-{self.t_fall_start}s]:   Hold at {self.peak_users} users")
        print(f"  Phase 3 [{self.t_fall_start}s-{self.t_end}s]:  Fall (k={self.k_fall_start:.0f}..{self.k_fall_end:.0f})")
        print(f"  Peak users:    {self.peak_users}")
        print(f"  λ (lambda):    {self.lam}")
        print(f"  Min users:     {self.min_users}")

    def tick(self):
        elapsed = self.get_run_time()

        if elapsed > self.run_time:
            return None

        if elapsed < self.t_hold_start:
            k = (elapsed / self.rise_seconds) * self.k_rise_max
            pmf_value = self._dist.pmf(int(k))
            normalized = pmf_value / self._pmf_peak
            users = max(self.min_users, int(normalized * self.peak_users))

        elif elapsed < self.t_fall_start:
            users = self.peak_users

        else:
            fall_elapsed = elapsed - self.t_fall_start
            k = self.k_fall_start + (fall_elapsed / self.fall_seconds) * (self.k_fall_end - self.k_fall_start)
            pmf_value = self._dist.pmf(int(k))
            normalized = pmf_value / self._pmf_peak
            users = max(self.min_users, int(normalized * self.peak_users))

        return (users, self.spawn_rate)
