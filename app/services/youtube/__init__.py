"""
The Herald's YouTube ingestion and Discord distribution feature.

Subscribes to approved DSB partner YouTube channels, detects newly
published long-form videos, and announces them in #content-corner.

Adding an approved partner is a configuration change -- one line with their
``@handle`` -- never a code change. There is no backfill: a partner
onboarded today gets their next upload announced, not their back catalogue.

The work is split four ways. ``ingestion`` turns a configured source into
content items, ``classification`` decides Short vs long-form,
``publishing`` takes one video from claim to announcement, and ``polling``
walks the sources and moves their watermarks. Everything they depend on
lives in its own layer: HTTP in ``app.clients``, DynamoDB in
``app.repositories``, YAML in ``app.config``, endpoints in ``app.routes``,
and the wiring in ``app.bootstrap``.
"""
