"""A pure inline-SVG latency sparkline; the same input always gives the same bytes."""

from collections.abc import Sequence

from markupsafe import Markup


def _runs(values: Sequence[int | None]) -> list[list[tuple[int, int]]]:
    """Split the series into runs of consecutive non-None values as (index, value) pairs."""
    runs: list[list[tuple[int, int]]] = []
    current: list[tuple[int, int]] = []
    for index, value in enumerate(values):
        if value is None:
            if current:
                runs.append(current)
            current = []
        else:
            current.append((index, value))
    if current:
        runs.append(current)
    return runs


def latency_sparkline(
    values: Sequence[int | None], *, width: int = 320, height: int = 48
) -> Markup:
    """Draw one value per day, oldest first, as lines broken at days without a value.

    The y axis runs from 0 (bottom) to the series maximum (top). Returns an empty string when
    fewer than two days have a value, because one point is not a trend.
    """
    present = [v for v in values if v is not None]
    if len(present) < 2:
        return Markup("")
    top = max(present)
    steps = max(len(values) - 1, 1)

    def point(index: int, value: int) -> tuple[float, float]:
        x = index / steps * width
        y = height - (value / top * height if top else 0)
        return round(x, 1), round(y, 1)

    shapes = []
    for run in _runs(values):
        points = [point(index, value) for index, value in run]
        text = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        shapes.append(f'<polyline points="{text}" fill="none" stroke="currentColor"/>')
        if len(points) == 1:
            x, y = points[0]
            shapes.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="1.5" fill="currentColor"/>')
    label = f"Median latency per day, {min(present)} to {top} ms"
    return Markup(
        f'<svg class="spark" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="{label}" overflow="visible">'
        f"{''.join(shapes)}</svg>"
    )
