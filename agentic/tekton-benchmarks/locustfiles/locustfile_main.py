"""
Main entry point for Locust tests.
All configuration is done via environment variables.

Usage:
    locust -f locustfile_main.py --host http://llamastack:8321 --headless

Environment Variables:
    USER_CLASS         - User class: ResponsesMCPUser | ResponsesSimpleUser | ChatCompletionsUser
    USERS              - Number of concurrent users (default: 10)
    SPAWN_RATE         - Users to spawn per second (default: 1)
    RUN_TIME_SECONDS   - Test duration in seconds (default: 60)
    MCP_SERVER         - MCP server URL (for ResponsesMCPUser)
    MODEL              - Model name (default: vllm-inference/llama-32-3b-instruct)
    PROMPT             - Test prompt
    LOAD_SHAPE         - Shape: steady | spike | realistic | custom
    MLFLOW_URL         - MLflow tracking server URL (optional)
    MLFLOW_EXPERIMENT  - MLflow experiment name (default: llamastack-benchmarks)
"""
import os
import sys

# Add current directory to path for imports
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

# Import and activate selected user class
# Use module import (not from...import) to avoid Locust seeing duplicates
import locustfile_users

user_class_name = os.environ.get("USER_CLASS", "ResponsesMCPUser")

user_class_map = {
    "ResponsesMCPUser": locustfile_users.ResponsesMCPUser,
    "ResponsesSimpleUser": locustfile_users.ResponsesSimpleUser,
    "ChatCompletionsUser": locustfile_users.ChatCompletionsUser,
}

selected_class = user_class_map.get(user_class_name)
if selected_class:
    selected_class.abstract = False
    print(f"Active user class: {user_class_name}")
else:
    print(f"WARNING: Unknown USER_CLASS '{user_class_name}', defaulting to ResponsesMCPUser")
    locustfile_users.ResponsesMCPUser.abstract = False

# Import hooks - registers event listeners as side effect
from hooks import metrics_collector

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
