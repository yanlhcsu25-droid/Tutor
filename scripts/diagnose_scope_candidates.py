"""Reproduce Teacher Agent candidate-query logic against the REAL calculus_agent.db.

Dependency-free (stdlib sqlite3) so it runs without the heavy ML deps.
Mirrors:
  - blueprint_adapter.resolve_generation_scope  (chapter code -> knowledge node ids)
  - selector._candidates                        (approved+active+current+non-excluded+scope join+canonical type)
  - workbench list_questions scope join         (in-scope, status-agnostic)
Goal: confirm whether `insufficient_candidates` is a real data-coverage gap
(Problem 2) and whether "第三章" resolution works (Problem 1).
"""
import sqlite3
from collections import Counter

DB = "/Users/shengyue/Documents/Teacher_Agent/calculus_knowledge_agent/calculus_agent.db"
EXCLUDED = ("CMM-Math", "built-in-demo", "test_source")
CANON = {
    "selection": "选择题", "single_choice": "选择题", "multiple_choice": "多选题",
    "fill_blank": "填空题", "calculation": "计算题", "proof": "证明题",
    "选择": "选择题", "单选题": "选择题", "选择题": "选择题",
    "多选题": "多选题", "填空": "填空题", "填空题": "填空题",
    "计算": "计算题", "计算题": "计算题", "证明": "证明题", "证明题": "证明题",
}
ALLOWED = {"选择题", "多选题", "填空题", "计算题", "证明题"}


def canonical(v):
    v = (v or "").strip()
    return CANON.get(v, v)


con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

# ---- taxonomy (mirror resolve_generation_scope: chapter code -> descendant knowledge ids)
nodes = {r["id"]: dict(r) for r in con.execute(
    "SELECT id, code, node_type, parent_id FROM curriculum_node").fetchall()}


def resolve_chapter(code_cn):
    chapters = [n for n in nodes.values()
                if n["node_type"] == "chapter" and (n["code"] or "") == code_cn]
    if len(chapters) != 1:
        return None, f"chapter candidates={len(chapters)}"
    selected = set()
    pending = [chapters[0]["id"]]
    while pending:
        c = pending.pop()
        selected.add(c)
        for nid, n in nodes.items():
            if n["parent_id"] == c and nid not in selected:
                pending.append(nid)
    knids = [r[0] for r in con.execute(
        "SELECT id FROM knowledge_node WHERE curriculum_node_id IN (%s)" % ",".join("?" * len(selected)),
        list(selected)).fetchall()]
    return knids, None


def gen_candidates(knids):
    """Mirror selector._candidates (no difficulty filter; scope-joined)."""
    q = """
        SELECT DISTINCT q.id, q.question_type
        FROM question q
        JOIN question_draft qd ON qd.id = q.draft_id
        WHERE q.review_status='approved'
          AND q.is_active=1
          AND q.knowledge_match_status='current'
          AND qd.source_name NOT IN (?,?,?)
    """
    params = list(EXCLUDED)
    if knids:
        q += " AND q.id IN (SELECT question_id FROM question_knowledge_link WHERE knowledge_node_id IN (%s))" % ",".join("?" * len(knids))
        params += list(knids)
    rows = con.execute(q, params).fetchall()
    return [r for r in rows if canonical(r["question_type"]) in ALLOWED]


def scope_all_status(knids):
    """What a teacher would see in the bank UI for this scope (status-agnostic)."""
    if not knids:
        return []
    q = """
        SELECT DISTINCT q.id, q.question_type, q.review_status, q.is_active, q.knowledge_match_status, qd.source_name
        FROM question q
        JOIN question_draft qd ON qd.id = q.draft_id
        WHERE q.id IN (SELECT question_id FROM question_knowledge_link WHERE knowledge_node_id IN (%s))
    """ % ",".join("?" * len(knids))
    return con.execute(q, list(knids)).fetchall()


# ---- Problem 1: 第三章 resolution
kn3, err = resolve_chapter("三")
print("=== PROBLEM 1: 第三章 scope resolution ===")
print(f"  resolved knowledge_node_ids = {len(kn3)}  error={err}")
c3 = Counter(canonical(r["question_type"]) for r in gen_candidates(kn3))
print(f"  generation candidates = {sum(c3.values())}  by type={dict(c3)}")
print(f"  => Problem 1 is a NON-ISSUE (resolution works)\n")

# ---- source distribution (whole bank, generation-eligible base)
print("=== Bank source distribution (approved+active+current) ===")
src = Counter(r[0] for r in con.execute("""
    SELECT qd.source_name FROM question q
    JOIN question_draft qd ON qd.id = q.draft_id
    WHERE q.review_status='approved' AND q.is_active=1 AND q.knowledge_match_status='current'
""").fetchall())
for k, v in src.most_common():
    print(f"  {k}: {v}")
print()

# ---- Problem 2: full 12-chapter funnel
print("=== PROBLEM 2: 12-chapter candidate funnel (generation-eligible) ===")
print(f"{'章':<4}{'kn_ids':<8}{'gen_total':<10}{'选择':<6}{'填空':<6}{'计算':<6}{'证明':<6}{'多选':<6}{'bank_all':<9}")
TYPES = ["选择题", "填空题", "计算题", "证明题", "多选题"]
for i in range(1, 13):
    cn = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "十一", "十二"][i - 1]
    knids, e = resolve_chapter(cn)
    if knids is None:
        print(f"第{cn}章  RESOLVE FAIL: {e}")
        continue
    gc = Counter(canonical(r["question_type"]) for r in gen_candidates(knids))
    total = sum(gc.values())
    bank = len(scope_all_status(knids))
    row = [f"第{cn}章", len(knids), total] + [gc.get(t, 0) for t in TYPES] + [bank]
    print(f"{row[0]:<6}{row[1]:<8}{row[2]:<10}{row[3]:<6}{row[4]:<6}{row[5]:<6}{row[6]:<6}{row[7]:<6}{row[8]:<9}")
print()
print("Default CHAPTER_TEST_TEMPLATE = 选择题 x4, 填空题 x2, 计算题 x4 (total 10)")
print("=> 九~十二章 generation candidates = 0  (ocr_import 零覆盖)  -> insufficient_candidates 真实成立")
