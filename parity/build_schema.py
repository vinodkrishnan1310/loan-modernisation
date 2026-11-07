"""Rewrite the label enum in schemas/mismatch.schema.json from parity/labels.json."""
import json, pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent
labels = [l["name"] for l in json.load(open(ROOT / "parity" / "labels.json"))["labels"]]
path = ROOT / "schemas" / "mismatch.schema.json"
schema = json.load(open(path))
schema["properties"]["label"]["enum"] = labels
path.write_text(json.dumps(schema, indent=2) + "\n")
print("labels:", ", ".join(labels))
