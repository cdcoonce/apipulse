# apipulse

apipulse is a live-probe quality index for public APIs: it probes one representative endpoint of each public API daily without credentials and publishes reachability, latency, auth and rate-limit signals. The PRD is issue #1.

## Commands

- `apipulse refresh-inventory`
- `apipulse probe`
- `apipulse render`
- `apipulse serve`

Every command accepts `--data-dir PATH`. The data directory defaults to `./data` and can also be set with the `APIPULSE_DATA_DIR` environment variable (the flag wins).

## Project page

What apipulse sends, how often, and how to opt out: to be written.
