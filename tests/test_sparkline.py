import re

from markupsafe import Markup

from apipulse.sparkline import latency_sparkline


def _polylines(svg: str) -> list[list[tuple[float, float]]]:
    lines = []
    for points in re.findall(r'<polyline[^>]*\spoints="([^"]*)"', svg):
        lines.append([tuple(map(float, p.split(","))) for p in points.split()])
    return lines


def test_returns_markup_svg_with_viewbox_role_and_aria_label():
    svg = latency_sparkline([100, 200, 150])
    assert isinstance(svg, Markup)
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert 'viewBox="0 0 320 48"' in svg
    assert 'role="img"' in svg
    assert 'aria-label="Median latency per day, 100 to 200 ms"' in svg


def test_custom_size_changes_the_view_box():
    assert 'viewBox="0 0 100 20"' in latency_sparkline([1, 2], width=100, height=20)


def test_one_polyline_for_a_continuous_series():
    lines = _polylines(latency_sparkline([10, 20, 30, 40]))
    assert len(lines) == 1 and len(lines[0]) == 4


def test_gaps_break_the_line_into_one_polyline_per_run():
    svg = latency_sparkline([10, 20, None, 30, 40, 50, None, None, 60, 70])
    assert [len(line) for line in _polylines(svg)] == [2, 3, 2]
    assert svg.count("<polyline") == 3


def test_x_follows_the_position_in_the_series_so_a_gap_keeps_its_width():
    lines = _polylines(latency_sparkline([10, None, None, 40, 50], width=100, height=40))
    # x is the centre of the point's cell: (i + 0.5) / n * width, n = 5
    assert [x for x, _ in lines[0]] == [10.0]  # a single-value run is still its own run
    assert [x for x, _ in lines[1]] == [70.0, 90.0]


def test_y_runs_from_zero_at_the_bottom_to_the_max_at_the_top():
    lines = _polylines(latency_sparkline([0, 50, 100], width=100, height=40))
    assert [y for _, y in lines[0]] == [40.0, 20.0, 0.0]


def test_coordinates_are_rounded_to_one_decimal():
    svg = latency_sparkline([1, 2, 3, 7], width=100, height=47)
    for point in re.findall(r'points="([^"]*)"', svg)[0].split():
        for number in point.split(","):
            assert re.fullmatch(r"-?\d+\.\d", number), number
    assert "12.5,40.3 37.5,33.6" in svg


def test_output_is_deterministic():
    values = [12, None, 80, 33, 90]
    assert latency_sparkline(values) == latency_sparkline(values)


def test_flat_series_draws_a_line_at_the_top_without_dividing_by_zero():
    lines = _polylines(latency_sparkline([80, 80, 80], width=100, height=40))
    assert [y for _, y in lines[0]] == [0.0, 0.0, 0.0]
    assert 'aria-label="Median latency per day, 80 to 80 ms"' in latency_sparkline([80, 80, 80])


def test_single_non_none_value_is_not_enough():
    assert latency_sparkline([None, 50, None]) == ""
    assert latency_sparkline([50]) == ""


def test_all_none_or_empty_is_not_enough():
    assert latency_sparkline([None, None, None]) == ""
    assert latency_sparkline([]) == ""


def test_maximum_of_zero_draws_on_the_baseline():
    svg = latency_sparkline([0, 0, 0], width=100, height=40)
    assert [y for _, y in _polylines(svg)[0]] == [40.0, 40.0, 40.0]
    assert 'aria-label="Median latency per day, 0 to 0 ms"' in svg


def test_two_points_is_the_minimum_that_draws():
    assert "<polyline" in latency_sparkline([None, 5, None, 9])


def test_x_is_the_centre_of_the_cell_so_it_lines_up_with_a_strip_of_n_cells():
    n, width = 31, 320
    svg = latency_sparkline([10] * n, width=width)
    (line,) = _polylines(svg)
    assert line[0][0] == round(0.5 / n * width, 1) == 5.2
    assert line[-1][0] == round((n - 0.5) / n * width, 1) == 314.8


def test_svg_stretches_to_its_container_with_undistorted_strokes():
    svg = latency_sparkline([10, 20, 30])
    assert 'preserveAspectRatio="none"' in svg
    assert svg.count('vector-effect="non-scaling-stroke"') >= svg.count("<polyline") >= 1
    assert re.search(r'<polyline[^>]*vector-effect="non-scaling-stroke"', svg)


def test_a_faint_baseline_sits_at_y_equals_height():
    svg = latency_sparkline([10, 20, 30], width=100, height=40)
    base = re.search(r'<line[^>]*class="base"[^>]*>', svg)[0]
    assert 'x1="0"' in base and 'x2="100"' in base
    assert 'y1="40"' in base and 'y2="40"' in base


def test_an_isolated_point_is_a_short_horizontal_segment_not_a_circle():
    svg = latency_sparkline([10, None, 30, 40], width=100, height=40)
    assert "<circle" not in svg
    segment = re.search(r'<line[^>]*class="dot"[^>]*>', svg)[0]
    assert 'x1="11.0"' in segment and 'x2="14.0"' in segment  # centre 12.5 +- 1.5
    assert 'y1="30.0"' in segment and 'y2="30.0"' in segment
