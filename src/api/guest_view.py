"""Route-layer guest view: drop operator-only keys from payloads served to non-admins (on top of src/auth/rounding)."""

from typing import Any, Callable

# Keys a guest must never receive from station/status style payloads (secrets and operator-only settings)
GUEST_STRIP_KEYS = ("google_maps_key", "fr24_token", "fr24_password", "fr24_email", "remote_solver_url")


def strip_keys(obj: Any, keys=GUEST_STRIP_KEYS) -> Any:
    if isinstance(obj, dict):
        return {k: strip_keys(v, keys) for k, v in obj.items() if k not in keys}
    if isinstance(obj, list):
        return [strip_keys(v, keys) for v in obj]
    return obj


def guest_view(view: Callable[[Any], Any]) -> Callable[[Any], Any]:
    """Wrap a rounding view so the stripped keys are removed as well (use for guests only)."""
    return lambda payload: strip_keys(view(payload))
