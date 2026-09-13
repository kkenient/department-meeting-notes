"""Metrics from human-verified item matches; no claim of automatic semantic grading."""

import unicodedata


def normalize(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKC", text).casefold()
                   if not c.isspace() and not unicodedata.category(c).startswith("P"))


def cer(reference: str, hypothesis: str) -> float | None:
    reference, hypothesis = normalize(reference), normalize(hypothesis)
    if not reference:
        return None
    previous = list(range(len(hypothesis) + 1))
    for i, left in enumerate(reference, 1):
        current = [i]
        for j, right in enumerate(hypothesis, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j-1] + (left != right)))
        previous = current
    return previous[-1] / len(reference)


def ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def score(gold: list[dict], predictions: list[dict], matches: list[tuple[str, str]],
          hallucinated_ids: list[str]) -> dict:
    """matches contain (gold_id, prediction_id), manually checked for meaning and qualifiers."""
    g, p = {x["id"]: x["category"] for x in gold}, {x["id"]: x["category"] for x in predictions}
    if len(g) != len(gold) or len(p) != len(predictions):
        raise ValueError("标注 ID 必须唯一")
    seen_g, seen_p = set(), set()
    for gid, pid in matches:
        if gid not in g or pid not in p or g[gid] != p[pid] or gid in seen_g or pid in seen_p:
            raise ValueError("匹配必须是一对一、同类别且引用有效 ID")
        seen_g.add(gid)
        seen_p.add(pid)
    hallucinations = set(hallucinated_ids)
    if not hallucinations <= p.keys() or hallucinations & seen_p:
        raise ValueError("幻觉标注必须引用未正确匹配的预测条目")
    by_category = {}
    for category in sorted(set(g.values()) | set(p.values())):
        correct = sum(g[gid] == category for gid in seen_g)
        by_category[category] = {
            "correct": correct, "gold": list(g.values()).count(category),
            "predicted": list(p.values()).count(category),
            "precision": ratio(correct, list(p.values()).count(category)),
            "recall": ratio(correct, list(g.values()).count(category)),
        }
    return {"precision": ratio(len(matches), len(p)), "recall": ratio(len(matches), len(g)),
            "hallucination_rate": ratio(len(hallucinations), len(p)), "by_category": by_category,
            "missed_ids": sorted(g.keys() - seen_g), "unmatched_prediction_ids": sorted(p.keys() - seen_p)}
