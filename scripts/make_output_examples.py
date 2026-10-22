"""Write output_examples/: the real output of python run.py --input for every file in examples/.

    python -m scripts.make_output_examples

Each output is what run.py printed, byte for byte, in sample mode (SAMPLE_MODE=true: the
synthetic world in SQLite and the real reference cells, no key, no network). The app never
reads these files back; tests/test_output_examples.py runs each input again and checks the
verdict and the verdict_sha256 are the same. The script also writes one receipt,
01_contradicted_receipt.json, that python verify.py re-derives offline, and an index.
"""
import io
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES, OUTPUTS = ROOT / "examples", ROOT / "output_examples"
RECEIPT_OF = "01_contradicted.json"


def sample_env() -> dict[str, str]:
    """Sample mode whatever .env says (load_dotenv never overrides a variable already set), and no memory."""
    return {**os.environ, "SAMPLE_MODE": "true", "PYTHONIOENCODING": "utf-8", "HMEM_URL": ""}


def run(example: Path) -> str:
    result = subprocess.run([sys.executable, "run.py", "--input", str(example.relative_to(ROOT))], cwd=ROOT,
                            env=sample_env(), capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise SystemExit(f"{example.name}: run.py exited {result.returncode}: {result.stdout[-300:]}")
    return result.stdout


def receipt(example: Path) -> dict:
    """The receipt of the same request, run in this process (run.py prints the evidence file only)."""
    os.environ["SAMPLE_MODE"] = "true"
    from app import orchestrator
    from app.memory.fallback import MemoryEngine
    from app.pipeline import run_case
    from app.receipt import build_receipt

    request = json.loads(example.read_text(encoding="utf-8-sig"))
    run_case(request, stream=io.StringIO(), memory_engine=MemoryEngine())
    return build_receipt(orchestrator.RUNS[request["run_id"]])


def main() -> int:
    OUTPUTS.mkdir(exist_ok=True)
    rows = []
    for example in sorted(EXAMPLES.glob("*.json")):
        text = run(example)
        out = json.loads(text)
        (OUTPUTS / example.name).write_text(text, encoding="utf-8", newline="\n")
        rows.append((example.name, out.get("verdict", out["status"]), out.get("verdict_sha256", "")))
        print(f"{example.name:<36} {rows[-1][1]:<13} {rows[-1][2][:16]}")

    sealed = receipt(EXAMPLES / RECEIPT_OF)
    expected = json.loads((OUTPUTS / RECEIPT_OF).read_text(encoding="utf-8"))["verdict_sha256"]
    if sealed["verdict_sha256"] != expected:
        raise SystemExit("the receipt's seal differs from run.py's: the run is not deterministic")
    name = RECEIPT_OF.replace(".json", "_receipt.json")
    (OUTPUTS / name).write_text(json.dumps(sealed, indent=1, ensure_ascii=False) + "\n", encoding="utf-8",
                                newline="\n")
    print(f"{name:<36} seal {sealed['verdict_sha256'][:16]}")

    lines = ["# Output examples", "",
             "Real outputs of `python run.py --input examples/<file>` in sample mode, written by",
             "`python -m scripts.make_output_examples`. The app never reads them; "
             "`tests/test_output_examples.py` reruns each input and checks the verdict and its seal.", "",
             "| Input | Verdict | verdict_sha256 |", "|---|---|---|"]
    lines += [f"| `examples/{n}` | {v} | `{s}` |" for n, v, s in rows]
    lines += ["", f"`{name}` is the receipt of `{RECEIPT_OF}`: `python verify.py output_examples/{name}` "
                  "prints VALID and the same seal."]
    (OUTPUTS / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
