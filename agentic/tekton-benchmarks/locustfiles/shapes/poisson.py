"""
Poisson CDF load shape — user count rises following a Poisson CDF then settles at peak.

The Poisson CDF naturally models arrival processes: load starts near zero,
accelerates around t ≈ λ, and saturates smoothly toward the target user count.
This avoids the abrupt jumps of step-based shapes while still reaching full
load in a predictable time window.

The time axis is mapped so that t=0 corresponds to CDF≈0 and
t=POISSON_RAMP_SECONDS corresponds to CDF≈1 (specifically, CDF(2λ)).
After ramp, users hold at peak for the remainder of RUN_TIME_SECONDS.

Configuration via environment variables:
    USERS:                 Target peak users (default: 50)
    SPAWN_RATE:            Max users to spawn per second (default: 10)
    RUN_TIME_SECONDS:      Total test duration in seconds (default: 300)
    POISSON_LAMBDA:        λ parameter — controls CDF steepness (default: 10)
    POISSON_RAMP_SECONDS:  Seconds for the ramp phase (default: 120)
    POISSON_MIN_USERS:     Minimum users during ramp (default: 1)
"""
import os
from scipy.stats import poisson
from locust import LoadTestShape


class PoissonShape(LoadTestShape):
    """
    Poisson CDF ramp — smooth S-curve rise to peak users, then hold.

    The ramp phase maps elapsed time linearly to the Poisson CDF domain [0, 2λ].
    At the midpoint of the ramp (t = ramp/2), users ≈ 50% of peak.
    """

    def __init__(self):
        super().__init__()

        self.peak_users = int(os.environ.get("USERS", "50"))
        self.spawn_rate = int(os.environ.get("SPAWN_RATE", "10"))
        self.run_time = int(os.environ.get("RUN_TIME_SECONDS", "300"))
        self.lam = float(os.environ.get("POISSON_LAMBDA", "10"))
        self.ramp_seconds = int(os.environ.get("POISSON_RAMP_SECONDS", "120"))
        self.min_users = int(os.environ.get("POISSON_MIN_USERS", "1"))

        self.k_max = 2 * self.lam
        self._dist = poisson(mu=self.lam)

        print(f"PoissonShape plan:")
        print(f"  Peak users:    {self.peak_users}")
        print(f"  λ (lambda):    {self.lam}")
        print(f"  Ramp duration: {self.ramp_seconds}s  (CDF mapped over k=0..{self.k_max:.0f})")
        print(f"  Hold duration: {self.run_time - self.ramp_seconds}s")
        print(f"  Total:         {self.run_time}s")
        print(f"  Min users:     {self.min_users}")

    def tick(self):
        elapsed = self.get_run_time()

        if elapsed > self.run_time:
            return None

        if elapsed < self.ramp_seconds:
            k = (elapsed / self.ramp_seconds) * self.k_max
            cdf_value = self._dist.cdf(k)
            users = max(self.min_users, int(cdf_value * self.peak_users))
        else:
            users = self.peak_users

        return (users, self.spawn_rate)
