"""Render PDF pages with a separately installed verification renderer."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "output/verification_deps"))
sys.path.insert(0, str(ROOT))
import pypdfium2 as pdfium
from finevolab.experiments import sha256
from finevolab.runtime import write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", type=Path)
    args = parser.parse_args()
    args.pdf = args.pdf.resolve()
    document = pdfium.PdfDocument(str(args.pdf))
    pages = []
    for index in range(len(document)):
        page = document[index]
        path = args.pdf.with_name(args.pdf.stem + f"-page-{index + 1}.png")
        page.render(scale=1.5).to_pil().save(path)
        pages.append({"page": index + 1, "preview": str(path.relative_to(ROOT)),
                      "sha256": sha256(path), "characters": len(page.get_textpage().get_text_range())})
    record = {"pdf": str(args.pdf.relative_to(ROOT)), "sha256": sha256(args.pdf),
              "pages": pages, "renderer": "pypdfium2-5.14.0", "visual_review": "pending"}
    write_json(args.pdf.with_suffix(".render.json"), record)
    print(json.dumps(record))


if __name__ == "__main__":
    main()
