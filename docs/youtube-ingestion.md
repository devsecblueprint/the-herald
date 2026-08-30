# YouTube ingestion & Discord distribution (v1)

The Herald subscribes to approved DSB partner YouTube channels, detects newly
published **long-form** videos, and announces them in **#content-corner**.

Adding an approved partner is a **configuration change** — one line with their
`@handle` — never a code change. There is **no backfill**: a partner onboarded
today gets their next upload announced, not their back catalogue.

This is the first reusable ingestion path for The Herald's longer-term role as
DSB's content intelligence and distribution service. Ingestion and distribution
are separated behind two small protocols ([protocols.py](../app/services/youtube/protocols.py)),
so LinkedIn, podcasts or partner blogs can be added later as new ingestion
services that emit the same `ContentItem`, with the Discord distributor
untouched.

---

## How it works

```
youtube_sources config        @damienjburks
        │
        ▼
ChannelResolver               @handle → UC… (memo + DynamoDB cache)
        │
        ▼
YouTubeIngestionService       channel Atom feed → parse → ContentItem
        │
        ▼
Source watermark              published after onboarding, newest → oldest
        │
        ▼
ProcessingRepository.claim()  conditional PutItem on video id (dedupe)
        │
        ▼
ShortsDetector                long-form only; verdict persisted
        │
        ▼
DiscordDistributionService    format embed → POST to #content-corner
        │
        ▼
ProcessingRepository.mark_distributed()
                              channel id + message id
        │
        ▼
Watermark advanced            past everything resolved this poll
```

`YouTubePipeline` ([pipeline.py](../app/services/youtube/pipeline.py)) is the
only component that knows about all the stages. It is also the only place that
catches exceptions: an unresolvable handle, a failing feed, a rejected Discord
post or a throttled DynamoDB write is recorded as a `SourceFailure` and the
poll continues.

**There is no announcement cap.** If a partner has a busy month, every one of
their long-form videos is announced — flooding #content-corner with partner
content is the point.

### The lifecycle

There are four states in the whole feature, and this is all of them:

1. **A partner is added** to `youtube_sources`.
2. **Their starting point is established** on the next poll — nothing is
   announced on that poll.
3. **New long-form videos are announced** from then on.
4. **A partner is removed** from `youtube_sources` and monitoring stops.

Add them again later and you are back at step 1: a fresh onboarding, a new
starting point. There is no pausing, no per-source enable flag, and nothing to
catch up on.

That falls out of how the state is stored. One DynamoDB item — the **roster** —
holds a watermark for every monitored source, and it is rewritten from the
configuration on every poll. A partner who is no longer in the config is simply
not carried over, so their watermark disappears with them. No pruning job, no
expiry rules to reason about.

Two details keep that single item safe. It carries **no TTL**: it is the only
record of where each partner started, so a long polling outage must not silently
expire it and re-onboard everyone. And the write is guarded on a `revision`
attribute, so a slow poller in another process can't overwrite a newer roster
and drop a partner added in the meantime — a conflict is logged and the next
poll reconciles.

A source's identity in the roster is *how it is written in the config*, kind
included (`handle:@damienjburks`, `id:UC…`, `playlist:UU…`). So `/user/foo` and
`/c/foo` — different YouTube namespaces that can be different channels — never
share state. It also means **rewriting an existing partner's `channel` line**
(swapping an `@handle` for a `UC…` id, say) reads as a removal plus an addition:
they are onboarded fresh, and anything published since the last poll is not
announced. Harmless for a new partner, worth knowing for an established one.
Changing only the *capitalisation* of a handle is safe: handles are normalised
to lower case before they become a roster key.

A source's watermark is set the first time it is polled — to *now*, or to the
newest video already on the channel if that is later, so a host clock running
behind YouTube's can't let back catalogue slip through.

| Situation | What happens |
| --- | --- |
| A partner is added to the config | First poll records the moment and announces **nothing**. Their existing videos are never posted. |
| They upload something | Next poll sees it published after the watermark, announces it, and moves the watermark to that video's publish time. |
| Nothing new | Nothing to do; the watermark is untouched (but rewritten, which refreshes the roster). |
| A post fails | The watermark does **not** move at all. Next poll re-examines the whole batch: the ones that succeeded are stopped by their own records, the one that failed is retried. Nothing is silently skipped. |
| A source's feed fails this poll | It keeps its place on the roster — a transient YouTube outage must not look like a removal. |
| An entry in the roster is unreadable | That one source is dropped and re-onboards; the rest are unaffected. |
| A partner is removed and added back | Fresh onboarding. Anything they published while they were off the list is never announced. |

The watermark advances all-or-nothing: only when every video in a poll reached
a terminal state (announced, skipped as a Short, or confirmed already handled)
does it move, and then straight to the newest. That is deliberately simpler
than tracking which individual videos succeeded, and it is what makes the
failure rows above safe.

Because the watermark only moves forward, a video it has passed can never
become eligible again. That, not a TTL, is what guarantees nothing is
announced twice — which in turn means the per-video DynamoDB records are free
to be what they should be: a dedupe guard for overlapping polls, and an audit
trail of which Discord message announced which video. They expire after 35
days purely as a retention choice.

---

## Configuration

### Source list

[`app/static/youtube_sources.yaml`](../app/static/youtube_sources.yaml) (path
from `HERALD_YOUTUBE_CONFIG_PATH`, or pass the already-parsed mapping straight
to `load_config()` if The Herald ever keeps all configuration in one object):

```yaml
youtube:
  enabled: true
  poll_interval_minutes: 15
  exclude_shorts: true

  # Every announcement goes here.
  discord_channel_name: content-corner
  discord_channel_id: "123456789012345678"

  youtube_sources:
    - name: Damien Burks
      relationship: COMMUNITY_PARTNER
      channel: "@damienjburks"
      categories:
        - cloud-security
        - devsecops
```

To stop monitoring a partner, delete their entry. There is no `enabled` flag —
a source is either configured or it isn't.

| Field | Required | Notes |
| --- | --- | --- |
| `name` | yes | Partner/source name used for attribution in Discord. |
| `relationship` | yes | `COMMUNITY_PARTNER`, `DSB`, `MEMBER`, `SPONSOR` — normalised to upper case; unknown values are accepted and title-cased for display. |
| `channel` | yes | The channel's `@handle`, any `youtube.com` channel URL, or a canonical `UC…` id. See below. |
| `categories` | no (`[]`) | Tags carried through to the content object for future routing. |
| `playlist_id` | no | Poll a specific playlist (`UU…`/`PL…`) instead of the channel feed. |
| `attribution` | no | Override the channel name shown on the embed's author line. |

`channel_id` is accepted as a legacy alias for `channel`; setting both is an
error.

Configuration is validated at load time — unknown fields, duplicate channels
(case-insensitively), an unrecognisable channel reference, and a missing or
non-numeric `discord_channel_id` all raise `ConfigurationError` at startup
rather than mid-poll. In this deployment `discord_channel_id` comes from
`HERALD_DISCORD_CHANNEL_ID` so it can differ per environment; a *disabled*
feature does not need one at all.

### Identifying a channel

You should never have to go hunting for an `externalId`. All of these work:

| Written in config | Kind |
| --- | --- |
| `"@damienjburks"` | handle |
| `https://www.youtube.com/@damienjburks` | handle |
| `https://www.youtube.com/channel/UCxxxxxxxxxxxxxxxxxxxxxx` | id |
| `UCxxxxxxxxxxxxxxxxxxxxxx` | id |
| `https://www.youtube.com/user/somebody` | legacy user |
| `https://www.youtube.com/c/SomeVanityName` | legacy vanity |

A bare name (`damienjburks`, no `@`) is rejected as ambiguous, and a `UC…` id
must match exactly — a partial match would silently truncate a typo into a
valid-looking id and poll the wrong channel forever.

Anything that isn't already a `UC…` id is resolved **at poll time**, not at
startup, so a renamed handle or an unreachable YouTube is a source-level
failure the other partners survive — not a crash on boot. Resolution uses the
Data API when `HERALD_YOUTUBE_API_KEY` is set, and otherwise reads the public
channel page, accepting any of four independent markers (`externalId`, the
canonical link, the `identifier` meta tag, `channelId`) so one markup change
does not break it.

Each resolved id is cached twice: in memory (6-hour TTL) and in the same
DynamoDB table under `youtube-channel#<kind>:<handle>` (30-day TTL), so
restarts and new containers don't re-resolve. **Both** layers expire, because a
handle can be released and taken over by a different channel — an immortal
in-process memo would keep announcing the new owner's videos under the old
partner's name. The cache key includes the reference *kind*, since
`/user/name` and `/c/name` are different namespaces that can be different
channels.

### Shorts

v1 announces **long-form videos only**. The Atom feed carries no duration and
no format flag, so the distinction comes from three signals, cheapest first:

1. **`#shorts` tag** in the title or description — free, no network, but only
   used by the offline `HeuristicShortsDetector`. It is deliberately *not* a
   shortcut for the other two: a long-form video whose description says "clips
   are on my #shorts channel" would be misclassified, and a false positive here
   is permanent.
2. **Shorts URL probe** (default) — a `HEAD` to `youtube.com/shorts/<id>`
   without following redirects. A real Short answers `200`; a long-form video
   is redirected to `/watch?v=`. No API key, no quota. Authoritative.
3. **Data API duration** — when `HERALD_YOUTUBE_API_KEY` is set, compares
   `contentDetails.duration` against 180s (a video of exactly 180 seconds is
   still a Short), and also skips live broadcasts and unfinished premieres.
   When the API reports no usable duration (`P0D`, which it returns for
   streams), it falls back to the probe rather than reading the zero as "under
   180 seconds".

Classification runs **after** the DynamoDB claim, and the verdict is written to
the record as `status: SKIPPED, skip_reason: SHORT` (or `LIVE` / `PREMIERE`).
Combined with the watermark moving past it, each video is classified exactly
once.

If a video can't be classified (probe timeout, unexpected status), the claim is
released and the video is retried next poll. Guessing would mean either a Short
in #content-corner or a partner's real video silently dropped — neither is
acceptable, so the pipeline waits rather than guesses.

Set `exclude_shorts: false` (or `HERALD_YOUTUBE_EXCLUDE_SHORTS=false`) to
announce everything and skip classification entirely.

### Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `HERALD_YOUTUBE_CONFIG_PATH` | `app/static/youtube_sources.yaml` | YAML source config path. |
| `HERALD_YOUTUBE_ENABLED` | `true` | Master kill switch for the feature. |
| `HERALD_YOUTUBE_POLL_INTERVAL_MINUTES` | `15` | Scheduled polling cadence. |
| `HERALD_DISCORD_CHANNEL_ID` | — | Numeric id of the announcement channel (#content-corner). |
| `HERALD_DISCORD_CHANNEL_NAME` | `content-corner` | Display name only, used in logs and `/health`. |
| `HERALD_YOUTUBE_EXCLUDE_SHORTS` | `true` | Set `false` to announce Shorts too. |
| `HERALD_YOUTUBE_API_KEY` | — | Optional YouTube Data API key. When set, handle resolution and Shorts detection use the API instead of page fetches. |
| `HERALD_DEDUP_TABLE_NAME` | — | DynamoDB table (see below). |
| `HERALD_DEDUP_KEY_ATTRIBUTE` | `content_id` | Partition key attribute name. |
| `HERALD_DEDUP_TTL_ATTRIBUTE` | `ttl` | TTL attribute name. |
| `HERALD_DISCORD_BOT_TOKEN` | — | Bot token. When unset, the token is read from Parameter Store (`/the-herald/prod/discord-token`), which is what the deployed service does. |
| `HERALD_DISCORD_WEBHOOKS` | — | JSON `{"<channel id>": "<webhook url>"}` — used only when no bot token is available. |
| `HERALD_YOUTUBE_MESSAGE_STYLE` | `embed` | `embed` or `plain`. |
| `HERALD_YOUTUBE_POST_DELAY_SECONDS` | `0` | Pause between consecutive posts, if you want to be gentle with Discord's rate limit. |

---

## DynamoDB

The feature uses its own table, created by
[`terraform/main.tf`](../terraform/main.tf) as `aws_dynamodb_table.herald_dedup`.
It is separate from `the-herald-reminders`, whose partition key is
`reminder_key` and cannot hold these records.

```
PartitionKey: content_id (S)     # "youtube#<video id>"
TTL attribute: ttl               # enabled, epoch seconds
Billing: PAY_PER_REQUEST
```

If you ever create the table by hand, enable TTL once:

```bash
aws dynamodb update-time-to-live \
  --table-name the-herald-dedup \
  --time-to-live-specification "Enabled=true, AttributeName=ttl"
```

Stored record:

```json
{
  "content_id": "youtube#abc123",
  "platform": "youtube",
  "video_id": "abc123",
  "youtube_channel_id": "UCxxxxxxxxxxxxxxxxxxxxxx",
  "source_name": "Damien Burks",
  "relationship": "COMMUNITY_PARTNER",
  "title": "Threat modelling for platform teams",
  "url": "https://www.youtube.com/watch?v=abc123",
  "categories": ["cloud-security"],
  "published_at": "2026-08-17T12:00:00Z",
  "first_seen_at": "2026-08-19T14:59:58Z",
  "first_seen_epoch": 1787151598,
  "discord_channel_id": "123456789012345678",
  "discord_message_id": "987654321098765432",
  "posted_at": "2026-08-19T15:00:00Z",
  "status": "POSTED",
  "ttl": 1790175600
}
```

`status` is one of `PENDING` (claimed, nothing sent yet), `POSTING` (a Discord
request has been issued), `POSTED` (announced, ids recorded) or `SKIPPED` (a
Short — `skip_reason: SHORT`).

The same table holds two other kinds of item. The roster — one item, every
monitored source:

```json
{
  "content_id": "youtube-sources",
  "record_type": "source_roster",
  "watermarks": {
    "handle:@damienjburks": "2026-08-19T15:00:00Z",
    "handle:@thedsbcommunity": "2026-08-18T09:30:00Z"
  },
  "updated_at": "2026-08-19T15:00:00Z",
  "revision": 412
}
```

No `ttl` — this item must never expire.

and resolved channel references:

```json
{
  "content_id": "youtube-channel#handle:@damienjburks",
  "record_type": "channel_reference",
  "reference": "handle:@damienjburks",
  "channel_id": "UCxxxxxxxxxxxxxxxxxxxxxx",
  "resolved_at": "2026-08-19T14:59:58Z",
  "ttl": 1792767600
}
```

### Claim → posting → posted

Announcing is two operations (post to Discord, then record the message id), and
polls can overlap. So the record moves through three states:

1. **`claim()`** → `PENDING`. Conditional `PutItem` guarded by
   `attribute_not_exists`. The loser of a race gets
   `ConditionalCheckFailedException` and skips the video; the winner owns the
   announcement.
2. **`mark_posting()`** → `POSTING`, issued immediately *before* the Discord
   request.
3. **`mark_distributed()`** → `POSTED`, with the Discord channel id, message id
   and post time, and the TTL re-stamped from post time.

`POSTING` is what makes the design safe. A crash or a failed state write after
a successful post leaves the record in `POSTING`, and a `POSTING` record is
never *reclaimed* by another poll — so the video can never be announced twice.
Only the owner of a claim deletes it, and only when the outcome is known:
nothing was sent, or Discord confirmed a rejection.

Failure handling:

| Failure | Behaviour |
| --- | --- |
| A configured `@handle` can't be resolved | Logged as an `ingestion` failure for that source; the other partners are polled normally. |
| The roster can't be read at all | Logged as a `roster` failure and the poll stops before anything is posted — without watermarks there is no safe way to decide what is new. A single unparseable *entry* is not this: it is skipped and that source re-onboards. |
| The roster can't be written, or another poll wrote it first | Logged as a `roster` failure. Announcements already made stand and are not repeated (their own records see to that); an onboarding that didn't stick simply repeats next poll, having announced nothing. |
| A video can't be classified as Short/long-form | Claim released, logged as `classification`; retried next poll. |
| Discord **rejects** the post (4xx/5xx, exhausted retries) | `release()` deletes the claim → retried next poll. |
| Discord delivery is **ambiguous** (`AmbiguousDeliveryError`) — connection dropped, response body unreadable or unparseable, or a 2xx with no message id | Claim is **kept** in `POSTING`, logged as `distribution_ambiguous`. The message may be live; a missing record is cheaper than a duplicate announcement. |
| State update fails after a successful post | Claim kept in `POSTING`, logged as `state_update`. |
| Process dies between claim and the Discord call | The `PENDING` claim is reclaimable after `stale_claim_minutes` (60). |

Both "kept" cases leave a record with no `discord_message_id`. Alert on
`youtube.video.distribution_ambiguous` and `youtube.video.state_update_failed`;
they need a human to confirm what actually landed in Discord.

---

## Scheduled job

The poll is registered by `configure_youtube()` in
[`app/main.py`](../app/main.py) during application startup. To wire it into
another host:

```python
from app.services.youtube import build_pipeline
from app.services.youtube.scheduler import register_youtube_job

pipeline = build_pipeline()
register_youtube_job(scheduler, pipeline)   # APScheduler; interval from config
```

Alternatives: `run_forever(pipeline)` for a plain worker loop, or
`make_lambda_handler(build_pipeline)` behind an EventBridge rule.

The job registers with `max_instances=1` and `coalesce=True` so a slow poll
cannot stack up behind itself. Independently of the scheduler,
`YouTubePipeline.run()` holds a non-blocking lock and returns
`PollResult(skipped=True)` if a poll is already in flight — so the scheduled job
and the manual trigger can never run concurrently either.

## Manual trigger & health

Both endpoints are already on the FastAPI app:

```
POST /trigger/youtube    → 200 ok | 207 completed_with_errors | 409 already_running
GET  /health/youtube     → configuration, configured sources, last run summary
```

They answer `503 unavailable` if the feature was never configured. For another
host, `create_fastapi_router(controller)` and `create_flask_blueprint(controller)`
in [`api.py`](../app/services/youtube/api.py) expose the same two routes.

`POST /trigger/youtube` response body:

```json
{
  "status": "ok",
  "started_at": "2026-08-27T12:00:00Z",
  "finished_at": "2026-08-27T12:00:03Z",
  "duration_ms": 3120,
  "sources_configured": 4,
  "sources_checked": 4,
  "sources_onboarded": 0,
  "videos_in_feeds": 47,
  "new_videos": 3,
  "shorts_skipped": 1,
  "announcements_published": 2,
  "duplicates_skipped": 0,
  "ttl_days": 35,
  "discord_channel_id": "123456789012345678",
  "failures": [],
  "announced": [
    {
      "video_id": "abc123",
      "source_name": "Damien Burks",
      "youtube_channel_id": "UCxxxxxxxxxxxxxxxxxxxxxx",
      "published_at": "2026-08-26T09:00:00Z",
      "discord_channel_id": "123456789012345678",
      "discord_message_id": "987654321098765432"
    }
  ]
}
```

## Logging

One JSON object per line (`app.services.youtube.logging_utils.configure_json_logging()`
if the host has no structured logger of its own; The Herald configures its own
handler in `app.main`):

| Event | Fields |
| --- | --- |
| `youtube.poll.started` | `sources_configured` |
| `youtube.channel.resolved` | `reference`, `channel_id` (an @handle was looked up) |
| `youtube.source.onboarded` | `source_name`, `watermark`, `videos_in_feed` (first sight — nothing announced) |
| `youtube.source.evaluated` | `source_name`, `videos_in_feed`, `new_videos`, `watermark` |
| `youtube.source.watermark_advanced` | `source_name`, `watermark` |
| `youtube.roster.sources_removed` | `sources` (dropped from the config; their state is gone) |
| `youtube.roster.unreadable_entry` | `source_key`, `value` (skipped; that source re-onboards) |
| `youtube.roster.write_conflict` | `expected_revision` (another poll wrote first; next poll reconciles) |
| `youtube.source.fetched` | `source_name`, `source_key`, `channel_id`, `videos_in_feed` |
| `youtube.source.fetch_failed` | `source_name`, `source_key`, `error` (includes handle-resolution failures) |
| `youtube.video.claimed` | `video_id`, `discord_channel_id`, `published_at`, `ttl` |
| `youtube.video.duplicate_skipped` | `video_id` (debug level) |
| `youtube.discord.posted` | `content_id`, `discord_channel_id`, `discord_message_id` |
| `youtube.video.distributed` | `video_id`, `discord_message_id`, `posted_at`, `ttl` |
| `youtube.video.classified` | `video_id`, `is_short`, `detector` (debug level) |
| `youtube.video.skipped` | `video_id`, `reason` (a Short, not announced) |
| `youtube.video.classification_failed` | `video_id`, `error` (undecidable — retried) |
| `youtube.video.mark_posting_failed` | `video_id`, `error` (the PENDING→POSTING write failed, so the record is no longer ours — nothing was sent and nothing was deleted) |
| `youtube.video.distribution_failed` | `video_id`, `error` (confirmed failure — will retry) |
| `youtube.video.distribution_ambiguous` | `video_id`, `error` (**alert on this** — may be live in Discord) |
| `youtube.video.state_update_failed` | `video_id`, `discord_message_id`, `error` (**alert on this**) |
| `youtube.poll.already_running` | — (a poll was skipped because one was in flight) |
| `youtube.poll.completed` | every counter, plus `failed_sources` and `duration_ms` |

CloudWatch Logs Insights:

```
fields @timestamp, sources_checked, sources_onboarded, new_videos,
       shorts_skipped, announcements_published, source_failures
| filter event = "youtube.poll.completed"
| sort @timestamp desc
```

---

## The Discord announcement

Deterministic and template-driven — no AI summarisation in v1.

* **Content line:** `📺 New from **<source name>** on YouTube`
* **Embed:** video title (linked), author line with the publishing channel name
  and a link to the channel, description (≤400 chars), thumbnail, publish
  timestamp, a `Watch` field with the raw URL, and a footer reading
  `<source name> • <relationship> • YouTube`. Colour is keyed to relationship.
* `allowed_mentions` is empty — announcements never ping a channel.

The raw URL is kept inside the embed rather than the content line so Discord
does not add a second, duplicate link preview. Set
`HERALD_YOUTUBE_MESSAGE_STYLE=plain` to post the bare URL instead and let
Discord's native YouTube player card do the work.

---

## Onboarding a new partner

1. Add an entry to `youtube_sources` in
   [`app/static/youtube_sources.yaml`](../app/static/youtube_sources.yaml) with
   the partner's `@handle`.
2. Deploy (`invoke push-and-deploy`).
3. `POST /trigger/youtube`. The response shows `sources_onboarded: 1` and no
   announcements — that first poll just marks the starting line.

From then on, every long-form video they publish lands in #content-corner
within the poll interval. No application code changes, and no hunting for a
channel id.

**Removing a partner** is the same edit in reverse: delete their entry and
deploy. The next poll drops them from the roster and stops fetching their feed.
If they are ever added back, they are onboarded fresh — nothing published in
between is announced.

---

## Development

```bash
pytest tests/youtube -q      # 257 tests, no network, no AWS
```

Tests cover source parsing and validation (every accepted channel-reference
form, and malformed ids that must not be silently truncated), handle resolution
and its two cache layers, Shorts detection across all three signals and the
exact 180-second boundary, onboarding (a new partner's back catalogue is never
posted), the add/remove/re-add lifecycle, roster isolation between namespaces
that share a name, stale-writer protection, watermark advance including the
cases where one failed post, a stranded claim, or two videos sharing a publish
timestamp must hold it back, clock-skew clamping at onboarding, roster refresh
for a quiet source, future-dated premieres, newest-first ordering,
deduplication, the `PENDING → POSTING → POSTED/SKIPPED` state machine and its
stale-reclaim rules, ambiguous-delivery handling, overlapping-run protection,
the YouTube↔Discord mapping, message generation, and per-source failure
isolation at each of the ingestion, watermark, classification, deduplication,
distribution and state-update stages.

Every external dependency is injected, so the suite runs against an in-memory
DynamoDB fake (which does evaluate the real condition expressions — see
[`tests/youtube/condition.py`](../tests/youtube/condition.py)), canned Atom
feeds, and a recording Discord transport.

---

## Scope

**In (v1):** YouTube only, config-driven sources identified by `@handle`,
add/remove lifecycle with no pausing, forward-only from onboarding (no
backfill), long-form only (Shorts filtered), DynamoDB dedupe and audit trail,
announcement to a single configurable Discord channel, scheduled poll, manual
trigger.

**Out (v1):** LinkedIn / TikTok / Instagram / podcasts / partner blogs, member
submissions, AI summaries, relevance scoring, DSB website feed, social
cross-posting.

### Adding a platform later

Implement `IngestionService` (return `SourceFetchResult`s carrying
`ContentItem`s) and give the item a new `platform` value. Deduplication keys are
`"<platform>#<content id>"`, so a shared table stays collision-free. The
pipeline, repository and Discord distributor need no changes.
