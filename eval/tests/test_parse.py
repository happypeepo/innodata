import pytest

from eval.parse import extract_json, parse

W, H = 1024, 768


def test_point_converts_yx_0_1000_to_xy_pixels():
    assert parse('{"point": [500, 250]}', "point", W, H) == {"point": [256.0, 384.0]}


def test_bbox_converts_order_and_scale():
    assert parse('{"bbox": [250, 500, 750, 1000]}', "bbox", W, H) == {"bbox": [512.0, 192.0, 1024.0, 576.0]}


@pytest.mark.parametrize("text", [
    '```json\n{"point": [500, 250]}\n```',
    'Sure! The cylinder is here: {"point": [500, 250]}. Hope that helps.',
    '[{"point": [500, 250], "label": "red cylinder"}]',          # ER list-of-points style
    '{"Point": ["500", "250"]}',                                  # key case, numeric strings
    'first guess {"point": [1, 1]} final answer {"point": [500, 250]}',  # last one wins
])
def test_lenient_wrapping(text):
    assert parse(text, "point", W, H) == {"point": [256.0, 384.0]}


def test_inner_list_never_beats_outer_object():
    assert extract_json('{"point": [1, 2]}') == {"point": [1, 2]}


@pytest.mark.parametrize("text,atype", [
    ('{"bbox": [1, 2, 3, 4]}', "point"),           # wrong key
    ('{"point": [1, 2, 3]}', "point"),            # wrong length
    ('{"point": [true, 2]}', "point"),
    ("the red one", "choice"),                    # no JSON: no guessing from prose
    ('{"count": 2.5}', "count"),
    ('{"count": -1}', "count"),
    ('{"bool": "maybe"}', "bool"),
    ('{"choice": "AB"}', "choice"),
    ('{"sequence": ["A", 3]}', "sequence"),
    ("", "count"),
])
def test_strict_meaning(text, atype):
    assert parse(text, atype, W, H) is None


def test_off_image_point_is_kept_not_clamped():
    assert parse('{"point": [1200, -10]}', "point", W, H) == {"point": [-10.2, 921.6]}


@pytest.mark.parametrize("text,atype,want", [
    ('{"count": "3"}', "count", 3), ('{"count": 3.0}', "count", 3),
    ('{"bool": "Yes"}', "bool", True), ('{"bool": false}', "bool", False),
    ('{"choice": "b"}', "choice", "B"), ('{"choice": "A) behind"}', "choice", "A"),
    ('{"sequence": ["b", "C", "a"]}', "sequence", ["B", "C", "A"]), ('{"set": []}', "set", []),
])
def test_unambiguous_coercions(text, atype, want):
    assert parse(text, atype, W, H) == {atype: want}
