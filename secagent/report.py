"""Summaries with bootstrap intervals over pairs (families), per docs/sec-protocol.md."""
from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path
from statistics import mean

N_BOOT = 10_000


def load_records(run_dir: Path) -> list[dict]:
    return [json.loads(l) for l in (Path(run_dir) / "episodes.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]


def pair_scores(records: list[dict], condition: str) -> dict[str, list[float]]:
    """family -> list of pair scores, one per repetition: (detected + clean) / 2."""
    by = defaultdict(dict)
    for r in records:
        if r["condition"] == condition:
            by[(r["family"], r["repetition"])][r["variant"]] = r["score"]
    out = defaultdict(list)
    for (fam, _), v in sorted(by.items()):
        if "vulnerable" in v and "patched" in v:
            out[fam].append((bool(v["vulnerable"]["detected"]) + bool(v["patched"]["clean"])) / 2)
    return dict(out)


def bootstrap_ci(per_family: dict[str, list[float]], seed: int = 0) -> tuple[float, float, float] | None:
    fams = sorted(per_family)
    if not fams:
        return None
    point = mean(mean(per_family[f]) for f in fams)
    rng = random.Random(seed)
    stats = sorted(mean(mean(per_family[rng.choice(fams)]) for _ in fams) for _ in range(N_BOOT))
    return point, stats[int(0.025 * N_BOOT)], stats[int(0.975 * N_BOOT) - 1]


def paired_diff(records: list[dict], a: str, b: str, seed: int = 0):
    """Mean over families of (score_b - score_a), with a bootstrap interval; None if no common family."""
    sa, sb = pair_scores(records, a), pair_scores(records, b)
    common = {f: [mean(sb[f]) - mean(sa[f])] for f in sa if f in sb}
    ci = bootstrap_ci(common, seed)
    if ci is None:
        return None
    verdict = "inconclusive" if ci[1] <= 0 <= ci[2] else ("b better" if ci[0] > 0 else "a better")
    return {"a": a, "b": b, "diff": ci[0], "lo": ci[1], "hi": ci[2], "verdict": verdict, "families": len(common)}


def condition_summary(records: list[dict], condition: str) -> dict:
    rs = [r for r in records if r["condition"] == condition]
    vul = [r for r in rs if r["variant"] == "vulnerable"]
    pat = [r for r in rs if r["variant"] == "patched"]
    findings = sum(r["score"]["n_findings"] for r in rs)
    matched = sum(1 for r in vul if r["score"]["detected"])
    cov = [r["score"]["path_coverage"] for r in vul if r["score"]["path_coverage"] is not None]
    known = lambda key: [r[key] for r in rs if r[key] is not None]
    return {
        "condition": condition, "episodes": len(rs),
        "pair_score": bootstrap_ci(pair_scores(records, condition)),
        "detection": (matched, len(vul)), "twin_clean": (sum(1 for r in pat if r["score"]["clean"]), len(pat)),
        "precision": (matched, findings), "misclassified": sum(r["score"]["misclassified"] for r in rs),
        "no_submit": sum(1 for r in rs if not r["score"]["submitted"]),
        "path_coverage": round(mean(cov), 3) if cov else None,
        "mean_input_tokens": round(mean(known("input_tokens"))) if known("input_tokens") else None,
        "mean_output_tokens": round(mean(known("output_tokens"))) if known("output_tokens") else None,
        "mean_model_calls": round(mean(r["model_calls"] for r in rs), 2) if rs else None,
        "mean_latency_s": round(mean(r["model_latency_s"] for r in rs), 2) if rs else None,
        "cost_eur": round(sum(r["cost_eur"] for r in rs), 4),
        "returned_models": sorted({m for r in rs for m in r["returned_models"]}),
    }


def agreement(records: list[dict]) -> tuple[int, int]:
    """Groups (task, variant, condition) whose repetitions all gave the same outcome."""
    groups = defaultdict(set)
    for r in records:
        s = r["score"]
        groups[(r["task_id"], r["variant"], r["condition"])].add(s["detected"] if r["variant"] == "vulnerable" else s["clean"])
    multi = [g for g in groups.values()]
    return sum(1 for g in multi if len(g) == 1), len(multi)


def _frac(pair):
    k, n = pair
    return f"{k}/{n} ({100 * k / n:.0f}%)" if n else "–"


def render(records: list[dict], manifest: dict, title: str) -> str:
    synthetic = any(r["synthetic"] for r in records)
    conds = [c for c in manifest["conditions"] if any(r["condition"] == c for r in records)]
    lines = [f"# {title}", ""]
    if synthetic:
        lines += ["> **SYNTHETIC** (mock backend): numbers test the pipeline only.", ""]
    m = manifest.get("model", {})
    lines += [f"Model: `{m.get('model', m.get('backend'))}` · temperature {m.get('temperature')} · "
              f"reasoning_effort {m.get('reasoning_effort')} · episodes {len(records)}/{manifest['episodes_planned']}", "",
              "| Condition | Pair score (95% CI over pairs) | Detection | Twin clean | Precision | Misclass. | No submit | Path cov. | In tok/ep | Out tok/ep | Calls/ep | Latency/ep | Cost € |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for c in conds:
        s = condition_summary(records, c)
        ps = s["pair_score"]
        ps_txt = f"{ps[0]:.2f} [{ps[1]:.2f}, {ps[2]:.2f}]" if ps else "–"
        lines.append(f"| {c} | {ps_txt} | {_frac(s['detection'])} | {_frac(s['twin_clean'])} | {_frac(s['precision'])} | "
                     f"{s['misclassified']} | {s['no_submit']} | {s['path_coverage']} | {s['mean_input_tokens']} | "
                     f"{s['mean_output_tokens']} | {s['mean_model_calls']} | {s['mean_latency_s']}s | {s['cost_eur']} |")
    if len(conds) > 1:
        lines += ["", "Paired differences vs `none` (mean over pairs, 95% bootstrap CI):", ""]
        for c in conds:
            if c != "none" and "none" in conds:
                d = paired_diff(records, "none", c)
                if d:
                    lines.append(f"- {c} − none: {d['diff']:+.2f} [{d['lo']:+.2f}, {d['hi']:+.2f}] → **{d['verdict']}** ({d['families']} pairs)")
    same, total = agreement(records)
    lines += ["", f"Repetition agreement: {same}/{total} (task, variant, condition) groups gave the same outcome in every repetition.",
              "", "## Per task", "",
              "Witness kind: `exploit` = harmful effect observed on planted test data; `reachability` = untrusted "
              "input shown to reach the dangerous call (weaker evidence). Never merged.", "",
              "| Task | Class | Witness | Condition | Vulnerable: detected | Twin: clean | Twin alarms | End reasons |",
              "|---|---|---|---|---|---|---|---|"]
    by = defaultdict(list)
    for r in records:
        by[(r["task_id"], r["class"], r.get("witness_kind"), r["condition"])].append(r)
    for (tid, cls, wk, cond), rs in sorted(by.items()):
        v = [r for r in rs if r["variant"] == "vulnerable"]
        p = [r for r in rs if r["variant"] == "patched"]
        ends = sorted({r["end_reason"] for r in rs})
        lines.append(f"| {tid} | {cls} | {wk} | {cond} | {sum(bool(r['score']['detected']) for r in v)}/{len(v)} | "
                     f"{sum(bool(r['score']['clean']) for r in p)}/{len(p)} | {sum(r['score']['alarms'] for r in p)} | {', '.join(ends)} |")
    models = sorted({m for r in records for m in r["returned_models"]})
    lines += ["", f"Returned model ids: {', '.join(models) or '–'} (requested: {m.get('model')}). "
              "A returned id is what the API reports; it does not prove the weights are unchanged."]
    return "\n".join(lines) + "\n"


def write_report(run_dir: Path, title: str = "Detection run") -> Path:
    run_dir = Path(run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    out = run_dir / "report.md"
    out.write_text(render(load_records(run_dir), manifest, title), encoding="utf-8")
    return out
