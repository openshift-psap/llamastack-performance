"""
Realistic load shape - gradual ramp up, peak, then cool down.

Simulates real-world traffic patterns where load builds gradually,
sustains at peak, and then tapers off.

Pattern:
  1. Warm-up:   Gentle ramp to get the system going
  2. Ramp:      Increase load to target
  3. Peak:      Sustain full load
  4. Taper:     Gradually reduce
  5. Cool-down: Low load before stopping

Configuration via environment variables:
    USERS:       Target peak users (default: 50)
    SPAWN_RATE:  Base spawn rate (default: 5)
    RUN_TIME_SECONDS: Approximate total duration in seconds (default: 300)
"""
import os
from locust import LoadTestShape


class RealisticShape(LoadTestShape):
    """
    Realistic traffic pattern - warm-up → ramp → peak → taper → cool-down.
    Total duration is approximately RUN_TIME_SECONDS.
    """

    def tick(self):
        run_time = self.get_run_time()

        peak_users = int(os.environ.get("USERS", "50"))
        spawn_rate = int(os.environ.get("SPAWN_RATE", "5"))
        total_time = int(os.environ.get("RUN_TIME_SECONDS", "300"))

        # Distribute time across phases (as fractions of total)
        # 10% warm-up, 15% ramp, 40% peak, 20% taper, 15% cool-down
        t_warmup = int(total_time * 0.10)
        t_ramp = int(total_time * 0.25)    # cumulative
        t_peak = int(total_time * 0.65)    # cumulative
        t_taper = int(total_time * 0.85)   # cumulative
        t_end = total_time                 # cumulative

        warmup_users = max(2, int(peak_users * 0.1))
        ramp_users = int(peak_users * 0.5)
        taper_users = int(peak_users * 0.3)
        cooldown_users = max(2, int(peak_users * 0.05))

        if run_time < t_warmup:
            # Phase 1: Warm-up - small number of users
            return (warmup_users, max(1, spawn_rate // 2))

        elif run_time < t_ramp:
            # Phase 2: Ramp - increase to half capacity
            return (ramp_users, spawn_rate)

        elif run_time < t_peak:
            # Phase 3: Peak - full load sustained
            return (peak_users, spawn_rate)

        elif run_time < t_taper:
            # Phase 4: Taper - reduce load
            return (taper_users, spawn_rate)

        elif run_time < t_end:
            # Phase 5: Cool-down - minimal load
            return (cooldown_users, spawn_rate)

        else:
            # Test complete
            return None
