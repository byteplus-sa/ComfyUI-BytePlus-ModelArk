FAILED_TASK_STATUSES = ("failed", "cancelled", "expired")


def poll_error_is_permanent(error):
    """A 4xx other than 408/409/429 (bad key, unknown or expired task) will not go away."""
    status = getattr(error, "status_code", None)
    return isinstance(status, int) and 400 <= status < 500 and status not in (408, 409, 429)


def poll_failure_is_final(error, failures_in_a_row, max_failures):
    """Whether a failed status poll ends the wait: a permanent error, or too many transient ones in a row."""
    return poll_error_is_permanent(error) or failures_in_a_row >= max_failures
