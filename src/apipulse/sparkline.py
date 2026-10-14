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
    cells = len(values)

    def point(index: int, value: int) -> tuple[float, float]:
        x = (index + 0.5) / cells * width  # the centre of the strip cell for this date
        y = height - (value / top * height if top else 0)
        return round(x, 1), round(y, 1)

    shapes = []
    for run in _runs(values):
        points = [point(index, value) for index, value in run]
        text = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        shapes.append(
            f'<polyline points="{text}" fill="none" stroke="currentColor" '
            'vector-effect="non-scaling-stroke"/>'
        )
        if len(points) == 1:
            # A circle would be squashed by the non-uniform scaling; a short segment with a
            # round cap stays a dot.
            x, y = points[0]
            shapes.append(
                f'<line class="dot" x1="{x - 1.5:.1f}" x2="{x + 1.5:.1f}" y1="{y:.1f}" '
                f'y2="{y:.1f}" stroke="currentColor" stroke-linecap="round" '
                'vector-effect="non-scaling-stroke"/>'
            )
    label = f"Median latency per day, {min(present)} to {top} ms"
    return Markup(
        f'<svg class="spark" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="{label}" preserveAspectRatio="none" '
        'overflow="visible">'
        f'<line class="base" x1="0" x2="{width}" y1="{height}" y2="{height}" '
        'stroke="currentColor" opacity=".25" vector-effect="non-scaling-stroke"/>'
        f"{''.join(shapes)}</svg>"
    )
