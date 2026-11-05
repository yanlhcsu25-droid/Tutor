"""Read-only inventory for Teaching Quality Dev paper fixtures.

Never loads blind cases, launches the agent, or mutates the source database.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path

CANONICAL_TYPES = ("选择题", "填空题", "计算题", "证明题")
DEV_PAPER_IDS = (
    "TQ-PQ-001", "TQ-PQ-002", "TQ-PQ-003", "TQ-PQ-010", "TQ-PQ-011",
    "TQ-PQ-012", "TQ-PQ-016", "TQ-PQ-017", "TQ-PQ-020",
)


def get_columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in db.execute(f'PRAGMA table_info("{table}")')}


def scalar(db: sqlite3.Connection, sql: str, args: tuple = ()) -> int:
    return int(db.execute(sql, args).fetchone()[0])


def check_preflight(db_path: Path) -> dict:
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    with sqlite3.connect(db_path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        db.row_factory = sqlite3.Row
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"question", "curriculum_node", "question_profile", "question_knowledge_link", "knowledge_node"}
        missing = sorted(required - tables)
        if missing:
            raise ValueError('Missing required tables: ' + ', '.join(missing))
        qcols = get_columns(db, 'question')
        needcols = {"id", "curriculum_chapter_id", "question_type", "is_active", "review_status"}
        missing = sorted(needcols - qcols)
        if missing:
            raise ValueError('Missing required question columns: ' + ', '.join(missing))
        total = scalar(db, 'SELECT COUNT(*) FROM question')
        state_counts = [dict(r) for r in db.execute(
            'SELECT review_status, is_active, question_type, COUNT(*) AS n '
            'FROM question GROUP BY review_status, is_active, question_type '
            'ORDER BY review_status, is_active, question_type'
        )]
        approved_filter = "q.is_active = 1 AND q.review_status = 'approved' AND q.question_type IN ('选择题','填空题','计算题','证明题')"
        eligible_count = scalar(db, f'SELECT COUNT(*) FROM question q WHERE {approved_filter}')
        chapter_rows = [dict(r) for r in db.execute(
            'SELECT COALESCE(c.title, "<未关联章节>") chapter, q.question_type, COUNT(*) n '
            'FROM question q LEFT JOIN curriculum_node c ON c.id=q.curriculum_chapter_id '
            f'WHERE {approved_filter} GROUP BY chapter, q.question_type ORDER BY chapter, q.question_type'
        )]
        chapter_types: dict[str, dict[str, int]] = {}
        for r in chapter_rows:
            chapter_types.setdefault(r['chapter'], {})[r['question_type']] = r['n']
        chapter_totals = {c: sum(t.values()) for c, t in chapter_types.items()}
        named = {}
        for prefix in ('第一章', '第二章', '第三章', '第四章'):
            names = [n for n in chapter_types if n.startswith(prefix)]
            named[prefix] = {
                'total': sum(chapter_totals[n] for n in names),
                'types': dict(Counter({typ: sum(chapter_types[n].get(typ, 0) for n in names) for typ in CANONICAL_TYPES})),
                'matched_chapter_titles': names,
            }
        available_profiles = scalar(db, f'SELECT COUNT(DISTINCT p.question_id) FROM question_profile p JOIN question q ON q.id=p.question_id WHERE {approved_filter}')
        available_links = scalar(db, f'SELECT COUNT(DISTINCT l.question_id) FROM question_knowledge_link l JOIN question q ON q.id=l.question_id WHERE {approved_filter}')
        topic_counts: dict[str, int] = {}
        for term in ('极限', '高阶导数', '洛必达'):
            topic_counts[term] = scalar(db,
                f'SELECT COUNT(DISTINCT q.id) FROM question q JOIN question_knowledge_link l ON l.question_id=q.id JOIN knowledge_node k ON k.id=l.knowledge_node_id WHERE {approved_filter} AND k.name LIKE ?',
                ('%' + term + '%',))
        profiles_by_difficulty = [dict(r) for r in db.execute(
            'SELECT p.difficulty, COUNT(DISTINCT q.id) n FROM question q '
            'JOIN question_profile p ON p.question_id=q.id '
            'AND p.profile_version=(SELECT MAX(p2.profile_version) FROM question_profile p2 WHERE p2.question_id=q.id) '
            f'WHERE {approved_filter} GROUP BY p.difficulty ORDER BY p.difficulty'
        )]
        c1, c2, c3, c4 = (named[x]['total'] for x in ('第一章','第二章','第三章','第四章'))
        def row(case, status, rationale):
            return {'case_id': case, 'preflight': status, 'reason': rationale}
        p2types = named['第三章']['types']
        paper_cases = [
            row('TQ-PQ-001', 'SUPPLY_MINIMUM_MET' if c1 >= 6 else 'SUPPLY_INSUFFICIENT', f'第一章可用题数 {c1}，最低需要6；未验证题目多样性'),
            row('TQ-PQ-002', 'CONTRACT_CONFLICT',
                '冻结用例要求第三章选择4/填空4/计算2/解答2/证明2；当前题型契约不支持解答题，规范化为unknown。'
                f'第三章可用题型分布={p2types}。需要单独裁定，不能以补题自动解决'),
            row('TQ-PQ-003', 'SUPPLY_MINIMUM_MET' if all((c1,c2,c3,c4)) else 'SUPPLY_INSUFFICIENT',
                f'第一/二/三/四章可用题数={c1}/{c2}/{c3}/{c4}；还需确认默认蓝图与封闭章范围'),
            row('TQ-PQ-010', 'NEEDS_FIXTURE_REVIEW',
                f'带“极限”知识关联题数={topic_counts["极限"]}；必须人工审查解题方法是否依赖洛必达法则'),
            row('TQ-PQ-011', 'NEEDS_FIXED_GROUP_MAPPING',
                '用例要求Q01/Q07/Q12与Q02/Q09相似组；数据库UUID与这些逻辑ID之间尚无受控映射，需人工确证相似性'),
            row('TQ-PQ-012', 'NEEDS_METHOD_REVIEW',
                f'带“极限”知识关联题数={topic_counts["极限"]}；须确认至少3道有不同解题方法'),
            row('TQ-PQ-016', 'NEEDS_SCORE_SHARE_REVIEW',
                f'带“高阶导数”知识关联题数={topic_counts["高阶导数"]}；需要验证题目分值占比≥60%和题库可供给量'),
            row('TQ-PQ-017', 'SUPPLY_MINIMUM_MET' if all((c1,c2,c3)) else 'SUPPLY_INSUFFICIENT',
                f'第一/二/三章可用题数={c1}/{c2}/{c3}；仍需考查难度梯度与题型平衡'),
            row('TQ-PQ-020', 'SUPPLY_MINIMUM_MET' if all((c1,c2,c3)) else 'SUPPLY_INSUFFICIENT',
                f'第一/二/三章可用题数={c1}/{c2}/{c3}；仍需考查合理比例和内容质量'),
        ]
        return {
            'kind': 'teaching_quality_dev_fixture_readonly_preflight',
            'official_benchmark': False,
            'model_calls': 0,
            'blind_executed': False,
            'data_source': 'existing read-only SQLite question bank',
            'counts': {
                'question_total': total,
                'approved_active_supported_type': eligible_count,
                'eligible_with_any_question_profile': available_profiles,
                'eligible_with_any_knowledge_link': available_links,
            },
            'question_state_counts': state_counts,
            'chapter_type_counts': chapter_types,
            'chapter_prefix_summary': named,
            'topic_link_counts_approximate': topic_counts,
            'latest_profile_difficulty_counts': profiles_by_difficulty,
            'dev_paper_cases': paper_cases,
            'limitations': [
                '数据库题数不是合格Fixture数量；未审查题干正确性、知识链接准确性和多样性',
                '本脚本不制造题目、不改变任何数据库、不执行Agent/Judge',
                '仅做最低供给检查；缺少受控逻辑ID/人工相似分组的用例仍不能正式跑分',
                '题型契约冲突属于评测定义问题，不应通过修改冻结Oracle或隐式映射来掩盖',
            ],
        }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('--db', default='calculus_agent.db')
    p.add_argument('--output', default='evaluation/artifacts/reports/teaching_quality/runs/dev_fixture_preflight.json')
    args = p.parse_args()
    info = check_preflight(Path(args.db))
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
    print('REPORT=' + str(out))
    print('QUESTION_TOTAL=' + str(info['counts']['question_total']))
    print('ELIGIBLE=' + str(info['counts']['approved_active_supported_type']))
    print('PROFILED=' + str(info['counts']['eligible_with_any_question_profile']))
    print('KNOWLEDGE_LINKED=' + str(info['counts']['eligible_with_any_knowledge_link']))
    for chapter, entry in info['chapter_prefix_summary'].items():
        print(chapter, entry['total'], entry['types'])
    print('DEV_PAPER_CASES=9')
    for result in info['dev_paper_cases']:
        print(result['case_id'], result['preflight'], result['reason'])
    print('MODEL_CALLS=0 BLIND_EXECUTED=False DB_READ_ONLY=True')


if __name__ == '__main__':
    main()
