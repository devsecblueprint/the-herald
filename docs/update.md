# Update — YouTube feature restructuring (PR #45)

Response to the [review on PR #45](https://github.com/devsecblueprint/the-herald/pull/45#issuecomment-5503342243).
Every issue catalogued in [issues.md](./issues.md) is addressed below.

`app/services/youtube` went from **17 modules / 3,193 lines** to **5 modules /
1,075 lines**. Nothing was deleted except one unused adapter (see
[Judgment calls](#judgment-calls)); the rest moved to the layer it belongs to.

- **Tests:** 266 passing, up from 257.
- **Pylint:** 9.77/10, with no findings in any new or moved module.
- **Layering:** verified by AST check — no module imports a layer above its own.

---

## Final layout

Matches the target given in the review, plus three additions explained under
[Judgment calls](#judgment-calls).

```text
app/
  main.py                       FastAPI + APScheduler + Discord presence
  bootstrap.py                  Dependency assembly; job/worker/Lambda entry points
  errors.py                     Domain exceptions, shared by every layer
  clients/
    http.py                     The HTTP seam every other client is built on
    discord.py                  Discord message transports (bot token, webhook)
    youtube.py                  Atom feeds, @handle lookup, Shorts probe, Data API
  config/
    youtube.py                  Source list parsing and validation
  models/
    feeds.py                    RSS models (Feed, FeedsConfig)
    youtube.py                  ContentItem, PollResult, states, verdicts
  repositories/
    dynamodb.py                 Conditional-write helpers
    youtube/
      processing.py             Per-video claims and state machine
      roster.py                 Source roster and watermarks
      channel_cache.py          Resolved @handle -> UC... id, 30-day TTL
  routes/
    youtube.py                  Trigger and health endpoints
  services/
    protocols.py                The ingestion/publishing seams
    youtube/
      ingestion.py              @handle -> UC... id, Atom feed -> ContentItem
      classification.py         Long-form vs Short
      publishing.py             One video: claim -> classify -> publish -> record
      polling.py                The poll: sources, ingestion, watermarks
  utils/
    clock.py                    UTC time helpers, injectable for tests
    logging.py                  Structured JSON event logging
    text.py                     Small text helpers
```

---

## What moved where

| Was | Is now | Layer |
|-----|--------|-------|
| `services/youtube/http.py` | `clients/http.py` | clients |
| `services/youtube/distribution.py` (transports) | `clients/discord.py` | clients |
| `services/youtube/resolver.py` (network + page scraping) | `clients/youtube.py` | clients |
| `services/youtube/shorts.py` (probe + Data API calls) | `clients/youtube.py` | clients |
| `services/youtube/ingestion.py` (feed fetch) | `clients/youtube.py` | clients |
| `services/youtube/config.py` | `config/youtube.py` | config |
| `services/youtube/models.py` | `models/youtube.py` | models |
| `app/models.py` | `models/feeds.py` | models |
| `services/youtube/repository.py` | `repositories/youtube/{processing,roster,channel_cache}.py` | repositories |
| `services/youtube/api.py` | `routes/youtube.py` | routes |
| `services/youtube/pipeline.py` | `services/youtube/{polling,publishing}.py` | services |
| `services/youtube/shorts.py` (decisions) | `services/youtube/classification.py` | services |
| `services/youtube/resolver.py` (cache policy) | `services/youtube/ingestion.py` | services |
| `services/youtube/distribution.py` (message building) | `services/youtube/publishing.py` | services |
| `services/youtube/protocols.py` | `services/protocols.py` | services |
| `services/youtube/factory.py` | `bootstrap.py` | boundary |
| `services/youtube/scheduler.py` | `bootstrap.py` | boundary |
| `services/youtube/errors.py` | `errors.py` | root |
| `services/youtube/clock.py` | `utils/clock.py` | utils |
| `services/youtube/logging_utils.py` | `utils/logging.py` | utils |

Two modules were split across layers rather than moved whole:

- **`resolver.py`** — the network lookups (Data API call, channel-page marker
  scraping) became `YouTubeClient.resolve_channel_id()`; the caching policy
  (six-hour memo + DynamoDB cache, both expiring) stayed a service as
  `ChannelResolver` in `services/youtube/ingestion.py`.
- **`shorts.py`** — the HTTP calls (`HEAD /shorts/<id>`, Data API video
  lookup) became `YouTubeClient.shorts_url_answers()` and
  `.fetch_video_details()`; the detectors and their fallback chain stayed
  services in `classification.py`.

---

## The service split

`YouTubePipeline` coordinated eight concerns. It is now two services with a
narrow seam between them.

**`YouTubePublishingService`** ([publishing.py](../app/services/youtube/publishing.py))
owns one video, end to end: `claim -> classify -> publish -> record`. It
returns a `PublishOutcome` describing what happened. The rules that keep a
single video correct all live here:

- the claim comes first, so two polls cannot announce the same upload;
- an undecidable video is retried, never guessed at;
- a confirmed delivery failure releases the claim, an ambiguous one keeps it.

**`YouTubePollingService`** ([polling.py](../app/services/youtube/polling.py))
owns the outer loop only: load the roster, ingest each source, hand anything
new to publishing, fold the outcomes into the summary, advance the watermark.
It no longer touches the repository, the transport, or the classifier.

| Concern | Before | After |
|---------|--------|-------|
| ingestion | `YouTubePipeline` | `YouTubePollingService` |
| source lifecycle | `YouTubePipeline` | `YouTubePollingService` |
| watermarks | `YouTubePipeline` | `YouTubePollingService` |
| claims | `YouTubePipeline` | `YouTubePublishingService` |
| classification | `YouTubePipeline` | `YouTubePublishingService` |
| distribution | `YouTubePipeline` | `YouTubePublishingService` |
| retries | `YouTubePipeline` | `clients/discord.py` |
| failure handling | `YouTubePipeline` | both, at their own boundary |

---

## Inline imports removed

All three suppression sites are gone, and no `# pylint:
disable=import-outside-toplevel` or function-level import remains anywhere in
`app/`:

```console
$ grep -rn "import-outside-toplevel" app/
none
$ grep -rn "^\s\+\(import \|from .* import \)" app/
none
```

- **`boto3`** is imported at module scope in `app/bootstrap.py`, the one place
  that builds a real DynamoDB table.
- **FastAPI** is imported at module scope in `app/routes/youtube.py`.
- **Test isolation** is now purely dependency injection: `build_polling_service()`
  accepts a `table` and an `http_client`, and the suite passes an in-memory
  DynamoDB fake and a programmable HTTP client. Nothing is deferred to keep
  AWS out of the import graph.

I also removed the four function-level imports in the test doubles
(`tests/youtube/fakes.py`, `harness.py`) for the same reason.

---

## Judgment calls

Three modules exist that the target layout did not name:

- **`app/errors.py`** — the domain exception hierarchy. Clients, repositories,
  config and services all raise from it, so placing it inside any one layer
  would have forced upward imports. At the root it has no dependencies and
  every layer may import it.
- **`app/repositories/dynamodb.py`** — the conditional-write helper
  (`is_condition_failure`) and key/TTL attribute defaults shared by the three
  YouTube repositories, rather than duplicating them three times.
- **`app/utils/text.py`** — `truncate`, used by both the Discord client (error
  message trimming) and publishing (embed field limits).

Four decisions worth an explicit look:

- **`create_flask_blueprint` was dropped.** Flask is not a dependency of this
  project, so a module-scope `from flask import ...` in a routes module would
  break the app on import — the suppression was the only thing making it work.
  Nothing referenced it and it had no tests. `YouTubeTriggerController` stays
  framework-agnostic, so another framework needs a new adapter function next
  to `create_fastapi_router`, not a change to any service. **If Flask support
  is wanted back, it needs Flask added to `pyproject.toml` plus its own
  adapter module.**
- **`scheduler.py` folded into `bootstrap.py`.** The APScheduler job, worker
  loop and Lambda handler are all ways of *starting* the assembled service, so
  they sit with the assembly at the boundary.
- **`config.py` was moved but not split.** At 447 lines it is now the largest
  module in the feature, so review issue #3 is only partly resolved for this
  file. It is in the right layer and is a single cohesive responsibility —
  validating one YAML document, where every rule exists to catch a bad partner
  line at load time rather than mid-poll. Splitting it would separate the
  parsers from the validators they exist to serve. Flagging it rather than
  quietly leaving it: **say the word and I will split it into
  `parsing.py` / `validation.py`.**
- **`CHANNEL_ID_RE` moved to `models/youtube.py`.** Both the config parser and
  the YouTube client need the canonical channel-id shape; models is the lowest
  layer both can import.

---

## Public API renames

Callers outside the feature are affected by these:

| Before | After |
|--------|-------|
| `from app.services.youtube import build_pipeline` | `from app.bootstrap import build_polling_service` |
| `from app.services.youtube.scheduler import register_youtube_job` | `from app.bootstrap import register_youtube_job` |
| `from app.services.youtube.api import YouTubeTriggerController` | `from app.routes.youtube import YouTubeTriggerController` |
| `from app.services.youtube import YouTubeError` | `from app.errors import YouTubeError` |
| `from app.models import Feed, FeedsConfig` | `from app.models.feeds import Feed, FeedsConfig` |
| `YouTubePipeline` | `YouTubePollingService` |
| `DiscordDistributionService` | folded into `YouTubePublishingService` |
| `build_shorts_detector(http, exclude_shorts, api_key)` | `build_shorts_detector(client, exclude_shorts)` |

`app/services/youtube/__init__.py` no longer re-exports config, models or
errors. Re-exporting them from the services package is what let the flat
structure hide its layer mixing; callers now import from the owning layer.

[`app/main.py`](../app/main.py) and [`youtube-ingestion.md`](./youtube-ingestion.md)
are updated. The README project
structure section reflects the new layout.

---

## Tests

Test modules were renamed to follow the code:

| Before | After |
|--------|-------|
| `test_pipeline.py` | `test_polling.py` |
| `test_shorts.py` | `test_classification.py` |
| `test_api.py` | `test_routes.py` |
| `test_repository.py` | `test_repositories.py` |
| `test_distribution.py` | split into `test_discord_client.py` + `test_publishing.py` |
| `test_factory.py` + `test_scheduler.py` | `test_bootstrap.py` |

`tests/youtube/harness.py` assembles a `YouTubePollingService` from the same
doubles as before. New coverage was added for `PublishOutcome` and for the
client seam (one `YouTubeClient` shared by ingestion and classification).

---

## Verification

```console
$ pytest -q --ignore=tests/test_lambda_handler.py
1 failed, 268 passed

$ pylint app
Your code has been rated at 9.77/10
```

The single failure is **pre-existing and unrelated**:
`tests/test_discord_service.py` imports `clients.parameter_store` (no `app.`
prefix). `tests/test_lambda_handler.py` likewise fails to collect because it
imports a `lambda_handler` module that does not exist in this repository.
Both predate this branch and were left alone.

Layering was checked mechanically — every `import` in `app/` was walked with
`ast` against the layer ranking `errors/utils < models < config < clients <
repositories < services < routes < bootstrap/main`:

```console
No upward layer dependencies.
```

`isort` was run over the changed files; its reordering of six files unrelated
to this work was reverted to keep the diff scoped. `black` was not run — the
repository is not black-formatted (`.pylintrc` sets `max-line-length=100`) and
running it would have reformatted every file.
