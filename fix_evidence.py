import re
with open("src/keel/evidence.py", "r") as f:
    text = f.read()
text = text.replace(
    'if (\n            line.startswith("<!--") and line.endswith("-->")\n        ) or _CLASSIFICATION_MARKERS_SET.issuperset(line.split()):',
    'if (line.startswith("<!--") and line.endswith("-->")) or _CLASSIFICATION_MARKERS_SET.issuperset(\n            line.split()\n        ):'
)
with open("src/keel/evidence.py", "w") as f:
    f.write(text)
