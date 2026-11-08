#!/usr/bin/env python3
"""status_coupler.py -- a merged pull request moves the tickets it names to in-review.

Planning #2930 (RETRO-HEBEL-STATUS-COUPLER), writer half, slice 1. Called by
.github/workflows/status-coupler.yml after a pull request is merged.

What it reads: the pull request's title, body and head branch, from the
environment (PR_TITLE, PR_BODY, PR_BRANCH). A keyword needs whitespace before
#N, so a branch name (bob/2930-x) never matches; the branch is read for the
rare branch written as words. That text is untrusted: it is
parsed, never executed, and only ticket numbers taken from it reach gh.

What it does, per reference found:
  1. a reference in the SAME repository -> a ticket on to-do or in-progress
     gets in-review instead, plus one comment that names the pull request.
     Markers (blocked, failed-acceptance) are left as they are. Not moved: a
     closed ticket, a done ticket, a pull request (silently), and a ticket on
     in-review, in-specification or no status label (the PR gets a note). `done` is never overwritten: that
     stays MASCHIN's PIR act (Completion rule 2) or a recorded Founder verdict.
  2. a reference to ANOTHER repository -> nothing moves; the pull request gets
     a comment that says so (slice 1 has no token for other repositories).
  3. a NEGATED keyword ("Do not close #705") -> nothing moves; the pull request
     gets a warning, because GitHub's own parser closes on it anyway (kaze
     #704/#705, the mechanism that led here).
  4. no reference at all -> one comment on the pull request; in planning
     (SUMMARY_ONLY_REPOS) only a line in the job summary, because 146 of 150
     of its merged pull requests carry no keyword (comment 6722195126).

Keywords: closing keywords and refs. `refs` is the completion signal on the app
boards (135 of 164 hits in the replay); closing-only misses about four fifths of
them. Engels' cut (#2930 comment 6722272482) is built in as the default; callers
set nothing. KEYWORDS and NO_REF_MODE in the environment exist for the tests.

Fail-closed: a malformed environment ends the run at once with exit 1. A
repository without an in-review label or a gh call that fails is recorded per
ticket and the run goes on (the other tickets, the PR notes, the summary);
it then ends with exit 1 and lists every failure.
--dry-run prints every write instead of making it (the tests use it).
"""

import json
import os
import re
import subprocess
import sys

STATUS_FROM = ('to-do', 'in-progress')
# No-ref behaviour is a default per repository, not a caller flag (Engels' hand-off on
# #2930): planning summarises, because 146 of 150 of its PRs name no ticket; every
# other repository comments. NO_REF_MODE in the environment exists for the tests only.
SUMMARY_ONLY_REPOS = {'neckarshore-ai/neckarshore-planning'}
TARGET = 'in-review'

CLOSING = r'close[sd]?|fix(?:e[sd])?|resolve[sd]?'
REFS = r'refs?'
# Negation guard (Engels' cut, #2930 comment 6722272482, sharpening 3). Built on the
# three real cases of the replay (comment 6722195126), quoted verbatim:
#   kaze #743, #724   "**Do not close #705"
#   kaze #745         "does **not** close #704"
#   musclecat #705    "**Do not close #692"
# TOKENS: not, never, without, kein, nicht, and any contraction ending in n't
#   (don't, doesn't, won't, ...); "do not" / "does not" are covered by "not".
# WINDOW: the token must stand directly before the keyword on the same line,
#   with nothing between them but whitespace and Markdown emphasis (* _ `).
#   A "not" earlier in the sentence is not a negation of the keyword: musclecat
#   #678 "is not a STALL Closes #676" and #538 "is not bundled Closes #516" were
#   the two false hits of a wider window.
# REACH: the guard keeps the coupler honest and warns on the PR. It does not
#   undo GitHub's own close, which still happens on a negated closing keyword.
NEGATION = re.compile(r"(?i)(?:\b(?:not|never|without|kein|nicht)|\b\w+n't)[\s*_`]*$")


def keyword_re(keywords):
    alts = []
    if 'closing' in keywords:
        alts.append(CLOSING)
    if 'refs' in keywords:
        alts.append(REFS)
    if not alts:
        raise ValueError('KEYWORDS names neither closing nor refs')
    return re.compile(r'(?i)\b(%s)\b:?\s+(?:([\w.-]+/[\w.-]+))?#(\d+)\b' % '|'.join(alts))


def parse(text, repo, keywords=('closing', 'refs')):
    """Return {'same': [n...], 'cross': ['org/repo#n'...], 'negated': ['#n'...]} in first-seen order."""
    rx = keyword_re(keywords)
    same, cross, negated = [], [], []
    for m in rx.finditer(text):
        line_start = text.rfind('\n', 0, m.start()) + 1
        before = text[line_start:m.start()]
        target_repo, num = m.group(2), int(m.group(3))
        is_cross = bool(target_repo) and target_repo.lower() != repo.lower()
        label = '%s#%d' % (target_repo, num) if is_cross else '#%d' % num
        if NEGATION.search(before):
            if label not in negated:
                negated.append(label)
            continue
        if is_cross:
            if label not in cross:
                cross.append(label)
        elif num not in same:
            same.append(num)
    # A ticket that one sentence negates and another names is not moved: the
    # negation is the more specific statement and the safe side.
    same = [n for n in same if '#%d' % n not in negated]
    return {'same': same, 'cross': cross, 'negated': negated}


class Gh:
    def __init__(self, repo, dry_run):
        self.repo, self.dry_run = repo, dry_run

    def read(self, *args):
        out = subprocess.run(['gh'] + list(args), capture_output=True, text=True)
        if out.returncode != 0:
            raise RuntimeError('gh %s failed: %s' % (' '.join(args[:3]), out.stderr.strip()[:300]))
        return out.stdout

    def write(self, *args):
        if self.dry_run:
            print('DRY-RUN gh ' + ' '.join(args))
            return
        self.read(*args)


def env(name, required=True):
    v = os.environ.get(name, '')
    if required and not v:
        raise RuntimeError('environment variable %s is empty' % name)
    return v


def run(argv):
    dry_run = '--dry-run' in argv
    repo = env('REPO')
    if not re.fullmatch(r'[\w.-]+/[\w.-]+', repo):
        raise RuntimeError('REPO %r is not owner/name' % repo)
    pr_number = env('PR_NUMBER')
    if not pr_number.isdigit():
        raise RuntimeError('PR_NUMBER %r is not a number' % pr_number)
    pr_url, merge_sha, merged_at = env('PR_URL'), env('MERGE_SHA'), env('MERGED_AT')
    keywords = tuple(k.strip() for k in env('KEYWORDS', False).split(',') if k.strip()) or ('closing', 'refs')
    no_ref_mode = env('NO_REF_MODE', False) or ('summary' if repo.lower() in SUMMARY_ONLY_REPOS else 'comment')
    if no_ref_mode not in ('comment', 'summary'):
        raise RuntimeError('NO_REF_MODE %r is neither comment nor summary' % no_ref_mode)
    text = '\n'.join([env('PR_TITLE', False), env('PR_BODY', False), env('PR_BRANCH', False)])

    found = parse(text, repo, keywords)
    gh = Gh(repo, dry_run)
    lines = []

    errors = []

    def attempt(what, fn, *args):
        """One failed gh call is recorded and the run goes on; the exit code says it at the end."""
        try:
            return fn(*args)
        except (RuntimeError, json.JSONDecodeError) as exc:
            errors.append('%s: %s' % (what, exc))
            return None

    same = found['same']
    if same:
        out = attempt('label list', gh.read, 'label', 'list', '--repo', repo, '--limit', '200', '--json', 'name')
        labels = {l['name'] for l in json.loads(out)} if out is not None else set()
        if out is not None and TARGET not in labels:
            errors.append('%s has no %s label; create it (itsm-sync-manifest v5) before the coupler runs' % (repo, TARGET))
        if TARGET not in labels:
            lines += ['#%d not moved (no %s label)' % (n, TARGET) for n in same]
            same = []

    pr_notes = []
    for n in same:
        out = attempt('#%d read' % n, gh.read, 'api', 'repos/%s/issues/%d' % (repo, n))
        if out is None:
            lines.append('#%d could not be read, not moved' % n)
            continue
        data = json.loads(out)
        names = {l['name'] for l in data.get('labels', [])}
        if 'pull_request' in data:
            lines.append('#%d is a pull request, not moved' % n)
            continue
        if data.get('state') != 'open':
            lines.append('#%d is closed, not moved' % n)
            continue
        if 'done' in names:
            lines.append('#%d carries done, not moved (done is never overwritten)' % n)
            continue
        movable = [l for l in STATUS_FROM if l in names]
        if not movable:
            # Move only from to-do or in-progress (Engels' cut, sharpening 1). in-review,
            # in-specification (two gates, issue-workflow.md v5) or no status label: the
            # coupler writes one field and never invents a state the board did not have.
            carries = next((l for l in (TARGET, 'in-specification') if l in names), 'no status label')
            lines.append('#%d carries %s, not moved' % (n, carries))
            pr_notes.append('coupler: #%d carries %s, not moved' % (n, carries))
            continue
        args = ['issue', 'edit', str(n), '--repo', repo, '--add-label', TARGET]
        for old in movable:
            args += ['--remove-label', old]
        before = len(errors)
        attempt('#%d edit' % n, gh.write, *args)
        if len(errors) > before:
            lines.append('#%d label move FAILED' % n)
            continue
        attempt('#%d comment' % n, gh.write, 'issue', 'comment', str(n), '--repo', repo, '--body',
                'coupler: PR %s merged %s %s -> in-review (done stays MASCHIN\'s PIR act per Completion rule 2)'
                % (pr_url, merge_sha[:12], merged_at))
        lines.append('#%d -> in-review' % n)

    for ref in found['cross']:
        org = ref.split('/')[0]
        pr_notes.append('coupler: cross-repo reference %s not moved — no token for %s (slice 2)' % (ref, org))
        lines.append('%s cross-repo, not moved' % ref)
    for ref in found['negated']:
        pr_notes.append('coupler: negated reference "%s" not moved. GitHub closes on a closing keyword even after "not"; '
                        'check that %s is in the state you meant.' % (ref, ref))
        lines.append('%s negated, not moved' % ref)
    if not (found['same'] or found['cross'] or found['negated']):
        lines.append('no issue reference found, label not moved')
        if no_ref_mode == 'comment':
            pr_notes.append('coupler: no issue reference found — label not moved')
    for note in pr_notes:
        attempt('PR comment', gh.write, 'pr', 'comment', pr_number, '--repo', repo, '--body', note)

    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    report = ['### status-coupler: %s#%s' % (repo, pr_number), ''] + ['- ' + l for l in lines]
    if errors:
        report += ['', '**%d error(s):**' % len(errors)] + ['- ' + e for e in errors]
    print('\n'.join(report))
    if summary and not dry_run:
        with open(summary, 'a', encoding='utf-8') as fh:
            fh.write('\n'.join(report) + '\n')
    for e in errors:
        print('::error::status-coupler: %s' % e)
    return 1 if errors else 0


def main(argv=None):
    try:
        return run(sys.argv[1:] if argv is None else argv)
    except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print('::error::status-coupler: %s' % exc)
        return 1


if __name__ == '__main__':
    sys.exit(main())
