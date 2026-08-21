# Security Policy

SentinelPCB is a NUS-ISS SWE5008 coursework prototype, not a production system — there is no
dedicated security team and no SLA on fixes. That said, if you find a genuine vulnerability
(secrets exposure, injection, auth bypass, etc.), please report it responsibly rather than opening
a public issue.

## Reporting a vulnerability

Email **jeffrey.chong28@gmail.com** with:

- A description of the issue and its potential impact
- Steps to reproduce (or a PoC)
- Any relevant logs/screenshots

We'll acknowledge within a few days and let you know if/when a fix lands. Please don't test against
any deployed instance beyond what's needed to demonstrate the issue, and don't access or modify
data that isn't yours.

## Scope notes

- This repo has no production deployment; the shared Supabase/Postgres instance is used for team
  development only and should not contain real PCB or customer data.
- `ANTHROPIC_API_KEY` and other secrets belong in `.env` (gitignored) — never commit them. If you
  find a committed secret, report it via the channel above rather than filing a public issue.
