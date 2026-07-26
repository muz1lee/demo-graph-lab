from robot_subtask_seg.eval import Interval, interval_iou, segment_f1


def test_interval_iou():
    assert round(interval_iou(Interval(0, 2), Interval(1, 3)), 3) == 0.333


def test_segment_f1():
    scores = segment_f1(
        [Interval(0, 1), Interval(2, 3)],
        [Interval(0, 1.1), Interval(4, 5)],
        iou_threshold=0.75,
    )
    assert scores["true_positive"] == 1.0
    assert scores["precision"] == 0.5
    assert scores["recall"] == 0.5
    assert scores["f1"] == 0.5

