def fmt_ts(seconds: float) -> str:
    """Format seconds as mm:ss, or h:mm:ss for an hour or more."""
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def parse_ts(text: str) -> float:
    """Parse mm:ss or h:mm:ss into seconds."""
    parts = text.strip().split(":")
    if not 2 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"invalid timestamp: {text!r}")
    nums = [int(p) for p in parts]
    if any(n >= 60 for n in nums[1:]):
        raise ValueError(f"invalid timestamp: {text!r}")
    if len(nums) == 2:
        nums = [0, *nums]
    hours, minutes, secs = nums
    return float(hours * 3600 + minutes * 60 + secs)
