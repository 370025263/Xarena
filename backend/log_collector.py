# Optional log collector hook (no-op in local deployment).
# The backend persists evaluator logs directly via the local-executor path.
def collect(*args, **kwargs):
    return None
