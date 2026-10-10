# apipulse

apipulse is a live-probe quality index for public APIs: it probes one representative endpoint of each public API daily without credentials and publishes reachability, latency, auth and rate-limit signals. The PRD is issue #1.

## Commands

- `apipulse refresh-inventory`
- `apipulse probe`
- `apipulse render`
- `apipulse serve`

Every command accepts `--data-dir PATH`. The data directory defaults to `./data` and can also be set with the `APIPULSE_DATA_DIR` environment variable (the flag wins).

Search uses SQLite FTS5 when the bundled SQLite has it, and falls back to substring matching otherwise.

## Badge

Every API gets an SVG badge at `badge/<id>.svg` on the published site; embed it with:

```markdown
![apipulse](https://<site>/badge/<id>.svg)
```

The badge shows reachable days out of 30 and median latency.

## Project page

`apipulse render` writes the static site to `<data-dir>/site` (or `--out-dir PATH`): `index.html` with client-side search, `apis/<id>.html` per API, `apis.json`, the badges, `project.html` and `removed.html`. The output is deterministic for a given database, so it is diffable.

`project.html` is the project page: it states exactly what a probe sends (a single `GET` per API per day, the `User-Agent` header, no credentials, no body) and how to opt out. `removed.html` lists APIs removed at the maintainer's request, so their absence is not read as death.

To opt out, open an issue at <https://github.com/cdcoonce/apipulse/issues> titled `opt-out: <host>`. The maintainer adds the host to `data/optout.json`; the next `refresh-inventory` stops probing it, records it, and the next `render` moves it from the index to `removed.html`.
