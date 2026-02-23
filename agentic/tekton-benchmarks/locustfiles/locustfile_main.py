"""
Main entry point for Locust tests.
All configuration is done via environment variables.

Usage:
    locust -f locustfile_main.py --host http://llamastack:8321 --headless
    
Environment Variables:
    USERS              - Number of concurrent users (default: 10)
    SPAWN_RATE         - Users to spawn per second (default: 1)  
    RUN_TIME_SECONDS   - Test duration in seconds (default: 60)
    MCP_SERVER         - MCP server URL (default: in-cluster)
    MODEL              - Model name (default: vllm-inference/llama-32-3b-instruct)
    PROMPT             - Test prompt (default: "What is Kubernetes?")
    LOAD_SHAPE         - Shape to use: steady (default: steady)
    MLFLOW_URL         - MLflow tracking server URL (optional)
    MLFLOW_EXPERIMENT  - MLflow experiment name (default: llamastack-benchmarks)
"""
import os
import sys

# Add current directory to path for imports
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

# Import user classes - Locust auto-discovers HttpUser subclasses
from locustfile_users import ResponsesMCPUser

# Import hooks - registers event listeners as side effect
from hooks import mlflow_hooks

# Import shape based on LOAD_SHAPE env var
shape_name = os.environ.get("LOAD_SHAPE", "steady")

if shape_name == "steady":
    from shapes.steady import SteadyShape
elif shape_name == "spike":
    from shapes.spike import SpikeShape
elif shape_name == "realistic":
    from shapes.realistic import RealisticShape
elif shape_name == "custom":
    from shapes.custom import CustomShape
else:
    print(f"WARNING: Unknown shape '{shape_name}', using steady")
    from shapes.steady import SteadyShape
