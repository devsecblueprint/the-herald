# Pull Request (PR) Rules - The Herald Bot

## Introduction

The **DevSec Blueprint (DSB)** community is dedicated to educating members about **DevSecOps** principles, tools, and best practices. As part of our commitment to maintaining high-quality software development, **The Herald** Discord bot follows structured **Pull Request (PR) guidelines** to ensure clarity, maintainability, and consistency across contributions.

This document outlines the **Conventional Commits** specification for PR titles, ensuring that contributions align with **best DevSecOps practices**, facilitate automated release generation, and maintain a structured commit history.

---

## PR Title Formatting

Every **non-draft PR** must follow the **Conventional Commits** specification for PR titles. This ensures:

- **Consistent and readable commit history**
- **Automatic generation of release notes**
- **Avoidance of merge conflicts in release documentation**

### PR Title Format

A PR title should follow this structure:

```
<type>([optional scope]): <description>
```

Where:

- **`<type>`** – Defines the nature of the change (see **Types** below)
- **`[optional scope]`** – Provides additional context (optional but recommended)
- **`<description>`** – Brief and meaningful description of the change

---

## PR Types

Each PR must fall under one of the following types:

| Type         | Description                                                                                |
| ------------ | ------------------------------------------------------------------------------------------ |
| **feat**     | Introduces a new feature for The Herald bot                                                |
| **fix**      | Resolves a bug or issue in the bot’s code                                                  |
| **chore**    | Non-code changes, such as documentation updates, manual release notes, or repo maintenance |
| **docs**     | Updates to documentation, including README or wiki pages                                   |
| **build**    | Changes to build scripts or dependency updates                                             |
| **ci**       | Modifications to CI/CD pipelines, such as GitHub Actions                                   |
| **refactor** | Code refactoring without functional changes                                                |
| **style**    | Formatting, whitespace, or stylistic changes that don’t alter logic                        |
| **test**     | Adding or updating tests                                                                   |
| **perf**     | Performance improvements (unlikely to be common for The Herald)                            |

---

## Examples

| PR Title                                                              | Description                                                   |
| --------------------------------------------------------------------- | ------------------------------------------------------------- |
| `feat(bot): Add role-based access control`                            | Introduces role-based access control (RBAC) to The Herald bot |
| `fix(auth): Resolve incorrect token expiration handling`              | Fixes a bug where tokens were expiring prematurely            |
| `ci(workflows): Update deployment pipeline to include security scans` | Enhances CI/CD pipeline with security checks                  |
| `docs(readme): Add setup guide for contributors`                      | Improves documentation for new contributors                   |
| `test(commands): Add unit tests for moderation commands`              | Adds missing tests for bot moderation features                |
| `chore: Release v1.2.0`                                               | Manual release note update for version 1.2.0                  |

---

## Additional Guidelines

- **Scope (optional but encouraged):** Use it when it provides meaningful context (e.g., `feat(bot): ...` instead of just `feat: ...`).
- **Keep descriptions clear and concise:** Avoid unnecessary details in the title; add further context in the PR description.
- **Follow best security practices:** Any security-related PRs should be labeled accordingly and reviewed carefully before merging.

---

## Why This Matters

By following this **PR title convention**, contributors to **The Herald** help:

✅ Maintain a **structured and meaningful commit history**  
✅ Enable **automated changelogs** and **release note generation**  
✅ Improve **code review efficiency** for the **DSB** community

Thank you for contributing to **The Herald** and supporting **DevSecOps education!** 🚀

---

