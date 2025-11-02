TICKS_PER_SECOND = 254_016_000_000  # Premiere internal timing base

def ticks_to_tc_24fps(ticks: int) -> str:
    """Convert ticks to HH:MM:SS with 24 fps rounding (<=12 keep, >=13 round up)."""
    seconds = ticks / TICKS_PER_SECOND
    whole = int(seconds)
    frac = seconds - whole
    frames = round(frac * 24)
    if frames >= 13:
        whole += 1
    h = whole // 3600
    m = (whole % 3600) // 60
    s = whole % 60
    return f"{h:02d}:{m:02d}:{s:02d}"
