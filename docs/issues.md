# Review issues — PR #45

Source: [review comment by @damienjburks](https://github.com/devsecblueprint/the-herald/pull/45#issuecomment-5503342243),
2026-09-02.

The review asked for the YouTube implementation to be reorganized before
merging, **while preserving the repository's existing horizontal layering**.
Ten distinct issues were raised. They are catalogued below with the evidence
that supported each one, measured against `app/services/youtube` as it stood
at the time of review.

Resolution for every issue is recorded in [update.md](./update.md).

---

## Summary

| # | Issue | Category | Status |
|---|-------|----------|--------|
| 1 | One flat package holds every layer | Structure | Resolved |
| 2 | Package too large: 17 modules, 3,193 lines | Structure | Resolved |
| 3 | Three modules each exceed 450 lines | Structure | Partly resolved |
| 4 | `YouTubePipeline` is a god object | Design | Resolved |
| 5 | Service boundary is too wide | Design | Resolved |
| 6 | External HTTP behaviour lives in services | Layering | Resolved |
| 7 | DynamoDB behaviour lives in services | Layering | Resolved |
| 8 | Dependency assembly mixed into the service package | Layering | Resolved |
| 9 | Imports inside functions, with lint suppressions | Import rules | Resolved |
| 10 | Optional deps handled by bypassing import rules | Import rules | Resolved |

---

## 1. One flat package holds every layer

`app/services/youtube` contained clients, configuration, persistence, HTTP
routes, models, utilities, dependency construction, and business logic side by
side in a single package. The repository already organises `app/` horizontally
by kind (`clients/`, `config/`, `services/`, `utils/`), so the feature was the
one part of the codebase that did not follow the house structure.

**Required:** retain the layer-oriented structure and move each responsibility
into its corresponding package.

**Target layout given in the review:**

```text
app/
  clients/      discord.py, http.py, youtube.py
  config/       youtube.py
  models/       feeds.py, youtube.py
  repositories/ youtube/{processing,roster,channel_cache}.py
  services/     youtube/{ingestion,classification,publishing,polling}.py
  routes/       youtube.py
  utils/        clock.py, logging.py
```

---

## 2. Package too large

17 production modules totalling **3,193 lines** in one package.

| Module | Lines | Module | Lines |
|--------|------:|--------|------:|
| `repository.py` | 478 | `http.py` | 132 |
| `pipeline.py` | 472 | `api.py` | 79 |
| `config.py` | 456 | `logging_utils.py` | 77 |
| `distribution.py` | 272 | `scheduler.py` | 72 |
| `shorts.py` | 233 | `__init__.py` | 66 |
| `models.py` | 212 | `errors.py` | 53 |
| `resolver.py` | 200 | `clock.py` | 42 |
| `ingestion.py` | 165 | `protocols.py` | 39 |
| `factory.py` | 145 | | |

---

## 3. Three modules each exceed 450 lines

`pipeline.py` (472), `repository.py` (478), and `config.py` (456) were each
past 450 lines, making the individual workflows hard to maintain
independently.

---

## 4. `YouTubePipeline` is a god object

A single class coordinated **eight** concerns:

- ingestion
- source lifecycle
- watermarks
- claims
- classification
- distribution
- retries
- failure handling

This gave one service knowledge of nearly every part of the feature.

---

## 5. Service boundary is too wide

**Required split:**

- `YouTubePublishingService` owns the per-video workflow:
  `claim -> classify -> publish -> record`.
- `YouTubePollingService` only loads sources, invokes ingestion and
  publishing, and advances the source watermark.

---

## 6. External HTTP behaviour lives in services

`http.py`, `distribution.py` (Discord transports), `resolver.py` (channel-page
scraping and Data API lookups) and the Shorts URL probe in `shorts.py` all
issued outbound HTTP from inside `services/`.

**Required:** external HTTP behaviour belongs in `clients`.

---

## 7. DynamoDB behaviour lives in services

`repository.py` held all three DynamoDB concerns — per-video processing state,
the source roster, and the resolved-channel cache — inside the service
package.

**Required:** DynamoDB behaviour belongs in `repositories`.

---

## 8. Dependency assembly mixed into the service package

`factory.py` performed the production wiring from inside
`app/services/youtube`, so the service package reached for `boto3` and the
process environment directly.

**Required:** dependency assembly should occur once at the application
boundary — `main.py` or a small `app/bootstrap.py`.

---

## 9. Imports inside functions, with lint suppressions

Three sites deferred imports into function bodies and suppressed the resulting
lint error:

| Location | Import | Suppression |
|----------|--------|-------------|
| `app/services/youtube/api.py:39` | `fastapi` | `# pylint: disable=import-outside-toplevel` |
| `app/services/youtube/api.py:62` | `flask` | `# pylint: disable=import-outside-toplevel` |
| `app/services/youtube/factory.py:112` | `boto3` | `# pylint: disable=import-outside-toplevel` |

**Required:** `boto3`, FastAPI and Flask should be imported normally at module
scope in their respective client or route adapter modules.

---

## 10. Optional dependencies handled by bypassing import rules

The inline imports above existed to keep optional dependencies out of the
import graph and to isolate tests from AWS.

**Required:** handle optional dependencies and test isolation through adapter
boundaries and dependency injection, not by bypassing the project's import
rules.
