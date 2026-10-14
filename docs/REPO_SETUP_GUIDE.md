# Repository Setup Guide

Some of the challenge's security requirements are GitHub **repository settings**, not files. The "Use this template" button copies files but cannot flip these switches for you, so turn them on right after you create your repository. All of these features are **free for public repositories**.

Do this once, at the start.

# 1. Make the repository public

Submissions must be open source. Create the repo from the template as **Public** (or switch it to public under Settings → General → Danger Zone).

# 2. Enable secret scanning and push protection

Stops committed credentials — and blocks you from pushing new ones.

- Go to **Settings → Code security**.
- Under **Secret scanning**, click **Enable**.
- Under **Push protection**, click **Enable**.

Reference: https://docs.github.com/code-security/secret-scanning/introduction/about-secret-scanning

If a secret is ever detected, rotate (invalidate) it immediately — removing it from git history is not enough once it has been pushed.

# 3. Enable code scanning (CodeQL)

- Go to **Settings → Code security → Code scanning**.
- Next to **CodeQL analysis**, choose **Set up → Default**.

Default setup requires no workflow file and covers the common languages. If you need to customize it, use the advanced workflow at `.github/workflows/codeql.yml` instead and delete the default setup.

If your language is not supported by CodeQL (for example, R or Julia), you cannot use it — rely on secret scanning, Dependabot, and dependency review, and note this in `docs/DISCLOSURES.md`.

# 4. Confirm Dependabot alerts

- Go to **Settings → Code security**.
- Ensure **Dependabot alerts** is enabled.

The `.github/dependabot.yml` in this template already schedules updates for your GitHub Actions. Uncomment the block for your language so it also watches your project dependencies.

# 5. Turn on private vulnerability reporting

- Go to **Settings → Code security**.
- Enable **Private vulnerability reporting** so issues can be reported to you privately.

# 6. Know how to export your SBOM

You do not need a workflow to produce a software bill of materials. GitHub builds one automatically from your dependency manifests:

- Go to **Insights → Dependency graph → Export SBOM** (SPDX JSON), or
- Use the REST API: `GET /repos/{owner}/{repo}/dependency-graph/sbom`.

Use this to complete the dependency table in `docs/DISCLOSURES.md`.

# Quick verification

Before you submit, confirm each of these shows as enabled under
**Settings → Code security**:

- [ ] Secret scanning
- [ ] Push protection
- [ ] Code scanning (CodeQL default setup **or** the advanced workflow)
- [ ] Dependabot alerts
- [ ] Private vulnerability reporting
