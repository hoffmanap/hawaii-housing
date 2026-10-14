# Security Policy

## Reporting a vulnerability

If you discover a security issue in this project, please report it privately rather than opening a public issue. Use GitHub's **Private vulnerability reporting** (the "Report a vulnerability" button under the repository's Security tab), which the team has enabled per the challenge setup guide.

Please include enough detail to reproduce the issue. We will acknowledge the report and respond as the challenge timeline allows.

## Handling of secrets

This repository has secret scanning and push protection enabled. If a credential is ever committed, it must be rotated (invalidated) immediately — removing it from history alone is not sufficient once it has been pushed.
