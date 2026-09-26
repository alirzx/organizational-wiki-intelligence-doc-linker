from app.core.ids import stable_point_id


def test_stable_point_id():
    assert stable_point_id("a", 1, "b") == stable_point_id("a", 1, "b")
    assert stable_point_id("a", 1, "b") != stable_point_id("a", 2, "b")
