from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tutor_dev_task_completion_audit as a


class ReadOnlyAuditTests(unittest.TestCase):
    def case(self, id_, setup=1):
        return {'id': id_, 'split': 'dev', 'category':'pending', 'subtype':'confirm',
                'turns':[{'user':'确认'}], 'setup':{'pending_generation': setup},
                'expected':{'status':'completed'}, 'graders':[{'type':'state'}],
                'review':{'status':'pending'}}

    def test_text_dedup_keeps_different_fixture_contracts(self):
        m, s = a.build_manifest({'cases':[self.case('EVAL-001'), self.case('EVAL-002'), self.case('EVAL-003', 2)]})
        self.assertEqual(s['dev_unique_prompt_sequences'], 1)
        self.assertEqual(s['dev_distinct_contracts'], 2)
        self.assertEqual(s['dev_strict_duplicates'], 1)
        self.assertEqual({x['canonical_id'] for x in m}, {'EVAL-001','EVAL-003'})

    def test_contract_missing_critical_grader_not_pass(self):
        rec={'passed':True,'graders':[{'grader':'state','passed':True}]}
        self.assertEqual(a.get_graded_result(rec), 'unverifiable')

    def test_full_grader_pass(self):
        rec={'passed':True,'graders':[{'grader':g,'passed':True} for g in a.CORE_GRADERS]}
        self.assertEqual(a.get_graded_result(rec), 'contract_pass')

    def test_failed_grader_overrides_report(self):
        rec={'passed':False,'graders':[{'grader':g,'passed':True} for g in a.CORE_GRADERS]}
        self.assertEqual(a.get_graded_result(rec), 'contract_fail')

    def test_conflicts_are_not_silently_merged(self):
        with TemporaryDirectory() as td:
            p=Path(td)
            def record(passed): return {'case_id':'EVAL-001','passed':passed,
                'graders':[{'grader':g,'passed':passed} for g in a.CORE_GRADERS]}
            (p/'a-first-api.json').write_text(json.dumps({'results':[record(True)]}))
            (p/'b-first-api.json').write_text(json.dumps({'results':[record(False)]}))
            chosen, inv = a.find_first_api(p,{'EVAL-001'})
            self.assertEqual(chosen['EVAL-001']['status'],'conflict')
            self.assertEqual(len(inv['case_id_conflicts']),1)

    def test_no_report_does_not_invent_success(self):
        m, _=a.build_manifest({'cases':[self.case('EVAL-001')]})
        rows,s=a.group_first_api(m,{})
        self.assertEqual(s['graded_contract_count'],0)
        self.assertIsNone(s['contract_pass_rate_on_graded_subset'])
        self.assertEqual(rows[0]['status'],'not_evidenced')

if __name__=='__main__': unittest.main()
