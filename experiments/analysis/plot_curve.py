"""Plot the pass@2 curve of a variant-pool run to a PNG (read-only).

Draws the pool aggregate pass@2 curve plus each variant's per-round pass@2, with
vertical markers at ship and fork rounds, to
``experiments/analysis/out/<tag>/curve.png``.

matplotlib is optional. If it is not installed in the active interpreter the
script degrades gracefully (prints guidance and exits 0) — it never installs
anything.

Usage::

    python experiments/analysis/plot_curve.py --run-dir recipe/gaia_evolver/runs/s1k8
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # keep experiments/analysis/ free of __pycache__

import _poolscan as ps


def _load_matplotlib():
    """Import matplotlib with a non-interactive backend, or return None."""
    try:
        import matplotlib

        matplotlib.use("Agg")  # headless: write files, never open a window
        import matplotlib.pyplot as plt

        return plt
    except ImportError:
        return None


def make_plot(run_dir: Path, plt) -> Path | None:
    tag = ps.run_tag(run_dir)
    rounds = ps.scan_run(run_dir)
    settled = [r for r in rounds if r.ok]
    if not settled:
        print(f"note: no settled rounds under {run_dir}; nothing to plot.", file=sys.stderr)
        return None

    xs = [r.round for r in settled]
    pool_y = [r.pool_pct for r in settled]

    # Per-variant series: variant -> {round: pct}. Absent in a round => gap (None).
    variants: list[str] = []
    for r in settled:
        for v in r.per_variant:
            if v not in variants:
                variants.append(v)

    fig, ax = plt.subplots(figsize=(9, 5.5))
    ax.plot(xs, pool_y, marker="o", linewidth=2.4, color="#1f3b73", label="pool aggregate pass@2", zorder=5)

    for v in variants:
        vy = []
        for r in settled:
            pv = r.per_variant.get(v)
            vy.append(100.0 * pv[0] / pv[1] if pv and pv[1] else None)
        ax.plot(xs, vy, marker=".", linewidth=1.0, alpha=0.75, label=f"{v} pass@2")

    # Ship / fork vertical markers.
    ship_rounds = [r.round for r in settled if r.shipped]
    fork_rounds = [r.round for r in settled if r.forked]
    seen_labels: set[str] = set()
    for rr in ship_rounds:
        lbl = "ship" if "ship" not in seen_labels else None
        seen_labels.add("ship")
        ax.axvline(rr, color="#2e8b57", linestyle="--", alpha=0.6, label=lbl)
    for rr in fork_rounds:
        lbl = "fork" if "fork" not in seen_labels else None
        seen_labels.add("fork")
        ax.axvline(rr, color="#b8860b", linestyle=":", alpha=0.7, label=lbl)

    stats = ps.curve_stats(rounds)
    if stats["peak_round"] is not None:
        ax.annotate(
            f"peak R{stats['peak_round']} {stats['peak_pct']:.1f}%",
            xy=(stats["peak_round"], stats["peak_pct"]),
            xytext=(0, 12),
            textcoords="offset points",
            ha="center",
            fontsize=9,
            color="#1f3b73",
        )

    denom = settled[-1].denominator
    pending = [r for r in rounds if not r.ok]
    title = f"{tag}: variant-pool pass@2 (n={denom})"
    if pending:
        title += f"  [in-flight: {len(pending)} pending round(s)]"
    ax.set_title(title)
    ax.set_xlabel("round")
    ax.set_ylabel("pass@2 (%)")
    ax.set_xticks(xs)
    ax.set_ylim(0, 100)
    ax.grid(True, alpha=0.25)
    ax.legend(fontsize=8, loc="lower right", ncol=2)
    fig.tight_layout()

    out_dir = ps.out_dir_for(tag)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "curve.png"
    fig.savefig(out_path, dpi=130)
    plt.close(fig)
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plot the variant-pool pass@2 curve to a PNG (read-only).")
    parser.add_argument("--run-dir", required=True, help="Path to runs/<tag>")
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir)
    if not run_dir.is_dir():
        print(f"error: run-dir not found: {run_dir}", file=sys.stderr)
        return 2

    plt = _load_matplotlib()
    if plt is None:
        print(
            "matplotlib is not installed in this interpreter — skipping the PNG.\n"
            "  This is a graceful no-op (the tool does not install packages).\n"
            "  To enable plotting, install matplotlib into the active venv yourself,\n"
            "  e.g.  .venv312\\Scripts\\python.exe -m pip install matplotlib\n"
            "  The numeric curve is always available via curve_extract.py / acceptance_report.py.",
            file=sys.stderr,
        )
        return 0

    out_path = make_plot(run_dir, plt)
    if out_path is None:
        return 1
    print(f"wrote plot -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
