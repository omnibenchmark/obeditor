# OBEditor — design principles

A browser-only playground for editing OmniBenchmark `benchmark.yaml` files.
This file captures the load-bearing decisions so future changes stay aligned.

## Architecture

- **Zero-build static site.** Three files: `index.html`, `worker.js`,
  `releases.yaml`. No bundler, no npm, no Node, no server-side anything.
  Serve over HTTP (`python3 -m http.server`) and the page works.
- **Two-thread split.** UI in main thread; Pyodide + omnibenchmark in
  `worker.js`. Never block the main thread with Python work, and never
  call DOM APIs from the worker.
- **All Python lives in `PREVIEW_PY`** — a JS string array joined with
  newlines. Keep it self-contained (no `runPython` strings scattered
  around). Pass data across the boundary via `pyodide.globals.set` and
  return JSON-stringified payloads for nested structures.
- **No backend.** Selection blobs and filtered YAMLs are designed to be
  consumed by a separate CLI; the editor never phones home.

## omnibenchmark loading

- Wheels are fetched from PyPI (`files.pythonhosted.org` serves CORS) and
  installed via Pyodide's zipimport (`sys.path.insert(0, "/whl")`), not
  micropip. micropip resolves transitive deps; we don't want that — we
  mock the heavy ones.
- **Mock list is curated, not exhaustive.** `worker.js` mocks every dep
  omnibenchmark *imports at module load* but we don't *call* (matplotlib,
  snakemake, dulwich, rich, click, …). When a new omnibenchmark version
  adds an import we don't use, add it to the mock list — don't try to
  load the real package.
- **Rule of thumb:** we use `omnibenchmark.model.benchmark.Benchmark` and
  `omnibenchmark.model.params.Params`. That's it. Everything else is
  reimplemented in `_generate*` helpers because the real Snakefile
  generator wants the full execution stack.
- The pydantic `Benchmark` schema is **open** (no `extra="forbid"`).
  Unknown top-level keys (`canonical-url`, `subset-of`, `derived-from`)
  pass through validation. Don't add shims to make it strict.

## Versioning the toolchain

- `releases.yaml` is the catalog. `default:` selects the boot version;
  entries listed (in order) appear in the dropdown.
- Phase out a version with `hidden: true` (soft) or by deleting the
  entry (hard). The in-repo dev wheel is a relative URL.
- **Always bump `worker.js?v=N` when `worker.js` changes.** Browsers
  cache workers aggressively; without the cache-buster, users get a
  stale worker and silent missing-field bugs (e.g. an empty wizard).

## Output preservation (filter step)

- Filtering uses raw `yaml.safe_load` → mutate dict → `yaml.safe_dump`,
  **not** a pydantic round-trip. This preserves unknown top-level fields
  (`canonical-url`, `subset-of`, `derived-from`) and avoids re-emitting
  defaults the user didn't write.
- Re-serialization is clean (no comment preservation). That's a deliberate
  call — comment-preserving YAML round-trips are fragile.
- Header keys are reordered for readability: `id, name, description,
  benchmarker, version, subset-of, derived-from, …rest`.
- Parameter pruning (`first`, custom) **normalizes** legacy `values:`
  parameters into modern `params:` form (single-valued mappings). Modules
  using `all` are untouched. Don't try to preserve legacy format through
  filtering — the canonical-out / legacy-in mismatch isn't worth it.
- Combo-level Custom selection is gated by a hard cap (`COMBO_HARD_LIMIT`
  in `index.html`). Above the cap the UI forces All/First. Don't lift
  the cap without a virtualized list — rendering 10k checkboxes locks
  the main thread.

## Provenance vocabulary

Three subtly different concepts — keep them straight:

| Field           | Meaning                                                 |
|-----------------|---------------------------------------------------------|
| `canonical-url` | The benchmark's own published address (self-claim).     |
| `derived-from`  | Where the editor *fetched* the YAML from this session.  |
| `subset-of`     | Lineage: `{sha256, url?}` of the parent this was filtered from. |

- `subset-of` is content-addressed: SHA-256 of the parent's YAML text
  after normalization (per-line rstrip + trailing newline). Hash is the
  source of truth; URL is a hint.
- `derived-from` is session state. It survives edits — the diverging
  hash already signals modification — but the user can clear it via the
  source-pill `✕`.
- A filtered file is itself a benchmark and can be filtered again. The
  hash chain forms a phylogeny.

## Selection blob

- Compact JSON → gzip → urlsafe base64 (no padding). Mirror this exactly
  in any CLI consumer.
- Schema today (v2):
  ```
  {"v":2,"parent":{"sha256":"…","url":"…","derived_from":"…"},
   "picks":{"<stage_id>":{"<mod_id>": "all" | "first" | ["<combo_hash>", …]}}}
  ```
  Module spec semantics:
  - `"all"`     — keep `parameters:` block as written (default).
  - `"first"`   — keep first combo of first set; emitted as a single
                  single-valued `parameters:` mapping.
  - `string[]`  — keep specific combos by their `Params.hash_short()`;
                  each emitted as one single-valued `parameters:` mapping.
- A module without `parameters:` always uses `"all"` implicitly; the UI
  hides the mode selector for it.
- Combos use the 8-char short hash (`Params.hash_short()`) as identity.
  Hashes are computed from canonical JSON of the combo's `{key:value}`
  dict — stable across runs.
- **Bump `v` for every breaking schema change.** v1 (modules-only,
  `picks: {stage_id: [mod_id…]}`) is retired; we have no v1 consumers to
  preserve.
- Keep JSON canonical: `sort_keys=True`, `separators=(',',':')`,
  `gzip mtime=0`. Same input must produce the same blob bytes.

## Trust model (URL loader)

- GitHub only. `github.com/.../blob/...` and
  `raw.githubusercontent.com/...` are accepted; everything else is
  rejected at parse time.
- Default-trusted orgs (`omnibenchmark`, `omni-scrna`) are hardcoded and
  cannot be removed. Additional trust is per-browser, persisted in
  `localStorage["obeditor.trustedOrgs"]`. Comparison is case-insensitive.
- The trust prompt is a deliberate friction point — don't bypass it for
  "convenience" flows.

## UI conventions

- Right pane has tabs (Check / Snakefile / run.sh / Filter). Check and
  Filter are custom DOM panes; Snakefile and run.sh share one Monaco
  editor with two models (cheap + avoids re-layout flicker).
- The Filter wizard is a stage-stepper; picks are subset-not-singleton
  (checkboxes, ≥1 to advance). Result step renders below the stepper,
  not as a modal — keeps the YAML editor visible.
- Picks reconcile across YAML edits: orphaned stage/module IDs are
  silently dropped, stale results are invalidated when the parent hash
  changes.

## Things to *not* do

- Don't introduce a build step. If a feature needs npm/bundling,
  reconsider the feature.
- Don't use micropip for omnibenchmark — keep zipimport + mocks.
- Don't let the worker auto-update silently — bump `?v=N`.
- Don't preserve comments through filtering. Don't promise round-trip
  fidelity beyond "valid YAML in, valid YAML out".
- Don't add fields to the selection blob without bumping `v`.
- Don't add a "trust everything" toggle. Trust is per-org, on purpose.
