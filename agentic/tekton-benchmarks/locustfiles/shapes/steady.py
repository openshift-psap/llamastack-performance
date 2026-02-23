"""
Steady load shape - maintains constant user count.
"""
import os
from locust import LoadTestShape


class SteadyShape(LoadTestShape):
    """
    Constant load shape - maintains steady user count for duration.
    Spawn rate defaults to user count so all users start immediately.
    
    Configuration via environment variables:
        USERS: Number of concurrent users (default: 10)
        SPAWN_RATE: Users to spawn per second (defaults to USERS)
        RUN_TIME_SECONDS: Test duration in seconds (default: 60)
    """
    
    def tick(self):
        """
        Called ~1/second. Return (users, spawn_rate) or None to stop.
        """
        users = int(os.environ.get("USERS", "10"))
        spawn_rate = users  # All users spawn instantly for flat load
        run_time = int(os.environ.get("RUN_TIME_SECONDS", "60"))
        
        run_time_elapsed = self.get_run_time()
        
        if run_time_elapsed > run_time:
            return None  # Stop the test
            
        return (users, spawn_rate)
