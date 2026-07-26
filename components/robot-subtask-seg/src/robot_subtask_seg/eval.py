from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Interval:
    start_sec: float
    end_sec: float


def interval_iou(a: Interval, b: Interval) -> float:
    intersection = max(0.0, min(a.end_sec, b.end_sec) - max(a.start_sec, b.start_sec))
    union = max(a.end_sec, b.end_sec) - min(a.start_sec, b.start_sec)
    if union <= 0.0:
        return 0.0
    return intersection / union


def segment_f1(
    predicted: list[Interval],
    gold: list[Interval],
    *,
    iou_threshold: float = 0.75,
) -> dict[str, float]:
    matched_gold: set[int] = set()
    true_positive = 0
    for pred in predicted:
        best_index = None
        best_iou = 0.0
        for index, target in enumerate(gold):
            if index in matched_gold:
                continue
            score = interval_iou(pred, target)
            if score > best_iou:
                best_iou = score
                best_index = index
        if best_index is not None and best_iou >= iou_threshold:
            matched_gold.add(best_index)
            true_positive += 1
    precision = true_positive / len(predicted) if predicted else 0.0
    recall = true_positive / len(gold) if gold else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "true_positive": float(true_positive),
        "predicted": float(len(predicted)),
        "gold": float(len(gold)),
    }

