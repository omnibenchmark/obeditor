"""Parity check: the editor's pruning (PREVIEW_PY in worker.js) must agree with
`ob run --filter` (omnibenchmark.filter). Needs a Python with omnibenchmark>=0.7.

    python tests/check_filter.py [benchmark.yaml]      # default: SAMPLE_YAML in index.html
"""
import json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
js = (ROOT / "worker.js").read_text()
body = js[js.index("const PREVIEW_PY = [") + len("const PREVIEW_PY = ["):js.index('].join("\\n");')]
body = "\n".join(l for l in body.split("\n") if not l.strip().startswith("//")).strip().rstrip(",")
ns = {}
exec("\n".join(json.loads("[" + body + "]")), ns)

if len(sys.argv) > 1:
    plan = pathlib.Path(sys.argv[1]).read_text()
else:
    html = (ROOT / "index.html").read_text()
    plan = re.search(r"const SAMPLE_YAML = `\\\n(.*?)`;", html, re.S).group(1)

from omnibenchmark import filter as obfilter
from omnibenchmark.model.benchmark import Benchmark
import yaml

bench = Benchmark.from_yaml(plan)
stages = ns["_wizard"](bench)
s0, s1 = stages[0], stages[1]
m_params = next((m for st in stages for m in st["modules"] if m["params"]), None)

cases = {
    "wildcard": {s0["id"]: {"*": "all"}, s1["id"]: {s1["modules"][0]["id"]: "all"}},
    "first":    {s0["id"]: {"*": "first"}},
}
if m_params:
    sid = next(st["id"] for st in stages if m_params in st["modules"])
    cases["hashes"] = {sid: {m_params["id"]: [m_params["params"][0]["hash"]]}}

for name, picks in cases.items():
    obfilter.validate_picks(picks)
    out = yaml.safe_load(ns["_prune"](plan, picks)[0])
    kept = {(st["id"], m["id"]) for st in out["stages"] for m in st["modules"]}
    want = {(st.id, m.id) for st in bench.stages for m in st.modules if obfilter.keeps_module(picks, st.id, m.id)}
    assert kept == want, (name, kept ^ want)
    assert out["subset-of"]["sha256"] == ns["_parent_hash"](plan)
    res = ns["filter_benchmark"](plan, json.dumps(picks), "https://example/derived")
    assert res["ok"], res
    blob = obfilter.unpack_blob(res["blob_packed"])          # ob accepts the editor's blob
    assert blob["picks"] == picks and blob["parent"]["sha256"] == bench.summary_hash()
    assert obfilter.load_filter(yaml.safe_dump({"picks": picks})) ["picks"] == picks  # hand-written form
    print(f"ok  {name:16s} kept {len(kept)} module(s)")

if m_params:
    out = yaml.safe_load(ns["_prune"](plan, cases["hashes"])[0])
    mod = next(m for st in out["stages"] for m in st["modules"] if m["id"] == m_params["id"])
    assert len(mod["parameters"]) == 1 and all(len(v) == 1 for v in mod["parameters"][0].values()), mod
    print("ok  hash pick emits one single-valued params mapping")

# Views through the lens: stats shrink, stage list still describes the parent.
v = ns["yaml_to_views"](plan, json.dumps(cases["wildcard"]))
assert v["ok"], v
assert json.loads(v["stats"])["stages"] == 2 and len(json.loads(v["wizard"])) == len(stages)
assert "keeps no stage" in ns["yaml_to_views"](plan, json.dumps({}))["error"]  # no silent absence
# Metric collectors over pruned stages are dropped exactly as `ob run --filter` does.
from omnibenchmark.core._prune import filter_collectors_by_stages
only_first = {s0["id"]: {"*": "all"}}
mc = yaml.safe_load(ns["_prune"](plan, only_first)[0]).get("metric_collectors") or []
want_kept, _ = filter_collectors_by_stages(bench.metric_collectors, {s0["id"]}, bench)
assert [c["id"] for c in mc] == [c.id for c in want_kept], (mc, want_kept)
print("ok  filtered views")
