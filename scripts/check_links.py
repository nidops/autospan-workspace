"""Check documentation links locally, optionally across named repository checkouts."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit


def prose(text: str) -> str:
    return re.sub(r"^```[^\n]*\n.*?^```[^\n]*$", "", text, flags=re.M | re.S)


def anchors(path: Path) -> set[str]:
    result: set[str] = set()
    counts: dict[str, int] = {}
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", prose(path.read_text()), re.M):
        heading = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", heading)
        slug = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        result.add(f"{slug}-{count}" if count else slug)
    return result


def check(repositories: dict[str, Path]) -> int:
    """Resolve local and GitHub blob links against supplied source trees.

    This checks paths and heading anchors, not remote ref availability. Callers
    must check out the revision being validated before supplying its directory.
    Unmapped external repositories are outside this offline check.
    """
    checked = 0
    for name, root in repositories.items():
        documents = sorted(root.glob("*.md")) + sorted((root / "docs").rglob("*.md"))
        for document in documents:
            for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", prose(document.read_text())):
                url = urlsplit(target)
                if url.scheme:
                    parts = url.path.strip("/").split("/", 4)
                    if url.netloc != "github.com" or len(parts) != 5 or parts[2] != "blob":
                        continue
                    owner, repo, _, _, relative = parts
                    other = repositories.get(f"{owner}/{repo}")
                    if other is None:
                        continue
                    local = other / unquote(relative)
                else:
                    local = document.parent / unquote(url.path) if url.path else document
                if not local.exists():
                    raise ValueError(f"{name}/{document.relative_to(root)}: missing link {target}")
                if (
                    url.fragment
                    and local.suffix == ".md"
                    and unquote(url.fragment) not in anchors(local)
                ):
                    raise ValueError(
                        f"{name}/{document.relative_to(root)}: missing anchor {target}"
                    )
                checked += 1
    return checked


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", action="append", default=[], metavar="OWNER/NAME=PATH")
    args = parser.parse_args()
    roots = {"nidops/autospan-workspace": Path(__file__).resolve().parents[1]}
    for value in args.repository:
        name, separator, path = value.partition("=")
        if not separator or not path or not Path(path).is_dir():
            parser.error("--repository requires OWNER/NAME=existing-directory")
        roots[name] = Path(path).resolve()
    print(f"{check(roots)} documentation links/anchors resolve across {len(roots)} repositories")


if __name__ == "__main__":
    main()
