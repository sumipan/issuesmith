"""B1 brushup tier: route design-doc issues to the heavy tier, light fixes to light.

Returns a tier ("light" / "heavy"); the tier-to-model mapping is resolved by
skills/system-issuesmith/scripts/engine.py per configured engine.
"""


def determine_b1_tier(body: str) -> str:
    """Return the B1 brushup tier for an issue body.

    Design-doc templates (## 背景・目的 or ## 設計) → "heavy".
    Light-fix templates → "light".
    """
    if "## 背景・目的" in body or "## 設計" in body:
        return "heavy"
    return "light"


def main() -> None:
    import argparse

    from ghdag.github_client import GitHubClient

    parser = argparse.ArgumentParser(description="Determine B1 brushup tier")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--issue", type=int, help="GitHub issue number")
    group.add_argument("--body-file", help="Path to issue body text file")
    args = parser.parse_args()

    if args.issue is not None:
        data = GitHubClient().issue_get(args.issue, fields=["body"])
        body = data["body"]
    else:
        body = open(args.body_file, encoding="utf-8").read()

    print(determine_b1_tier(body))


if __name__ == "__main__":
    main()
