# Session Knowledge

Accumulated working knowledge for this repo. Read before making architectural decisions. Update when you learn something non-obvious; correct entries that turn out to be wrong. Dates matter, check staleness.

## What this repo is (2026-06-12)

Two independent ways to get Gabb Wireless (Smartcom FiLIP) kid-watch data into Home Assistant, in one repo:

1. **Native HA integration** at `custom_components/gabb_wireless/`, distributed via HACS. Self-contained async aiohttp client in `api.py` (zero pip requirements, that's deliberate, keep it that way). Config flow + reauth + options (poll interval), DataUpdateCoordinator, platforms: device_tracker, sensor, binary_sensor. Tests in `tests_integration/` (need `pytest-homeassistant-custom-component`, run with `--asyncio-mode=auto`).
2. **Docker MQTT publisher** (`gabb_mqtt_publisher.py` + trimmed `gabb/` package), the original product. Device-based MQTT discovery, availability + LWT, `migrate_discovery` for pre-0.3.0 upgraders. Tests in `tests/` (plain pytest, `.venv/bin/pytest tests/ -q`).

Users run one or the other, not both (duplicate devices otherwise). The Gabb API is undocumented; auth protocol notes live in `gabb/__init__.py` and `custom_components/gabb_wireless/api.py` (login `v2/sso/gabb` with appBuild "1.28 (966)", refresh `v2/token/refresh`, FiLIP-iOS headers required).

## Open follow-ups (as of 2026-06-12)

- **hacs/default PR #8441 is pending** (https://github.com/hacs/default/pull/8441), submitted from the `jaycollett/default` fork, branch `add-gabbwireless-mqtt`. When it merges:
  1. Close repo issue #21 (a comment already promises this).
  2. Update README: in "How to install", drop the custom-repository steps (1-3) since the integration will be searchable in HACS directly; keep the rest.
  3. Do NOT request reviews on the PR; the HACS template threatens closure for that.
- Nothing else is blocked. The brands repo is NOT involved (see gotchas).

## Release flow (do not break this)

- `custom_components/gabb_wireless/manifest.json` `version` is the single source of truth. Bump it, update CHANGELOG.md, push to master.
- `release.yml` creates GitHub release `v<version>` AND calls `BuildAndPublish.yml` via `workflow_call` in the same run.
- **Gotcha:** releases created with `GITHUB_TOKEN` do not emit events, so a `release: published` trigger never fires for automated releases. That's why release.yml calls the Docker workflow directly. Don't "simplify" this back to event chaining; it silently breaks (it did, v0.4.0's image had to be backfilled via the `workflow_dispatch` trigger that was added for exactly that).
- `ci.yml` blocks PRs whose manifest version wasn't bumped past the latest tag. Tags v0.4.0+ are v-prefixed; older ones (0.1.0..0.3.0) are not, the check handles both.
- Publisher `__version__` in `gabb_mqtt_publisher.py` is versioned separately-ish; it was 0.4.0 when manifest went 0.4.1 (icon-only release). Keep them aligned when publisher code actually changes.

## Gotchas and decisions

- **Brand icon: NEVER use Gabb's logo/wordmark/trade dress.** Owner decision 2026-06-12, non-negotiable. Current icon is original artwork (navy watch + map pin on teal), 1024px master at `assets/icon_1024.png`, shipped copies at `custom_components/gabb_wireless/brand/icon.png` (256) and `icon@2x.png` (512).
- **home-assistant/brands no longer accepts custom integration PRs** (Brands Proxy API, Feb 2026: https://developers.home-assistant.io/blog/2026/02/24/brands-proxy-api). Icons are bundled in `brand/` inside the integration; HA 2026.3+ serves them locally. HACS default checks accept the bundled dir. A brands-fork branch was created and then deleted during this work; nothing lives there.
- **Entity-id invariants:** MQTT publisher `unique_id`s (`gabb_device_<id>_<key>`, `..._tracker`, `..._last_updated`) and state topics are byte-stable since 0.1.x, preserved through the 0.3.0 device-discovery migration. The HACS integration intentionally uses different identifiers (`gabb_wireless` domain) and is a separate device in HA; no migration path between the two is promised.
- **migrate_discovery ordering** (publisher): signal `{"migrate_discovery": true}` on legacy topics, THEN publish device-based discovery, THEN clear legacy topics with empty retained payloads. Clearing first loses user entity customizations.
- **`object_id` is dead** (removed HA 2026.4); use `default_entity_id` in MQTT discovery payloads. The publisher already does.
- **OptionsFlowWithReload requires HA >= 2025.8**; hacs.json pins minimum 2025.1.0, so the integration uses a listener-based reload instead. If the minimum HA ever rises past 2025.8, this can be simplified.
- **strings.json and translations/en.json must stay byte-identical**; ci.yml diffs them.
- Integration test venv used during development: `/tmp/gabb_ha_test_venv` (ephemeral, rebuild with `pip install pytest-homeassistant-custom-component`).
- The publisher module imports with no env vars set (config resolution happens in `main()` via `Config.from_env()`). Keep it that way; tests rely on it.

## Verification commands

```sh
.venv/bin/pytest tests/ -q                                  # publisher (44 tests)
pytest tests_integration --asyncio-mode=auto                # integration (needs pytest-homeassistant-custom-component)
ruff check gabb gabb_mqtt_publisher.py tests custom_components/gabb_wireless
gh run list --limit 5                                       # Validate + CI + Release should all be green
```
