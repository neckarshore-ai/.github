# Security Policy

> Org-wide default security policy for all repositories in the `neckarshore-ai` organization,
> provided via this `.github` repository. A repository that ships its own `SECURITY.md`
> overrides this default for that repository.

## Supported Versions

| Version | Supported |
|---------|-----------|
| current `main` | Yes |
| latest tagged release (where releases exist) | Yes |
| older tags | No |

The estate follows a rolling model: fixes land on `main` first and are released from there.

## Reporting a Vulnerability

**Preferred (private):** use GitHub **private vulnerability reporting** — the
"Report a vulnerability" button on the affected repository's Security tab, where enabled.
This keeps the report confidential while we triage.

**Fallback:** open a GitHub issue on the affected repository and state that you have a
security-sensitive finding WITHOUT including exploit details — a maintainer will move the
conversation to a private channel. General contact: [neckarshore.ai](https://neckarshore.ai).

Please include:

1. **What you found** — describe the vulnerability
2. **How to reproduce** — steps or a proof-of-concept that triggers the issue
3. **Impact** — what could go wrong if exploited
4. **Affected repository/version** — repo name plus commit SHA or release tag

**Never post secrets, tokens, or working exploits in a public issue.** If you found an
exposed credential, report it privately — we will rotate it and confirm.

## Response Expectations

This estate is solo-maintained open source, not a commercial service with an SLA.
Best effort: acknowledgement within a few days, prioritized triage for anything
touching credentials, authentication, data exposure, or supply-chain integrity.

## Coordinated Disclosure

Please give us reasonable time to remediate before public disclosure. We credit
reporters in release notes on request.

## Scope

This default applies to every repository in `neckarshore-ai` that does not carry its own
`SECURITY.md`. Organization-level hardening (secret scanning, push protection,
Dependabot alerts, dependency graph) is enabled as the new-repository default
across the organization.
