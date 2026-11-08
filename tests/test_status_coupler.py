"""Tests for scripts/status_coupler.py (planning #2930). Run: python3 -m unittest discover -s tests"""

import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest


def read(path):
    with open(path, encoding='utf-8') as fh:
        return fh.read()

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, '..', 'scripts', 'status_coupler.py')
sys.path.insert(0, os.path.join(HERE, '..', 'scripts'))
import status_coupler as sc  # noqa: E402

REPO = 'neckarshore-mmps/kaze'


class Parse(unittest.TestCase):
    def test_closing_refs_and_colon(self):
        r = sc.parse('Closes #12\nRefs #13\nfixes: #14', REPO)
        self.assertEqual(r['same'], [12, 13, 14])

    def test_same_repo_qualified_is_same(self):
        self.assertEqual(sc.parse('Refs neckarshore-mmps/kaze#7', REPO)['same'], [7])

    def test_cross_repo(self):
        r = sc.parse('Refs neckarshore-ai/neckarshore-planning#2930', REPO)
        self.assertEqual((r['same'], r['cross']), ([], ['neckarshore-ai/neckarshore-planning#2930']))

    def test_negated_bold_like_kaze_743(self):
        r = sc.parse('Tests for #704 slice 4. **Do not close #705** until criterion 7.', REPO)
        self.assertEqual((r['same'], r['negated']), ([], ['#705']))

    def test_negation_only_directly_before_keyword(self):
        # musclecat #678: "not" earlier in the line is not a negation of the keyword
        r = sc.parse('the first poll window is not a STALL Closes #676', REPO)
        self.assertEqual((r['same'], r['negated']), ([676], []))

    def test_contraction_negation(self):
        self.assertEqual(sc.parse("this doesn't close #9", REPO)['negated'], ['#9'])

    def test_negated_anywhere_wins_over_positive(self):
        r = sc.parse('Refs #5\nDo not close #5', REPO)
        self.assertEqual((r['same'], r['negated']), ([], ['#5']))

    def test_closing_only_flag_ignores_refs(self):
        self.assertEqual(sc.parse('Refs #3\nCloses #4', REPO, ('closing',))['same'], [4])

    def test_bare_hash_and_prose_are_not_references(self):
        self.assertEqual(sc.parse('planning #2930 and prefixes #3, see #4', REPO),
                         {'same': [], 'cross': [], 'negated': []})

    def test_dedup(self):
        self.assertEqual(sc.parse('Refs #8\nCloses #8\nbranch refs #8', REPO)['same'], [8])


FAKE_GH = textwrap.dedent('''\
    #!/usr/bin/env python3
    import json, os, sys
    a = sys.argv[1:]
    with open(os.environ['GH_LOG'], 'a') as fh:
        fh.write(json.dumps(a) + '\\n')
    state = json.load(open(os.environ['GH_STATE']))
    if a[:2] == ['label', 'list']:
        print(json.dumps([{'name': n} for n in state['labels']])); sys.exit(0)
    if a[0] == 'api' and '/issues/' in a[1]:
        n = a[1].rsplit('/', 1)[1]
        if n not in state['issues']:
            sys.stderr.write('HTTP 404'); sys.exit(1)
        print(json.dumps(state['issues'][n])); sys.exit(0)
    if a[0] in ('issue', 'pr'):
        sys.exit(state.get('write_rc', 0))
    sys.exit(2)
''')


class Run(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        bin_dir = os.path.join(self.tmp, 'bin')
        os.mkdir(bin_dir)
        gh = os.path.join(bin_dir, 'gh')
        with open(gh, 'w') as fh:
            fh.write(FAKE_GH)
        os.chmod(gh, os.stat(gh).st_mode | stat.S_IEXEC)
        self.log = os.path.join(self.tmp, 'gh.log')
        self.state_file = os.path.join(self.tmp, 'state.json')
        self.summary = os.path.join(self.tmp, 'summary.md')
        self.env = dict(os.environ, PATH=bin_dir + os.pathsep + os.environ['PATH'], GH_LOG=self.log,
                        GH_STATE=self.state_file, REPO=REPO, PR_NUMBER='900',
                        PR_URL='https://github.com/%s/pull/900' % REPO, MERGE_SHA='a' * 40,
                        MERGED_AT='2026-11-08T15:00:00Z', PR_TITLE='', PR_BODY='', PR_BRANCH='bob/x',
                        GITHUB_STEP_SUMMARY=self.summary)
        self.state(labels=['to-do', 'in-progress', 'in-review', 'done'], issues={})

    def state(self, **kw):
        with open(self.state_file, 'w') as fh:
            json.dump(kw, fh)

    def go(self, body, **env):
        e = dict(self.env, PR_BODY=body, **env)
        p = subprocess.run([sys.executable, SCRIPT], env=e, capture_output=True, text=True)
        calls = [json.loads(l) for l in read(self.log).splitlines()] if os.path.exists(self.log) else []
        return p, [c for c in calls if c[0] in ('issue', 'pr')]

    def issue(self, state='open', labels=(), pr=False):
        d = {'state': state, 'labels': [{'name': l} for l in labels]}
        if pr:
            d['pull_request'] = {}
        return d

    def test_moves_to_do_to_in_review_with_comment(self):
        self.state(labels=['to-do', 'in-review'], issues={'12': self.issue(labels=['to-do', 'P2'])})
        p, writes = self.go('Closes #12')
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn(['issue', 'edit', '12', '--repo', REPO, '--add-label', 'in-review', '--remove-label', 'to-do'], writes)
        self.assertTrue(any(w[:3] == ['issue', 'comment', '12'] and 'pull/900' in w[-1] for w in writes))
        self.assertIn('#12 -> in-review', read(self.summary))

    def test_done_is_never_overwritten(self):
        self.state(labels=['in-review', 'done'], issues={'5': self.issue(labels=['done'])})
        p, writes = self.go('Refs #5')
        self.assertEqual((p.returncode, writes), (0, []))
        self.assertIn('carries done', p.stdout)

    def test_closed_and_pr_and_already_in_review_untouched(self):
        self.state(labels=['in-review'], issues={'1': self.issue(state='closed', labels=['in-progress']),
                                                 '2': self.issue(pr=True), '3': self.issue(labels=['in-review'])})
        p, writes = self.go('Refs #1\nRefs #2\nRefs #3')
        self.assertEqual((p.returncode, writes), (0, []))

    def test_cross_repo_and_negated_comment_on_pr_only(self):
        p, writes = self.go('Refs neckarshore-ai/neckarshore-planning#2930\nDo not close #705')
        self.assertEqual(p.returncode, 0)
        self.assertEqual([w[:3] for w in writes], [['pr', 'comment', '900']] * 2)
        self.assertIn('slice 2', writes[0][-1])
        self.assertIn('#705', writes[1][-1])

    def test_no_ref_comment_mode(self):
        p, writes = self.go('just a report')
        self.assertEqual(writes, [['pr', 'comment', '900', '--repo', REPO, '--body',
                                   'coupler: no issue reference found — label not moved']])

    def test_no_ref_summary_mode_writes_nothing_on_github(self):
        p, writes = self.go('just a report', NO_REF_MODE='summary')
        self.assertEqual((p.returncode, writes), (0, []))
        self.assertIn('no issue reference found', read(self.summary))

    def test_missing_in_review_label_fails_closed(self):
        self.state(labels=['to-do'], issues={'12': self.issue(labels=['to-do'])})
        p, writes = self.go('Closes #12')
        self.assertEqual((p.returncode, writes), (1, []))
        self.assertIn('no in-review label', p.stdout)
        self.assertIn('#12 not moved (no in-review label)', read(self.summary))

    def test_unknown_issue_fails_closed(self):
        p, writes = self.go('Closes #404')
        self.assertEqual((p.returncode, writes), (1, []))

    def test_one_bad_reference_does_not_stop_the_rest(self):
        # Refs #7055 is a typo (404); #12 must still move and the negation must still warn.
        self.state(labels=['in-review'], issues={'12': self.issue(labels=['to-do'])})
        p, writes = self.go('Refs #7055\nCloses #12\nDo not close #705')
        self.assertEqual(p.returncode, 1)
        self.assertTrue(any(w[:3] == ['issue', 'edit', '12'] for w in writes))
        self.assertTrue(any(w[:2] == ['pr', 'comment'] and '#705' in w[-1] for w in writes))
        out = read(self.summary)
        self.assertIn('#7055 could not be read', out)
        self.assertIn('1 error(s)', out)

    def test_in_specification_is_not_moved(self):
        self.state(labels=['in-review'], issues={'6': self.issue(labels=['in-specification'])})
        p, writes = self.go('Refs #6')
        self.assertEqual(p.returncode, 0)
        self.assertFalse(any(w[0] == 'issue' for w in writes))
        self.assertTrue(any(w[:2] == ['pr', 'comment'] and 'in-specification' in w[-1] for w in writes))

    def test_failed_write_fails_closed(self):
        self.state(labels=['in-review'], issues={'12': self.issue(labels=['to-do'])}, write_rc=1)
        p, _ = self.go('Closes #12')
        self.assertEqual(p.returncode, 1)

    def test_bad_no_ref_mode_and_bad_repo_fail(self):
        self.assertEqual(self.go('x', NO_REF_MODE='loud')[0].returncode, 1)
        self.assertEqual(self.go('x', REPO='kaze; rm -rf /')[0].returncode, 1)

    def test_dry_run_makes_no_write(self):
        self.state(labels=['in-review'], issues={'12': self.issue(labels=['to-do'])})
        e = dict(self.env, PR_BODY='Closes #12')
        p = subprocess.run([sys.executable, SCRIPT, '--dry-run'], env=e, capture_output=True, text=True)
        calls = [json.loads(l) for l in read(self.log).splitlines()]
        self.assertEqual((p.returncode, [c for c in calls if c[0] in ('issue', 'pr')]), (0, []))
        self.assertIn('DRY-RUN gh issue edit 12', p.stdout)


if __name__ == '__main__':
    unittest.main()
