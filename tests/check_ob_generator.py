"""The preview's Snakefile and rule counts come from omnibenchmark's own expansion
when the wheel has it. Runs like the worker: the wheel zip on sys.path, the
worker's mocks, then PREVIEW_PY. Use a bare Python (only pydantic + PyYAML)
so a missing mock fails here, not in the browser.

    python tests/check_ob_generator.py WHEEL PLAN [FILTER EXPECTED_RULES]
"""
import json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
wheel, plan = sys.argv[1], pathlib.Path(sys.argv[2]).read_text()
filt = pathlib.Path(sys.argv[3]).read_text() if len(sys.argv) > 3 else None
expected = int(sys.argv[4]) if len(sys.argv) > 4 else None

js = (ROOT / "worker.js").read_text()
init = js[js.index("await pyodide.runPythonAsync(`") + len("await pyodide.runPythonAsync(`"):]
init = init[: init.index("`);")].replace('"/omnibenchmark.whl"', repr(str(pathlib.Path(wheel).resolve())))
exec(init, {})                                     # mocks + smoke import, as in the browser

body = js[js.index("const PREVIEW_PY = [") + len("const PREVIEW_PY = ["):js.index('].join("\\n");')]
body = "\n".join(l for l in body.split("\n") if not l.strip().startswith("//")).strip().rstrip(",")
ns = {}
exec("\n".join(json.loads("[" + body + "]")), ns)
use_ob = ns["_has_ob_generator"]()
print(f"ok  preview engine: {'omnibenchmark expansion + writer' if use_ob else 'legacy _generate'}")

import yaml
picks = json.dumps(yaml.safe_load(filt)["picks"]) if filt else None
sf = ns["yaml_to_snakefile"](plan, picks)
assert sf["ok"], sf.get("error")
n_rules = len(re.findall(r"^rule ", sf["snakefile"], re.M))
print(f"ok  snakefile: {n_rules} rules")
gather = [l for l in sf["snakefile"].splitlines() if l.startswith("rule ") and ("PCA_M" in l or "REPORT" in l)]
print(f"ok  gather rules: {gather}")
if expected is not None:
    assert n_rules == expected, (n_rules, expected)
    print(f"ok  matches `ob run --dry`: {expected} rules")

v = ns["yaml_to_views"](plan, picks)
assert v["ok"], v.get("error")
stats = json.loads(v["stats"])
print(f"ok  views: total {stats.get('total')} rules{' (approx)' if stats.get('approx') else ''}")
if expected is not None and not stats.get("approx"):
    assert stats["total"] == expected - 1, stats      # Snakefile adds `rule all`
