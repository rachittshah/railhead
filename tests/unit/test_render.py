"""Network map: the routes come from the vehicles' own orders; tiles map onto the engine minimap."""

from railhead.render import pixel, routes


def test_pixel_mirrors_x_like_the_engine_minimap():
    # screenshot.cpp MinimapScreenCallback: col = SizeX - 1 - x, row = y
    assert pixel(0, 0, 512) == (511, 0)
    assert pixel(28, 389, 512) == (483, 389)


def test_routes_join_consecutive_station_orders_cyclically_and_count_vehicles():
    orders = {
        1: ("air", [{"kind": "station", "station": 0}, {"kind": "station", "station": 1}]),
        2: ("air", [{"kind": "station", "station": 1}, {"kind": "station", "station": 0}]),  # same pair, reversed
        3: ("road", [{"kind": "station", "station": 2}, {"kind": "depot"}, {"kind": "station", "station": 3},
                     {"kind": "station", "station": 4}]),
        4: ("road", [{"kind": "station", "station": 5}]),  # one stop: no route
    }
    r = routes(orders)
    assert r[("air", 0, 1)] == 2
    assert r[("road", 2, 3)] == 1 and r[("road", 3, 4)] == 1 and r[("road", 2, 4)] == 1  # 4 -> 2 closes the loop
    assert not any(k[1] == 5 or k[2] == 5 for k in r)
