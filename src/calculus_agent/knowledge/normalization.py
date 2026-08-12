import re


def normalize_name(value: str) -> str:
    value = value.lower().strip()
    value = value.replace("l'hôpital", "洛必达").replace("l’hôpital", "洛必达")
    return re.sub(r"[\s·、，。,.()（）'’\-]", "", value)


def terms(value: str) -> set[str]:
    compact = normalize_name(value)
    english = set(re.findall(r"[a-z]{2,}", value.lower()))
    chinese = set(re.findall(r"[\u4e00-\u9fff]{2,}", value))
    ngrams = {compact[i : i + 2] for i in range(max(0, len(compact) - 1))}
    return english | chinese | ngrams
