"""One command for judges: re-check GreenTrace's main claims on this machine, offline, in about a minute.

    python judge.py            # no key, no network, no Zetaris, no Nightshift: sample mode
    python judge.py --quick    # the same without the two benches (about 20 s)

Each check runs the real engine (or the real verify.py) and compares what it gets today with what
the README claims. It prints PASS or FAIL per claim and writes logs/judge/report.md, plus the receipt
it made and the forged copy it checked. Exit 0 when every check passes, 1 otherwise.
"""
import copy
import io
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "logs" / "judge"
SAMPLE = {"sample_mode": True, "use_model": False}


def _run(case: dict, run_id: str) -> dict:
    from app.memory.fallback import MemoryEngine
    from app.pipeline import run_case
    case = {**case, "run_id": run_id, "options": {**case.get("options", {}), **SAMPLE}}
    return run_case(case, stream=io.StringIO(), memory_engine=MemoryEngine())


def _example(name: str) -> dict:
    return json.loads((ROOT / "examples" / name).read_text(encoding="utf-8-sig"))


def _committed(name: str) -> dict:
    return json.loads((ROOT / "output_examples" / name).read_text(encoding="utf-8"))


def _verify(path: Path) -> tuple[int, str]:
    done = subprocess.run([sys.executable, str(ROOT / "verify.py"), str(path)], capture_output=True,
                          text=True, encoding="utf-8", cwd=ROOT)
    return done.returncode, (done.stdout + done.stderr).strip()


def check_seal() -> tuple[bool, str]:
    """Same data, same verdict, same seal: twice here, and as committed in the repository."""
    case = _example("01_contradicted.json")
    first, second = _run(case, "judge-seal-1"), _run(case, "judge-seal-2")
    committed = _committed("01_contradicted.json")
    seals = {first["verdict_sha256"], second["verdict_sha256"], committed["verdict_sha256"]}
    ok = first["verdict"] == "CONTRADICTED" and len(seals) == 1
    return ok, f"demo case: {first['verdict']} twice, seal {first['verdict_sha256'][:16]}... " \
               f"({'identical to' if ok else 'DIFFERENT from'} the committed run)"


def check_receipt() -> tuple[bool, str]:
    """A fresh receipt re-derives offline; the same receipt with one alert date forged does not."""
    from app import orchestrator
    from app.receipt import build_receipt
    out = _run(_example("01_contradicted.json"), "judge-receipt")
    receipt = build_receipt(orchestrator.RUNS[out["run_id"]])
    OUT.mkdir(parents=True, exist_ok=True)
    real, forged_path = OUT / "receipt.json", OUT / "receipt_forged.json"
    real.write_text(json.dumps(receipt, ensure_ascii=False, indent=1), encoding="utf-8")
    code, text = _verify(real)
    forged, changed = copy.deepcopy(receipt), 0
    for source in forged["sources"]:
        for row in source.get("rows", []):
            if row.get("alert_id") == "A-221" and "alert_date" in row:
                row["alert_date"] = "2019-03-14"
                changed += 1
    forged_path.write_text(json.dumps(forged, ensure_ascii=False, indent=1), encoding="utf-8")
    forged_code, forged_text = _verify(forged_path)
    found_as = "PROVEN" if "verdict recomputed on the data as found: PROVEN" in forged_text else "?"
    ok = code == 0 and text.startswith("VALID") and forged_code == 1 and "TAMPERED" in forged_text \
        and changed > 0 and found_as == "PROVEN"
    first = (text.splitlines()[0] if text else "(no output)").split(";")[0]
    return ok, f"verify.py on a receipt made just now: {first}; the same receipt with alert A-221 " \
               f"dated 2019-03-14 ({changed} rows): {'TAMPERED' if forged_code == 1 else f'exit {forged_code}'}, " \
               f"and the rules on the forged rows give {found_as}, not the CONTRADICTED it claims"


def check_flip() -> tuple[bool, str]:
    """What would change its mind: the engine applies one change and re-checks what depends on it."""
    from app import orchestrator
    out = _run(_example("01_contradicted.json"), "judge-flip")
    after = orchestrator.what_if(out["run_id"], "alert:A-221", "alert_date", "2020-12-31")
    change = after.get("what_if", {})
    ok = out["verdict"] == "CONTRADICTED" and after["verdict"] == "PROVEN"
    return ok, f"alert A-221 dated 2020-12-31 instead of 2022-03-14: {change.get('verdict_before')} -> " \
               f"{change.get('verdict_after')}, {len(change.get('invalidated_facts', []))} Meterless World Model " \
               f"fact(s) invalidated and re-checked"


def check_real_plots() -> tuple[bool, str]:
    """The real plots on the real EU maps: P-042 condemned, P-041 not, as committed."""
    parts, ok = [], True
    for name, plot in (("05_real_plot_contradicted.json", "P-042"), ("06_real_plot_unverifiable.json", "P-041")):
        got = _run(_example(name), f"judge-{plot}")["verdict"]
        want = _committed(name)["verdict"]
        ok &= got == want
        parts.append(f"{plot} {got}" + ("" if got == want else f" (README says {want})"))
    return ok, "real plots near Soubre: " + ", ".join(parts)


def check_bench() -> tuple[bool, str]:
    """The 21 trap cases through the real agents, rules alone, against the committed baseline."""
    from scripts.bench import BASELINE, run_all
    results = run_all(use_model=False)
    score = sum(r["ok"] for r in results)
    want = json.loads(BASELINE.read_text(encoding="utf-8"))["score"]
    false_accusations = sum(r["got"] == "CONTRADICTED" and r["expected"] != "CONTRADICTED" for r in results)
    missed = [f"{r['case']}" for r in results if not r["ok"]]
    return score == want, f"21 trap cases, rules alone: {score}/21 (README: {want}/21), " \
                          f"{false_accusations} false accusation(s), misses: cases {', '.join(missed) or 'none'}"


def check_blind() -> tuple[bool, str]:
    """The blind set another model wrote: its frozen hash, then rules alone against the published score."""
    from app import orchestrator
    from app.memory.fallback import MemoryEngine
    from app.pipeline import run_case
    from scripts import blind_set
    frozen = blind_set.FROZEN.read_text(encoding="utf-8").split()[0]
    if frozen != blind_set._digest():
        return False, "data/blind_set/cases.json no longer matches FROZEN.sha256"
    published = json.loads(blind_set.OUT.read_text(encoding="utf-8"))["rules_alone"]
    original, got = orchestrator.build_case, []
    try:  # same loop as `python -m scripts.blind_set run`, without rewriting the published file
        for case in blind_set.load_cases():
            world = blind_set.build(case)
            orchestrator.build_case = lambda number, _w=world: copy.deepcopy(_w)
            out = run_case({"run_id": f"judge-blind-{case['id'].lower()}", "input": {"claim": world["meta"]["claim"]},
                            "options": {**SAMPLE, "case": 0}}, stream=io.StringIO(), memory_engine=MemoryEngine())
            got.append((case["expected"], out.get("verdict")))
    finally:
        orchestrator.build_case = original
    score = sum(e == g for e, g in got)
    accusations = sum(g == "CONTRADICTED" and e != "CONTRADICTED" for e, g in got)
    ok = score == published["score"] and accusations == published["false_accusations"]
    return ok, f"10 cases written by another model, frozen by hash ({frozen[:8]}...) before the first run: " \
               f"{score}/{len(got)} on rules alone (published: {published['score']}/{published['of']}), " \
               f"{accusations} false accusation(s)"


def check_nightshift() -> tuple[bool, str]:
    """The kept Meterless Nightshift receipts: every Ed25519 signature again, offline."""
    from scripts.verify_nightshift_receipts import check
    files = sorted((ROOT / "data" / "nightshift_receipts").glob("*.jsonl")) \
        + sorted((ROOT / "output_examples" / "live").glob("*receipts.jsonl"))
    lines = [json.loads(t) for f in files for t in f.read_text(encoding="utf-8").splitlines() if t.strip()]
    valid = sum(not check(line) for line in lines)
    injected = sum(bool(((line["envelope"]["body"].get("receipt") or {}).get("hmem") or {}).get("injected"))
                   for line in lines)
    return valid == len(lines) > 0, f"{valid}/{len(lines)} signed receipts verify (signer meterlessd); " \
                                    f"{injected} of them say Nightshift's own memory added records " \
                                    f"to the prompt (disclosed in the README)"


CHECKS = [
    ("Same data, same verdict, same seal", check_seal),
    ("Receipt re-derives offline, a forgery is caught", check_receipt),
    ("What would change its mind", check_flip),
    ("Real plots on the real EU maps", check_real_plots),
    ("Nightshift receipts, re-checked offline", check_nightshift),
    ("21 trap cases, rules alone", check_bench),
    ("Blind set written by another model", check_blind),
]


def main(argv: list[str]) -> int:
    sys.path.insert(0, str(ROOT))
    checks = [c for c in CHECKS if not ("--quick" in argv and c[1] in (check_bench, check_blind))]
    rows, started = [], time.perf_counter()
    print("GreenTrace, checked on this machine (sample mode: no key, no network)\n")
    for title, fn in checks:
        t0 = time.perf_counter()
        try:
            ok, detail = fn()
        except Exception as error:  # a check that crashes is a failed check, named
            ok, detail = False, f"{type(error).__name__}: {error}"
        seconds = time.perf_counter() - t0
        rows.append((ok, title, detail, seconds))
        print(f"{'PASS' if ok else 'FAIL'}  {title}  ({seconds:.1f} s)\n      {detail}\n", flush=True)
    passed = sum(r[0] for r in rows)
    total = time.perf_counter() - started
    print(f"{passed}/{len(rows)} checks pass in {total:.0f} s. Report: logs/judge/report.md")
    OUT.mkdir(parents=True, exist_ok=True)
    report = ["# GreenTrace, checked by `python judge.py`", "",
              f"{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}, sample mode, {passed}/{len(rows)} checks pass "
              f"in {total:.0f} s.", "", "| | Check | What this machine got | Time |", "|---|---|---|---|"]
    report += [f"| {'PASS' if ok else 'FAIL'} | {title} | {detail} | {s:.1f} s |" for ok, title, detail, s in rows]
    (OUT / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8", newline="\n")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
