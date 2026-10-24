"""Which suppliers to check first: the suppliers of a finished run, ranked by one written rule.

    rank 1 first: a supplier a CONTRADICTED finding concerns, then one an UNVERIFIABLE finding
    concerns, then one no finding concerns; within the same status, more tonnes delivered in
    the fiscal year first; then supplier id, so the order never depends on chance.

No score is invented: every number is a count of findings or a sum of delivered tonnes from
the run's rows. A finding concerns a supplier through a supplier ref, a plot the supplier
declared, or, for untraced volume, the supplier whose tonnes no plot traces. The "missing
proof" is the Writer's own recommended action for that supplier's main finding. Pure: the
verdict stays app/rules.py's, and nothing here changes it.
"""
from typing import Any

from app.agents.writer import ACTIONS
from app.rules import CONTRADICTED, UNVERIFIABLE

NO_FINDING = "NO_FINDING"  # not PROVEN: the claim is judged as a whole, a supplier is only checked
STATUS_ORDER = (CONTRADICTED, UNVERIFIABLE, NO_FINDING)
RULE = ("contradicted first, then unverifiable, then no finding; within the same status, more tonnes "
        "delivered in the fiscal year first; then supplier id")


def concerned_suppliers(evidence: Any, finding: dict) -> set[str]:
    """The suppliers a finding concerns: named, owning a named plot, or with untraced tonnes."""
    owners: dict[str, set[str]] = {}
    for plot in evidence.plots:
        owners.setdefault(plot["plot_id"], set()).add(plot["supplier_id"])
    concerned: set[str] = set()
    for kind, _, key in (ref.partition(":") for ref in finding["refs"]):
        if kind == "supplier":
            concerned.add(key)
        elif kind == "plot":
            concerned |= owners.get(key, set())
    if finding["code"] == "volume_not_traced":
        concerned |= set(untraced_tonnes(evidence))
    return concerned


def untraced_tonnes(evidence: Any) -> dict[str, float]:
    """supplier_id -> tonnes of the fiscal year that no plot of the evidence traces."""
    known, year = {p["plot_id"] for p in evidence.plots}, evidence.company["fiscal_year"]
    totals: dict[str, float] = {}
    for d in evidence.deliveries:
        if d["year"] == year and d["plot_id"] not in known and d["tonnes"] > 0:
            totals[d["supplier_id"]] = totals.get(d["supplier_id"], 0.0) + d["tonnes"]
    return {s: round(t, 2) for s, t in sorted(totals.items())}


def rank(evidence: Any, decision: dict) -> dict[str, Any]:
    """Every supplier of the run, ranked, with the counts, tonnes and sentence behind its rank."""
    year = evidence.company["fiscal_year"]
    findings_of: dict[str, list[dict]] = {s["supplier_id"]: [] for s in evidence.suppliers}
    for finding in decision["findings"]:
        for supplier_id in concerned_suppliers(evidence, finding):
            findings_of.setdefault(supplier_id, []).append(finding)
    untraced = untraced_tonnes(evidence)

    rows = []
    for supplier in evidence.suppliers:
        sid = supplier["supplier_id"]
        mine = findings_of[sid]
        counts = {v: sum(1 for f in mine if f["verdict"] == v) for v in (CONTRADICTED, UNVERIFIABLE)}
        status = CONTRADICTED if counts[CONTRADICTED] else UNVERIFIABLE if counts[UNVERIFIABLE] else NO_FINDING
        main = next((f for f in mine if f["verdict"] == status), None)
        rows.append({
            "supplier_id": sid, "name": supplier["name"], "cooperative_group": supplier["cooperative_group"],
            "status": status,
            "tonnes": round(sum(d["tonnes"] for d in evidence.deliveries
                                if d["supplier_id"] == sid and d["year"] == year), 2),
            "untraced_tonnes": untraced.get(sid, 0.0),
            "plots": sorted({p["plot_id"] for p in evidence.plots if p["supplier_id"] == sid}),
            "findings": {"contradicted": counts[CONTRADICTED], "unverifiable": counts[UNVERIFIABLE]},
            "main_finding": main["detail"] if main else None,
            "missing_proof": _action(main, sid, untraced.get(sid, 0.0), year) if main else None,
        })
    rows.sort(key=lambda r: (STATUS_ORDER.index(r["status"]), -r["tonnes"], r["supplier_id"]))
    for number, row in enumerate(rows, start=1):
        row["rank"] = number
        row["why"] = _why(row, rows, year)
    return {"fiscal_year": year, "rule": RULE, "suppliers": rows}


def _action(finding: dict, supplier_id: str, untraced: float, year: int) -> str:
    refs = dict(r.split(":", 1) for r in finding["refs"] if ":" in r)
    if finding["code"] == "volume_not_traced" and untraced:
        return f"Ask {supplier_id} for the plots its {untraced:g} t of {year} came from."
    template = ACTIONS.get(finding["code"])
    if not template:
        return f"Settle this finding before relying on {supplier_id}: {finding['detail']}."
    return template.format(plot=refs.get("plot", "its plots"), supplier=supplier_id)


def _why(row: dict, rows: list[dict], year: int) -> str:
    n = row["findings"]
    if row["status"] == CONTRADICTED:
        status = f"{n['contradicted']} finding(s) contradict the claim on this supplier"
    elif row["status"] == UNVERIFIABLE:
        status = f"{n['unverifiable']} finding(s) leave the claim unverifiable on this supplier"
    else:
        status = "no finding concerns this supplier"
    sentence = f"Rank {row['rank']}: {status}; {row['tonnes']:g} t delivered in {year}"
    below = rows[row["rank"]:]
    same = next((r for r in below if r["status"] == row["status"]), None)
    if same and same["tonnes"] < row["tonnes"]:
        sentence += f", more than {same['supplier_id']} ({same['tonnes']:g} t) with the same status"
    elif same:
        sentence += f", as much as {same['supplier_id']}: the supplier id breaks the tie"
    return sentence + "."
