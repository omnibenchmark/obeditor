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
  `omnibenchmark.model.params.Params`. On wheels whose expansion API
  matches (`_has_ob_generator()`: omnibenchmark main, api 0.8) the
  Snakefile tab and rule counts come from omnibenchmark's own
  `core._expand` + `backend.snakemake.SnakemakeGenerator`, with module
  resolution stubbed from the plan (`_stub_module`: no clone, so
  entrypoints show the plan's key). That covers gather, `requires`/
  `provides` and capabilities with no reimplementation. Older wheels keep
  the `_generate*` helpers. `tests/check_ob_generator.py WHEEL PLAN
  [FILTER N]` runs the worker's Python against a wheel the way the browser
  does; run it from a bare Python (pydantic + PyYAML only) so a missing
  mock fails there and not in the browser.
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
- Metric collectors whose inputs only come from pruned stages are dropped,
  mirroring `omnibenchmark.core._prune.filter_collectors_by_stages`. A
  filter that keeps no stage is an error, not an empty benchmark.
- `tests/check_filter.py` runs the worker's Python against the real
  `omnibenchmark.filter` (needs a Python with omnibenchmark ≥ 0.7). Run it
  whenever `_prune` or the blob changes.

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
- The selection blob's `parent.sha256` is a *different* hash:
  `Benchmark.summary_hash()` (execution-only fields), because that's what
  `ob run --filter` compares against. Don't unify them — administrative
  edits to the parent must not invalidate a slice on the CLI side.
- `derived-from` is session state. It survives edits — the diverging
  hash already signals modification — but the user can clear it via the
  source-pill `✕`.
- A filtered file is itself a benchmark and can be filtered again. The
  hash chain forms a phylogeny.

## Selection blob

- Compact JSON → gzip → urlsafe base64 (no padding). The consumer is
  `omnibenchmark.filter` (`ob run --filter`); the editor mirrors it, not
  the other way round. `v` must equal `omnibenchmark.filter.BLOB_VERSION`.
- Schema today (v3):
  ```
  {"v":3,"parent":{"sha256":"<summary_hash>","url":"…","derived_from":"…"},
   "picks":{"<stage_id>":{"<mod_id>" | "*": "all" | "first" | ["<combo_hash>", …]}}}
  ```
  Module spec semantics:
  - `"all"`     — keep `parameters:` block as written (default).
  - `"first"`   — keep first combo of first set; emitted as a single
                  single-valued `parameters:` mapping.
  - `string[]`  — keep specific combos by their `Params.hash_short()`;
                  each emitted as one single-valued `parameters:` mapping.
  - `"*"` as module key — every module in the stage; cannot be mixed with
    explicit module ids. Explicit key wins over `"*"` (`module_spec`).
- A stage absent from `picks` is pruned. A module without `parameters:`
  always uses `"all"` implicitly.
- The same mapping, written as YAML under a `picks:` key, is the
  hand-written form (`picks.obfilter.yaml`). The Filter tab edits that
  form; a pasted blob is unpacked into it.
- **`until: <stage>`** is an editor-side grammar extension (ob has it as
  the `--until` flag, not in the filter file yet). It cuts the DAG to that
  stage plus its transitive ancestors (same walk as
  `omnibenchmark.core._prune.apply_until_filter`, over `stage_adjacency`).
  With `until`, a stage inside the cut that picks don't mention keeps
  every module — `until` sets the extent, picks narrow within it; `until:`
  alone keeps every surviving stage. Without `until`, picks alone decide
  (ob's rule: an unmentioned stage is pruned). The
  exported picks/blob carry the **already-cut** picks, so today's
  `ob run --filter` yields the same slice without knowing `until`; the key
  rides along in the YAML/blob (ignored by ob) so intent survives a round
  trip. When ob grows the key, nothing here needs to change.
- Combos use the 8-char short hash (`Params.hash_short()`) as identity.
  Hashes are computed from canonical JSON of the combo's `{key:value}`
  dict — stable across runs.
- **Bump `v` for every breaking schema change**, in lockstep with
  omnibenchmark. v1 (modules-only) and v2 (text-hash parent, no `*`) are
  retired.
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

- Left pane has two tabs sharing one Monaco editor (two models):
  `benchmark.yaml` and `Filter`. The Filter tab is plain text in the
  `ob run --filter` YAML format, with a presets dropdown, Apply
  (Ctrl+Enter), Clear and Template. The stage-stepper wizard was removed
  (too long for real benchmarks); the Template button lists every
  module and combo hash as comments instead.
- Presets are listed from a `filters/` folder next to the loaded
  `benchmark.yaml` via the GitHub contents API (same trusted repo; raw
  URLs are rebuilt from the repo coordinates, never taken from the API
  response). No folder → the dropdown stays disabled; Apply still works.
- An applied filter is a **lens**: the editor keeps the parent, and
  Check / Snakefile / run.sh / Filtered are computed from the pruned
  benchmark (worker `convert`/`snakefile` messages carry `picks`). The
  `⊂ filtered view` pill on the right and the dot on the Filter tab say
  so. Errors from the pruned text carry no line number (it would point
  into text the user can't see).
- Right pane has tabs (Check / Snakefile / run.sh / Filtered / Results).
  Check and Filtered are custom DOM panes; Snakefile and run.sh share one
  Monaco editor with two models (cheap + avoids re-layout flicker).
  Filtered shows the outputs of the applied filter: filtered YAML, the
  blob, and the picks YAML.
- Picks that don't resolve (renamed stage/module, vanished combo hash)
  are reported in the Filter tab's status line — `ob run --filter`
  refuses such a filter unless `--allow-drift`. The applied text is kept
  as written; the stale result is regenerated when the parent changes.

## Things to *not* do

- Don't introduce a build step. If a feature needs npm/bundling,
  reconsider the feature.
- Don't use micropip for omnibenchmark — keep zipimport + mocks.
- Don't let the worker auto-update silently — bump `?v=N`.
- Don't preserve comments through filtering. Don't promise round-trip
  fidelity beyond "valid YAML in, valid YAML out".
- Don't add fields to the selection blob without bumping `v`.
- Don't add a "trust everything" toggle. Trust is per-org, on purpose.
