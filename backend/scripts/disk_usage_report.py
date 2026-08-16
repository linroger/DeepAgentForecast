"""Read-only disk-usage report for DeepResearchForecast run artifacts.

Prints a per-area size summary (uploads/pipelines, uploads/reports, logs,
deerflow caches), the top consumers inside each, and retention observations.
Deliberately performs NO deletion — the July incidents (1.61GB free blocked
simulations for 3+ days) motivate visibility first; cleanup stays a human
decision. Usage: uv run python scripts/disk_usage_report.py [--top N]
"""

import argparse
import os
import sys

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(BACKEND_ROOT)

AREAS = [
    ("uploads/pipelines", os.path.join(BACKEND_ROOT, "uploads", "pipelines")),
    ("uploads/reports", os.path.join(BACKEND_ROOT, "uploads", "reports")),
    ("uploads (other)", os.path.join(BACKEND_ROOT, "uploads")),
    ("backend/logs", os.path.join(BACKEND_ROOT, "logs")),
    ("deerflow_bridge/.cache", os.path.join(REPO_ROOT, "deerflow_bridge", ".cache")),
    ("repo logs/", os.path.join(REPO_ROOT, "logs")),
]


def dir_size(path):
    total = 0
    for root, dirs, files in os.walk(path, onerror=lambda e: None):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
        for name in files:
            fp = os.path.join(root, name)
            try:
                if not os.path.islink(fp):
                    total += os.path.getsize(fp)
            except OSError:
                continue
    return total


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}B"
        n /= 1024
    return f"{n:.1f}TB"


def top_children(path, limit):
    rows = []
    try:
        for entry in os.scandir(path):
            if entry.name.startswith("."):
                continue
            size = dir_size(entry.path) if entry.is_dir(follow_symlinks=False) else (
                entry.stat(follow_symlinks=False).st_size)
            rows.append((size, entry.name, entry.is_dir(follow_symlinks=False)))
    except OSError:
        return []
    rows.sort(reverse=True)
    return rows[:limit]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--top", type=int, default=10, help="top-N children per area")
    args = parser.parse_args()

    try:
        stat = os.statvfs(REPO_ROOT)
        free = stat.f_bavail * stat.f_frsize
        print(f"volume free space: {human(free)}"
              + ("  ⚠️ LOW — July incidents began below ~2GB" if free < 5 << 30 else ""))
    except OSError:
        pass

    grand = 0
    seen_other = 0
    for label, path in AREAS:
        if not os.path.isdir(path):
            continue
        size = dir_size(path)
        if label == "uploads (other)":
            size = max(0, size - seen_other)
        else:
            seen_other += size if label.startswith("uploads/") else 0
        grand += size
        print(f"\n== {label}: {human(size)}")
        for csize, name, is_dir in top_children(path, args.top):
            print(f"   {human(csize):>10}  {name}{'/' if is_dir else ''}")

    print(f"\ntotal tracked artifact footprint: {human(grand)}")
    print(
        "\nretention observations (NO action taken):\n"
        "  * charts written before loop-i2 embed 4.86MB plotly.js per HTML —\n"
        "    re-exporting or deleting superseded reports reclaims most of\n"
        "    uploads/reports (new reports write one shared bundle per charts dir).\n"
        "  * rotated mirofish.log.* before 2026-08-17 mix pytest noise into run\n"
        "    logs (fixed since); they compress well if archived.\n"
        "  * deerflow_bridge/.cache is regenerable (search/fetch caches).\n"
        "  * candidate policy: keep the latest N pipelines + any pipeline\n"
        "    referenced by a published report; archive the rest off-volume."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
