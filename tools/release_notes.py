#!/usr/bin/env python3
"""Print one version's section of CHANGELOG.md, for the release page.

    python3 tools/release_notes.py 0.1.0
"""
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
version = sys.argv[1].lstrip("v")
lines = open(os.path.join(HERE, "CHANGELOG.md")).read().splitlines()
out, inside = [], False
for line in lines:
    if line.startswith("## "):
        if inside:
            break
        inside = line[3:].strip() == version
        continue
    if inside:
        out.append(line)
if not out:
    sys.exit("CHANGELOG.md has no section for %s" % version)
print("\n".join(out).strip())
print("\nDownload the file for your system below. Each has a READ ME inside.")
