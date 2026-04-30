from flask import abort, flash, redirect, render_template, request
from pathlib import Path


def _selected_instance_or_400():
    """Shared helper to get selected instance or abort."""
    from app import session, load_instances, get_instance_by_name
    instances = load_instances()
    selected_name = session.get("selected_instance")
    selected = get_instance_by_name(selected_name, instances)
    if not selected:
        abort(400, "No instance selected.")
    return selected


def _abort_404(message="Not found"):
    """Shared 404 helper."""
    abort(404, message)
