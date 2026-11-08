#!/usr/bin/env python3
"""status_coupler.py -- a merged pull request moves the tickets it names to in-review.

Planning #2930 (RETRO-HEBEL-STATUS-COUPLER), writer half, slice 1. Called by
.github/workflows/status-coupler.yml after a pull request is merged.

What it reads: the pull request's title, body and head branch, from the
environment (PR_TITLE, PR_BODY, PR_BRANCH). That text is untrusted: it is
parsed, never executed, and only ticket numbers taken from it reach gh.

What it does, per reference found:
  1. a reference in the SAME repository -> the ticket gets in-review instead of
     to-do / in-progress / in-specification, plus one comment that names the
     pull request. It is left alone when it is closed, already done, already
     in-review, or is itself a pull request. `done` is never overwritten: that
     stays MASCHIN's PIR act (Completion rule 2) or a recorded Founder verdict.
  2. a reference to ANOTHER repository -> nothing moves; the pull request gets
     a comment that says so (slice 1 has no token for other repositories).
  3. a NEGATED keyword ("Do not close #705") -> nothing moves; the pull request
     gets a warning, because GitHub's own parser closes on it anyway (kaze
     #704/#705, the mechanism that led here).
  4. no reference at all -> NO_REF_MODE=comment writes one comment on the pull
     request; NO_REF_MODE=summary writes only a line in the job summary (planning,
     where 146 of 150 merged pull requests carry no keyword: comment 6722195126).

Keywords: KEYWORDS=closing,refs (default) or closing. `refs` is the completion
signal on the app boards (135 of 164 hits in the replay); closing-only misses
about four fifths of them. The cut is Engels' on #2930; this is its flag.

Fail-closed: a repository without an in-review label, a gh call that fails or
a malformed environment ends the run with exit 1 and a line that says which.
--dry-run prints every write instead of making it (the tests use it).
"""

import json
import os
import re
import subprocess
import sys

STATUS_FROM = ('to-do', 'in-progress', 'in-specification')
TARGET = 'in-review'

CLOSING = r'close[sd]?|fix(?:e[sd])?|resolve[sd]?'
REFS = r'refs?'
NEGATION = re.compile(r"(?i)(?:\b(?:do|does|did|should|must|will|would)\s+not|\b\w+n't|\bnever|\bnot)[\s*_`]*$")


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
    no_ref_mode = env('NO_REF_MODE', False) or 'comment'
    if no_ref_mode not in ('comment', 'summary'):
        raise RuntimeError('NO_REF_MODE %r is neither comment nor summary' % no_ref_mode)
    text = '\n'.join([env('PR_TITLE', False), env('PR_BODY', False), env('PR_BRANCH', False)])

    found = parse(text, repo, keywords)
    gh = Gh(repo, dry_run)
    lines = []

    if found['same']:
        labels = {l['name'] for l in json.loads(gh.read('label', 'list', '--repo', repo, '--limit', '200', '--json', 'name'))}
        if TARGET not in labels:
            raise RuntimeError('%s has no %s label; create it (itsm-sync-manifest v5) before the coupler runs' % (repo, TARGET))

    for n in found['same']:
        data = json.loads(gh.read('api', 'repos/%s/issues/%d' % (repo, n)))
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
        if TARGET in names:
            lines.append('#%d already in-review' % n)
            continue
        args = ['issue', 'edit', str(n), '--repo', repo, '--add-label', TARGET]
        for old in STATUS_FROM:
            if old in names:
                args += ['--remove-label', old]
        gh.write(*args)
        gh.write('issue', 'comment', str(n), '--repo', repo, '--body',
                 'coupler: PR %s merged %s %s -> in-review (done stays MASCHIN\'s PIR act per Completion rule 2)'
                 % (pr_url, merge_sha[:12], merged_at))
        lines.append('#%d -> in-review' % n)

    pr_notes = []
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
        gh.write('pr', 'comment', pr_number, '--repo', repo, '--body', note)

    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    report = ['### status-coupler: %s#%s' % (repo, pr_number), ''] + ['- ' + l for l in lines]
    print('\n'.join(report))
    if summary and not dry_run:
        with open(summary, 'a', encoding='utf-8') as fh:
            fh.write('\n'.join(report) + '\n')
    return 0


def main(argv=None):
    try:
        return run(sys.argv[1:] if argv is None else argv)
    except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print('::error::status-coupler: %s' % exc)
        return 1


if __name__ == '__main__':
    sys.exit(main())
