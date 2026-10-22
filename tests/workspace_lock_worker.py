"""Lightweight spawn target; avoids importing the entire API test module."""
from backend.security.workspace_lock import workspace_lock


def hold_workspace_lock(workspace, acquired, release):
    with workspace_lock(workspace, timeout=10):
        acquired.set()
        release.wait(20)
