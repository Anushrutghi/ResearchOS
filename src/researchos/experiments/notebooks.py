"""Validate and flatten Python Jupyter notebooks for isolated sequential execution."""

import hashlib
import json
import re

from researchos.experiments.sandbox import MAX_CODE_BYTES

MAX_NOTEBOOK_BYTES = 2 * 1024 * 1024
MAX_NOTEBOOK_CELLS = 200


def prepare_notebook(value: object) -> tuple[str, bytes, dict]:
    """Return executable code, canonical source bytes, and cell provenance.

    Markdown is retained in the original notebook but not executed. Notebook
    magics and shell escapes are rejected because they depend on IPython.
    Cell outputs are ignored; they are never treated as experiment results.
    """
    if isinstance(value, str):
        raw = value.encode("utf-8")
    elif isinstance(value, dict):
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    else:
        raise ValueError("Notebook must be a JSON object or encoded JSON string.")
    if len(raw) > MAX_NOTEBOOK_BYTES:
        raise ValueError("Notebook JSON exceeds the 2 MiB limit.")
    try:
        notebook = json.loads(raw.decode("utf-8")) if isinstance(value, str) else value
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Notebook must be valid UTF-8 JSON.") from exc
    if not isinstance(notebook, dict) or notebook.get("nbformat") != 4 or not isinstance(notebook.get("cells"), list):
        raise ValueError("Only Jupyter notebook format 4 is supported.")
    cells = notebook["cells"]
    if len(cells) > MAX_NOTEBOOK_CELLS:
        raise ValueError("Notebook exceeds the 200-cell limit.")
    kernel = notebook.get("metadata", {}).get("kernelspec", {}) if isinstance(notebook.get("metadata", {}), dict) else {}
    language = kernel.get("language") if isinstance(kernel, dict) else None
    if language not in (None, "python", "python3"):
        raise ValueError("Only Python notebooks can be executed.")
    blocks = []
    cell_map = []
    for index, cell in enumerate(cells):
        if not isinstance(cell, dict) or cell.get("cell_type") not in {"code", "markdown", "raw"}:
            raise ValueError(f"Notebook cell {index + 1} has an unsupported structure.")
        if cell["cell_type"] != "code":
            continue
        source = cell.get("source", "")
        if isinstance(source, list) and all(isinstance(line, str) for line in source):
            source = "".join(source)
        if not isinstance(source, str):
            raise ValueError(f"Notebook code cell {index + 1} must contain text source.")
        if any(re.match(r"^\s*(?:%|!|\?\s*$)", line) for line in source.splitlines()):
            raise ValueError(f"Notebook cell {index + 1} uses IPython syntax; convert it to plain Python first.")
        blocks.append(f"# ResearchOS notebook cell {index + 1}\n{source.rstrip()}\n")
        cell_map.append({"cell_index": index, "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest()})
    code = "\n".join(blocks)
    code_bytes = code.encode("utf-8")
    if not code.strip() or len(code_bytes) > MAX_CODE_BYTES:
        raise ValueError("Notebook must contain 1 to 512000 bytes of executable Python code.")
    return code, raw, {"format": "ipynb-v4", "original_sha256": hashlib.sha256(raw).hexdigest(),
                       "code_cell_count": len(cell_map), "code_cells": cell_map,
                       "outputs_ignored": True, "execution": "sequential Python source in Docker sandbox"}
