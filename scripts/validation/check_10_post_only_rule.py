"""Validation #10: post-only rule is documented in all 3 required locations."""
from __future__ import annotations

import sys
from pathlib import Path

MEMORY_DIR = Path.home() / ".claude/projects/C--Users-Leandro-Downloads-hyperoil2/memory"


def main() -> int:
    checks: list[tuple[str, bool, str]] = []

    # 1. Memory file exists
    mem_file = MEMORY_DIR / "feedback_post_only_orders.md"
    exists = mem_file.exists()
    checks.append(("feedback_post_only_orders.md exists", exists, str(mem_file)))

    if exists:
        content = mem_file.read_text(encoding="utf-8", errors="ignore")
        checks.append((
            "  contains 'post-only' / 'limit_maker'",
            "post-only" in content.lower() or "limit_maker" in content.lower(),
            "",
        ))

    # 2. MEMORY.md index links it
    index = MEMORY_DIR / "MEMORY.md"
    exists = index.exists()
    checks.append(("MEMORY.md index exists", exists, str(index)))
    if exists:
        content = index.read_text(encoding="utf-8", errors="ignore")
        checks.append((
            "  references feedback_post_only_orders",
            "feedback_post_only_orders" in content,
            "",
        ))

    # 3. donchian_config.yaml has order_policy
    cfg = Path("donchian_config.yaml")
    exists = cfg.exists()
    checks.append(("donchian_config.yaml exists", exists, str(cfg)))
    if exists:
        content = cfg.read_text(encoding="utf-8", errors="ignore")
        checks.append(("  has order_policy section", "order_policy:" in content, ""))
        checks.append(("  entry_order_type: limit_maker", "limit_maker" in content, ""))

    print(f"{'Check':<55}  Result")
    print("-" * 70)
    n_fail = 0
    for name, ok, _ in checks:
        mark = "OK" if ok else "FAIL"
        if not ok:
            n_fail += 1
        print(f"{name:<55}  {mark}")
    print("-" * 70)
    print(f"Failures: {n_fail}")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
