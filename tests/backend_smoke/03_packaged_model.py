from __future__ import annotations

import importlib.util
import importlib.metadata
import json
import os
from pathlib import Path
import sys


spec = importlib.util.find_spec("ribodetector")
if spec is None or not spec.submodule_search_locations:
    raise SystemExit("RiboDetector package is unavailable")
package = Path(next(iter(spec.submodule_search_locations))).resolve()
config = json.loads((package / "config.json").read_text(encoding="utf-8"))
selected = config["state_file"]["recall"].replace(".pth", ".onnx")
model = (package / selected).resolve()
if (importlib.metadata.version("ribodetector") != "0.2.7" or not model.is_file() or
        not model.is_relative_to(package) or not package.is_relative_to(Path(sys.prefix).resolve())):
    raise SystemExit("The selected -e norrna CPU recall model is not packaged in the installed prefix")
root = Path(os.environ["BULKSEQ_SMOKE_ROOT"])
(root / "ribodetector-model.json").write_text(json.dumps({
    "packaged_model": model.name,
    "selected_by": "state_file.recall for -e norrna",
    "under_installed_prefix": True,
}, indent=2) + "\n", encoding="utf-8")
print("PASS: default RiboDetector CPU model is packaged in the installed prefix")
