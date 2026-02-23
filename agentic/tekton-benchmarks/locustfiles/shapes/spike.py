"""
Spike load shape - sudden spike to stress-test the system.

Pattern:
  1. Baseline: low steady load (warm-up)
  2. Spike: sudden jump to high user count
  3. Hold: sustain the spike
  4. Drop: back to baseline

Configuration via environment variables:
    SPIKE_BASELINE_USERS:    Users during baseline (default: 5)
    SPIKE_PEAK_USERS:        Users during spike (default: 100)
    SPIKE_BASELINE_DURATION: Seconds at baseline before spike (default: 30)
    SPIKE_RAMP_DURATION:     Seconds to ramp up to peak (default: 10)
    SPIKE_HOLD_DURATION:     Seconds to hold at peak (default: 60)
    SPIKE_COOLDOWN_DURATION: Seconds to cool down (default: 30)
    SPAWN_RATE:              Users per second for non-ramp phases (default: 10)

Note: The ramp spawn rate is auto-calculated to guarantee peak users
are reached within SPIKE_RAMP_DURATION. SPAWN_RATE is used for
baseline and cooldown phases only.
"""
import os
import math
from locust import LoadTestShape


class SpikeShape(LoadTestShape):
    """
    Sudden spike pattern - baseline → rapid spike → hold → drop.
    Ramp spawn rate is auto-calculated to guarantee the spike completes in time.
    """

    def __init__(self):
        super().__init__()

        self.baseline_users = int(os.environ.get("SPIKE_BASELINE_USERS", "5"))
        self.peak_users = int(os.environ.get("SPIKE_PEAK_USERS", "100"))
        self.spawn_rate = int(os.environ.get("SPAWN_RATE", "10"))

        self.baseline_duration = int(os.environ.get("SPIKE_BASELINE_DURATION", "30"))
        self.ramp_duration = int(os.environ.get("SPIKE_RAMP_DURATION", "10"))
        self.hold_duration = int(os.environ.get("SPIKE_HOLD_DURATION", "60"))
        self.cooldown_duration = int(os.environ.get("SPIKE_COOLDOWN_DURATION", "30"))

        # Auto-calculate ramp spawn rate to guarantee peak is reached in time
        users_to_spawn = self.peak_users - self.baseline_users
        if self.ramp_duration > 0 and users_to_spawn > 0:
            self.ramp_spawn_rate = max(1, math.ceil(users_to_spawn / self.ramp_duration))
        else:
            self.ramp_spawn_rate = self.spawn_rate

        # Phase boundaries (cumulative)
        self.t_baseline_end = self.baseline_duration
        self.t_ramp_end = self.t_baseline_end + self.ramp_duration
        self.t_hold_end = self.t_ramp_end + self.hold_duration
        self.t_cooldown_end = self.t_hold_end + self.cooldown_duration

        # Log the plan so operator knows exactly what will happen
        print(f"SpikeShape plan:")
        print(f"  Phase 1 [0s-{self.t_baseline_end}s]:     Baseline at {self.baseline_users} users (spawn_rate={self.spawn_rate}/s)")
        print(f"  Phase 2 [{self.t_baseline_end}s-{self.t_ramp_end}s]:   Spike to {self.peak_users} users (spawn_rate={self.ramp_spawn_rate}/s)")
        print(f"  Phase 3 [{self.t_ramp_end}s-{self.t_hold_end}s]:  Hold at {self.peak_users} users")
        print(f"  Phase 4 [{self.t_hold_end}s-{self.t_cooldown_end}s]: Cooldown to {self.baseline_users} users")
        print(f"  Total duration: {self.t_cooldown_end}s")

    def tick(self):
        run_time = self.get_run_time()

        if run_time < self.t_baseline_end:
            return (self.baseline_users, self.spawn_rate)

        elif run_time < self.t_ramp_end:
            return (self.peak_users, self.ramp_spawn_rate)

        elif run_time < self.t_hold_end:
            return (self.peak_users, self.spawn_rate)

        elif run_time < self.t_cooldown_end:
            return (self.baseline_users, self.spawn_rate)

        else:
            return None
