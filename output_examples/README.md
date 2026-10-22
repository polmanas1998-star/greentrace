# Output examples

Real outputs of `python run.py --input examples/<file>` in sample mode, written by
`python -m scripts.make_output_examples`. The app never reads them; `tests/test_output_examples.py` reruns each input and checks the verdict and its seal.

| Input | Verdict | verdict_sha256 |
|---|---|---|
| `examples/00_smoke.json` | CONTRADICTED | `24035b459821721e168619c808d35a6a03819b9feb113dac3ffabca4cba56eb0` |
| `examples/01_contradicted.json` | CONTRADICTED | `24035b459821721e168619c808d35a6a03819b9feb113dac3ffabca4cba56eb0` |
| `examples/02_proven.json` | PROVEN | `509901c785c2d42e6f00c08a823104fdbf42500b58de2243017c62bf6f9bdafa` |
| `examples/03_unverifiable.json` | UNVERIFIABLE | `e69f9c692a1e2c8e57f379e58a58dcaf42e3fa4a25ff1932d263640c5634687e` |
| `examples/04_prompt_injection.json` | CONTRADICTED | `e31226345c161aca730b1a2092edd37276a92bfd5db21f77ca5e8e19f616b094` |
| `examples/05_real_plot_contradicted.json` | CONTRADICTED | `06a4fbad34ecde7169959940d16fad47d916185d812bf41530fbdbd4ed57fb9d` |
| `examples/06_real_plot_unverifiable.json` | UNVERIFIABLE | `6361727d93e7c33505db27d1f5e8519620a7a86a01dbfd45fb0535762f1831d4` |

`01_contradicted_receipt.json` is the receipt of `01_contradicted.json`: `python verify.py output_examples/01_contradicted_receipt.json` prints VALID and the same seal.
