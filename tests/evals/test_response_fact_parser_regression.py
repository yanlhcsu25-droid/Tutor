from __future__ import annotations

import unittest
from collections import Counter

from tests.evals.graders.response_fact_parser import (
    extract_response_facts,
    read_only_claim_errors,
    whole_paper_counts,
)

TYPE_COUNTS = Counter({"选择题": 4, "填空题": 2, "计算题": 3, "证明题": 1})


class ResponseFactsRegressionTests(unittest.TestCase):
    def test_section_counts_do_not_become_whole_paper_count(self):
        s = "总题数：10题，总分：100分。选择题（共4题），填空题（共2题），计算题（共3题），证明题（共1题）"
        self.assertEqual(extract_response_facts(s), {
            "question_count": 10, "total_score": 100, "question_type_counts": dict(TYPE_COUNTS)
        })
        self.assertEqual(read_only_claim_errors(s, expected_count=10, expected_score=100, persisted_type_counts=TYPE_COUNTS), [])

    def test_numeric_type_before_name(self):
        s = "当前卷里共有 **1** 道证明题。整卷共10道题，包含4道选择题、2道填空题、3道计算题和1道证明题。"
        self.assertEqual(extract_response_facts(s)["question_type_counts"], dict(TYPE_COUNTS))
        self.assertEqual(whole_paper_counts(s), [10])

    def test_markdown_and_numbering(self):
        s = "**总题数**：10题\n- **总分**：100分\n1. **选择题** (共4题)\n2. **填空题** (共2题)\n3. **计算题** (共3题)\n4. **证明题** (共1题)"
        self.assertEqual(extract_response_facts(s)["question_count"], 10)
        self.assertEqual(extract_response_facts(s)["total_score"], 100)
        self.assertEqual(extract_response_facts(s)["question_type_counts"], dict(TYPE_COUNTS))

    def test_universal_difficulty(self):
        s = "全卷共10道题目，所有题目的难度系数均为 3。建议今后增加难度 4 的题。"
        self.assertEqual(extract_response_facts(s)["difficulty_counts"], {"3": 10})

    def test_type_percentage_is_not_a_count(self):
        s = "整卷共10题，选择题（共4题）。计算题占比最大（30%），填空题（20%）。"
        self.assertEqual(extract_response_facts(s)["question_type_counts"], {"选择题": 4})

    def test_type_count_not_total_without_global(self):
        self.assertNotIn("question_count", extract_response_facts("选择题（共4题）"))

    def test_wrong_total_still_fails(self):
        errs = read_only_claim_errors("整卷共9题，总分100分", expected_count=10, expected_score=100, persisted_type_counts=TYPE_COUNTS)
        self.assertTrue(any("question count=9" in s for s in errs))

    def test_wrong_score_still_fails(self):
        errs = read_only_claim_errors("总题数10题，总分90分", expected_count=10, expected_score=100, persisted_type_counts=TYPE_COUNTS)
        self.assertTrue(any("total score=90" in s for s in errs))

    def test_wrong_type_still_fails(self):
        errs = read_only_claim_errors("10道题，其中5道选择题", expected_count=10, expected_score=100, persisted_type_counts=TYPE_COUNTS)
        self.assertTrue(any("选择题 count=5" in s for s in errs))

    def test_conflicting_global_claim_still_fails(self):
        errs = read_only_claim_errors("总题数10题，但是整卷有9题", expected_count=10, expected_score=100, persisted_type_counts=TYPE_COUNTS)
        self.assertTrue(any("question count=9" in s for s in errs))

    def test_type_count_conflict_still_fails(self):
        errs = read_only_claim_errors("选择题共4题，但选择题还有5题", expected_count=10, expected_score=100, persisted_type_counts=TYPE_COUNTS)
        self.assertTrue(any("选择题 count=5" in s for s in errs))


if __name__ == "__main__":
    unittest.main()
