# Security Policy

## Reporting a vulnerability

**Do not open a public issue for security vulnerabilities.**

If you find a security problem in xskill — for example a way to leak API keys,
escape a sandbox, or execute arbitrary code through a crafted trajectory or
skill — report it privately:

- Use GitHub's [private vulnerability reporting](https://github.com/SkillNerds/xskill/security/advisories/new), or
- Email the maintainers (see repository profile).

Please include reproduction steps and the affected version. We aim to
acknowledge a report within a few days.

## Scope

xskill runs LLM-generated content (skills, trajectories) and shells out to
`git` and, optionally, Docker. Treat third-party skills as untrusted code.
Reports about sandbox escape, path traversal in skill install, credential
leakage in logs/trajectories, or injection through ingested trajectories are
in scope.

## Supported versions

Security fixes land on the latest released version. Older versions are not
backported.
