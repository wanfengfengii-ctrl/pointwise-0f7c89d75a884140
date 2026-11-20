"""End-to-end checks for the documented command-line entries.

These exercise the same `resample_fixture.main` and `shift_example.demo`
functions that the runtime manifest invokes, so a regression in either entry
fails `check_project.py` too.
"""
import contextlib
import io
import json
import unittest

import resample_fixture
import shift_example


class FixtureScriptTests(unittest.TestCase):
    def test_fixture_script_covers_all_cases(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            resample_fixture.main()
        lines = [json.loads(line) for line in output.getvalue().splitlines() if line.strip()]
        self.assertEqual([row['case'] for row in lines], list(resample_fixture.CASES))
        for row in lines:
            case = resample_fixture.CASES[row['case']]
            self.assertEqual(row['plan']['status'], case['status'])
            self.assertEqual(row['plan']['finish'], case['finish'])


class DemoEntryTests(unittest.TestCase):
    def test_demo_signs_and_reads_back_resample(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            shift_example.demo()
        text = output.getvalue()
        self.assertIn('Resample plan', text)
        scenarios = [
            json.loads(line[len('POINTWISE_SCENARIO '):])
            for line in text.splitlines()
            if line.startswith('POINTWISE_SCENARIO ')
        ]
        self.assertEqual([row['case_id'] for row in scenarios], ['N6', 'N7'])
        resample = scenarios[1]
        self.assertEqual(resample['actual']['status'], 'scheduled')
        self.assertEqual(resample['actual']['finish'], resample['expected']['finish'])
        self.assertEqual(resample['readback']['plan']['finish'], resample['actual']['finish'])
        self.assertEqual(resample['readback']['receipt']['plan_id'],
                         resample['readback']['plan']['plan_id'])


if __name__ == '__main__':
    unittest.main()
