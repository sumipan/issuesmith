"""CP2 tier determination based on diff size, AC completion, and P2 pass status.

Returns a tier ("light" / "heavy"); the tier-to-model mapping is resolved by
skills/system-issuesmith/scripts/engine.py per configured engine.
"""


def determine_cp2_tier(
    pr_diff_lines: int,
    unchecked_ac_count: int,
    p2_all_pass: bool,
) -> str:
    """Return the CP2 review tier based on conditions.

    "light" when: diff < 200 lines AND all AC checked AND P2 all pass.
    "heavy" in all other cases.
    """
    if pr_diff_lines < 200 and unchecked_ac_count == 0 and p2_all_pass:
        return "light"
    return "heavy"


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Determine CP2 review tier")
    parser.add_argument("--diff-lines", type=int, required=True)
    parser.add_argument("--unchecked-ac", type=int, required=True)
    parser.add_argument("--p2-all-pass", required=True)
    args = parser.parse_args()

    p2_all_pass = args.p2_all_pass.lower() in ("true", "1", "yes")
    print(determine_cp2_tier(args.diff_lines, args.unchecked_ac, p2_all_pass))


if __name__ == "__main__":
    main()
