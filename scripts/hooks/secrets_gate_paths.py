#!/usr/bin/python3
"""secrets_gate_paths: match FILE NAMES against the path rules of a gitleaks config.

planning #2928. gitleaks reports a path rule only where it gets a hunk of the
file. A binary file has none, an empty file has none, a pure rename has none.
Measured 2026-10-07: a random-byte `.p12` and `.key` passed
`gitleaks git --pre-commit --staged` with the estate config. So the names are
matched here as well, by one implementation that both readers of the config
use: the hook (scripts/hooks/secrets-gate.py imports this file) and, once it
is shipped there, the reusable workflow in neckarshore-ai/.github.

What it reads from the config: every `[[rules]]` table that has a `path` and
NO `regex`, and that rule's own `[[rules.allowlists]]` `paths`. It is not a
TOML parser. It reads the one spelling this estate's config uses
(`path = '''...'''`, `paths = ['''...''', ...]`) and REFUSES a config in which
it counts more `path`/`paths` keys than it could read: a rule it cannot read
must never become a rule that silently does not apply.

Regular expressions: gitleaks compiles them with Go's RE2, this file with
Python's `re`. The estate's path rules use only what both read the same way
(literals, classes, groups, `^ $ * + ?`, a leading `(?i)`). A pattern Python
cannot compile ends in an error, not in a skipped rule.

The way out for one file is the fingerprint gitleaks itself would print,
`<file>:<rule id>:<line>`, in the repository's `.gitleaksignore`; any line
number is accepted, because a file without a hunk has no line.

As a command (the CI use):
    secrets_gate_paths.py --config <toml> [--ignore-file <.gitleaksignore>] < NUL-separated names
prints one `<rule id>\t<file>` per hit. Exit 0 no hit, 1 hit(s), 2 anything else.
"""
import os
import re
import sys

RULE_HEADER = re.compile(r"^\s*\[\[rules\]\]\s*$")
ALLOW_HEADER = re.compile(r"^\s*\[\[rules\.allowlists\]\]\s*$")
OTHER_HEADER = re.compile(r"^\s*\[")
ID_LINE = re.compile(r"^\s*id\s*=\s*\"([^\"]+)\"\s*$")
PATH_LINE = re.compile(r"^\s*path\s*=\s*'''(.*)'''\s*$")
PATHS_LINE = re.compile(r"^\s*paths\s*=\s*\[(.*)\]\s*$")
PATHS_ITEM = re.compile(r"'''(.*?)'''")
REGEX_KEY = re.compile(r"^\s*regex\s*=")
PATH_KEY = re.compile(r"^\s*path\s*=")
PATHS_KEY = re.compile(r"^\s*paths\s*=")
COMMENT = re.compile(r"^\s*#")


class ConfigError(Exception):
    """The config cannot be read in full; the caller must fail closed."""


def _compile(pattern, where):
    try:
        return re.compile(pattern)
    except re.error as e:
        raise ConfigError("path pattern in %s does not compile here: %s (%s)" % (where, pattern, e))


def load_path_rules(config_path):
    """[(rule id, compiled path, [compiled allowlist paths])] for every path-only rule."""
    with open(config_path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    rules, cur, in_allow = [], None, False
    seen_path_keys = read_path_keys = 0
    for raw in lines:
        if COMMENT.match(raw) or not raw.strip():
            continue
        if RULE_HEADER.match(raw):
            cur = {"id": None, "path": None, "has_regex": False, "allow": []}
            rules.append(cur)
            in_allow = False
            continue
        if ALLOW_HEADER.match(raw):
            if cur is None:
                raise ConfigError("an allowlist before any rule")
            in_allow = True
            continue
        if OTHER_HEADER.match(raw):
            cur, in_allow = None, False
            continue
        if cur is None:
            continue  # a key of another table; `path` under [extend] is a file path, not a rule
        if PATH_KEY.match(raw) and not in_allow:
            seen_path_keys += 1
            m = PATH_LINE.match(raw)
            if m:
                read_path_keys += 1
                cur["path"] = m.group(1)
            continue
        if PATHS_KEY.match(raw) and in_allow:
            seen_path_keys += 1
            m = PATHS_LINE.match(raw)
            items = PATHS_ITEM.findall(m.group(1)) if m else []
            leftover = PATHS_ITEM.sub("", m.group(1)).replace(",", "").strip() if m else "x"
            if items and not leftover:
                read_path_keys += 1
                cur["allow"].extend(items)
            continue
        if REGEX_KEY.match(raw) and not in_allow:
            cur["has_regex"] = True
            continue
        m = ID_LINE.match(raw)
        if m and not in_allow:
            cur["id"] = m.group(1)
    if seen_path_keys != read_path_keys:
        raise ConfigError("%d path/paths key(s) in %s, only %d readable: a rule would silently not apply"
                          % (seen_path_keys, os.path.basename(config_path), read_path_keys))
    out = []
    for r in rules:
        if r["path"] is None or r["has_regex"]:
            continue  # a content rule, or a rule with both: gitleaks needs the content, so does this not apply
        if not r["id"]:
            raise ConfigError("a path rule without an id")
        out.append((r["id"], _compile(r["path"], r["id"]),
                    [_compile(a, r["id"] + " allowlist") for a in r["allow"]]))
    if not out:
        raise ConfigError("no path rule in %s" % os.path.basename(config_path))
    return out


def load_ignored(ignore_file):
    """{(file, rule id)} from a .gitleaksignore: `file:rule:line` or `commit:file:rule:line`."""
    ignored = set()
    if not ignore_file or not os.path.isfile(ignore_file):
        return ignored
    with open(ignore_file, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.split("#", 1)[0].strip()
            parts = line.split(":")
            if len(parts) < 3:
                continue
            # the file may itself hold no colon here; rule id and line are the last two fields
            rule, name = parts[-2], ":".join(parts[:-2])
            ignored.add((name, rule))
            if len(parts) >= 4 and re.match(r"^[0-9a-f]{7,40}$", parts[0]):
                ignored.add((":".join(parts[1:-2]), rule))
    return ignored


def match_names(names, rules, ignored=()):
    """[(rule id, file)] for every name a path rule matches and its allowlist does not."""
    ignored = set(ignored)
    hits, seen = [], set()
    for name in names:
        if not name:
            continue
        for rule_id, pattern, allow in rules:
            if (rule_id, name) in seen or (name, rule_id) in ignored:
                continue
            if pattern.search(name) and not any(a.search(name) for a in allow):
                seen.add((rule_id, name))
                hits.append((rule_id, name))
    return hits


def main(argv):
    config, ignore_file = None, None
    args = argv[1:]
    while args:
        a = args.pop(0)
        if a == "--config" and args:
            config = args.pop(0)
        elif a == "--ignore-file" and args:
            ignore_file = args.pop(0)
        else:
            raise ConfigError("unknown argument: " + a)
    if not config:
        raise ConfigError("--config is required")
    rules = load_path_rules(config)
    data = sys.stdin.buffer.read().decode("utf-8", errors="surrogateescape")
    names = [n for n in data.split("\0") if n]
    hits = match_names(names, rules, load_ignored(ignore_file))
    for rule_id, name in hits:
        sys.stdout.write("%s\t%s\n" % (rule_id, name.encode("utf-8", errors="backslashreplace").decode("ascii", errors="backslashreplace")
                                         if not name.isprintable() else name))
    sys.stderr.write("secrets_gate_paths: %d name(s), %d path rule(s), %d hit(s)\n" % (len(names), len(rules), len(hits)))
    return 1 if hits else 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception as e:  # noqa: BLE001 - never 0 or 1 over something unread
        sys.stderr.write("secrets_gate_paths: FAILED (%s: %s)\n" % (type(e).__name__, e))
        sys.exit(2)
