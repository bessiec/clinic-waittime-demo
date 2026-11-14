"""Simulated time: integer minutes since midnight, one tick per minute."""


def parse(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def fmt(t: int) -> str:
    t %= 24 * 60
    return f"{t // 60:02d}:{t % 60:02d}"


def fmt_12h(t: int) -> str:
    t %= 24 * 60
    h, m = t // 60, t % 60
    suffix = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d} {suffix}"
