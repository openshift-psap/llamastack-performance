"""
Custom load shape - fully configurable via CUSTOM_STAGES environment variable.

Reads a JSON array of stages, each with:
  - duration:   cumulative seconds from test start when this stage ends
  - users:      number of concurrent users
  - spawn_rate: users spawned per second

Configuration via environment variables:
    CUSTOM_STAGES: JSON array of stage objects (required)

Example CUSTOM_STAGES:
    [
        {"duration": 60,  "users": 10,  "spawn_rate": 2},
        {"duration": 120, "users": 50,  "spawn_rate": 10},
        {"duration": 240, "users": 100, "spawn_rate": 10},
        {"duration": 300, "users": 20,  "spawn_rate": 5}
    ]

Usage in PipelineRun:
    params:
      - name: LOAD_SHAPE
        value: "custom"
      - name: CUSTOM_STAGES
        value: '[{"duration":60,"users":10,"spawn_rate":2},{"duration":120,"users":50,"spawn_rate":10}]'
"""
import os
import json
from locust import LoadTestShape


class CustomShape(LoadTestShape):
    """
    Fully configurable load shape via CUSTOM_STAGES JSON env var.
    Each stage defines a cumulative duration, user count, and spawn rate.
    """

    def __init__(self):
        super().__init__()
        stages_json = os.environ.get("CUSTOM_STAGES", "[]")
        try:
            self.stages = json.loads(stages_json)
        except json.JSONDecodeError as e:
            print(f"ERROR: Failed to parse CUSTOM_STAGES: {e}")
            print(f"  Value was: {stages_json[:200]}")
            self.stages = []

        if not self.stages:
            print("WARNING: CUSTOM_STAGES is empty or invalid. Test will stop immediately.")
        else:
            print(f"CustomShape loaded {len(self.stages)} stages:")
            for i, stage in enumerate(self.stages):
                print(f"  Stage {i+1}: until {stage['duration']}s → {stage['users']} users @ {stage['spawn_rate']}/s")

    def tick(self):
        run_time = self.get_run_time()

        for stage in self.stages:
            if run_time < stage["duration"]:
                return (stage["users"], stage["spawn_rate"])

        # Past all stages - stop
        return None
