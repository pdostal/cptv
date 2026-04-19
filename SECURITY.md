# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in this project, please report it
responsibly by emailing **pdostal@pdostal.cz**. Do not open a public issue.

You should receive an acknowledgement within 48 hours. A fix or mitigation will
be released as soon as practical, typically within 7 days.

## Supported Versions

Only the latest release is supported with security updates.

## Scope

This policy covers the `cptv` application code and its container image
(`ghcr.io/pdostal/cptv`). Third-party dependencies are monitored by Dependabot
and scanned by `pip-audit`, `npm audit`, and `trivy` on every push.
