"""Check reviewed README fingerprints and bilingual technical equivalence."""

from collections import Counter
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re


FILES = ("README.md", "README_CN.md")
FENCE = re.compile(r"^```([^\n]*)\n(.*?)^```[ \t]*$", re.M | re.S)
LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
INLINE = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")


class Navigation(HTMLParser):
    def __init__(self):
        super().__init__()
        self.targets = []

    def handle_starttag(self, tag, attributes):
        if tag == "a":
            self.targets.extend(value for key, value in attributes if key == "href")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def check(root: Path) -> dict:
    manifest = json.loads((root / "configs/readme_sync.json").read_text(encoding="utf-8"))
    require(manifest.get("version") == 1 and set(manifest.get("files", {})) == set(FILES), "Invalid README synchronization manifest")
    texts = []
    fingerprints = {}
    for name in FILES:
        content = (root / name).read_bytes()
        fingerprints[name] = hashlib.sha256(content).hexdigest()
        require(fingerprints[name] == manifest["files"][name], "README changed since bilingual review: " + name)
        texts.append(content.decode("utf-8-sig").replace("\r\n", "\n"))
    code = [FENCE.findall(text) for text in texts]
    require(code[0] == code[1], "README command or configuration blocks differ")
    bodies = [FENCE.sub("", text) for text in texts]
    headings = [re.findall(r"^(#{1,6}) (.+)$", body, re.M) for body in bodies]
    require(all(headings), "README sections are missing")
    require([level for level, _ in headings[0]] == [level for level, _ in headings[1]], "README section structures differ")
    require(headings[0][0] == headings[1][0], "README project titles differ")
    identifiers = [Counter(INLINE.findall(body)) for body in bodies]
    require(identifiers[0] == identifiers[1], "README technical identifiers differ")
    links = [LINK.findall(body) for body in bodies]
    require(links[0] == links[1], "README evidence links differ")
    rows = [[len(row.strip().strip("|").split("|")) for row in body.splitlines()
             if row.startswith("|")] for body in bodies]
    require(rows[0] == rows[1], "README result table structures differ")
    numbers = []
    for body in bodies:
        prose = INLINE.sub("", body)
        prose = LINK.sub("", prose)
        prose = re.sub(r"<[^>]*>", "", prose)
        numbers.append(Counter(re.findall(r"(?<![\w])\d+(?:\.\d+)?(?:/\d+)?%?(?![\w])", prose)))
    require(numbers[0] == numbers[1], "README factual numbers differ")
    for text in texts:
        navigation = Navigation()
        navigation.feed(text)
        require(navigation.targets == list(FILES), "README language navigation differs")
        require(all((root / target).is_file() for target in navigation.targets), "A README language target is missing")
    return {"status": "passed", "files": fingerprints, "sections": len(headings[0]),
            "code_blocks": len(code[0]), "evidence_links": len(links[0]),
            "technical_identifiers": sum(identifiers[0].values()), "language_links_per_file": len(FILES)}


if __name__ == "__main__":
    print(json.dumps(check(Path(__file__).resolve().parents[1]), indent=2))
