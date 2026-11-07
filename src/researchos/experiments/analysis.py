"""Deterministic metrics for researcher-provided prediction columns."""

from collections import Counter
from datetime import datetime, timezone
import csv
import json
import math
from pathlib import Path
import uuid

from researchos.experiments.datasets import dataset_path, get_dataset


MAX_METRIC_ROWS = 100_000
MAX_CLASS_LABELS = 50


def list_evaluations(output_dir: str | Path) -> list[dict]:
    folder = Path(output_dir) / "experiments" / "evaluations"
    if not folder.exists():
        return []
    values = []
    for path in folder.glob("*.json"):
        try:
            values.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return sorted(values, key=lambda item: item.get("created_at", ""), reverse=True)


def evaluate_prediction_columns(output_dir: str | Path, dataset_id: str, task: str,
                                actual_column: str, predicted_column: str) -> dict:
    dataset = get_dataset(output_dir, dataset_id)
    columns = dataset["summary"]["columns"]
    if not isinstance(actual_column, str) or actual_column not in columns:
        raise ValueError("Choose an actual-value column from the selected dataset.")
    if not isinstance(predicted_column, str) or predicted_column not in columns or predicted_column == actual_column:
        raise ValueError("Choose a different prediction column from the selected dataset.")
    if task not in {"classification", "regression"}:
        raise ValueError("Choose classification or regression evaluation.")
    if dataset["summary"].get("row_count", 0) > MAX_METRIC_ROWS:
        raise ValueError("Metrics support datasets with 1 to 100,000 rows.")
    path = dataset_path(output_dir, dataset_id)
    rows = _read_rows(path, dataset["format"])
    if not rows or len(rows) > MAX_METRIC_ROWS:
        raise ValueError("Metrics support datasets with 1 to 100,000 rows.")
    pairs = []
    for index, row in enumerate(rows, 1):
        actual, predicted = row.get(actual_column), row.get(predicted_column)
        if actual is None or predicted is None or actual == "" or predicted == "":
            raise ValueError(f"Rows with missing actual/predicted values must be handled before evaluation (row {index}).")
        pairs.append((actual, predicted))
    metrics = _classification(pairs) if task == "classification" else _regression(pairs)
    visualization = _confusion_visualization(metrics) if task == "classification" else _regression_visualization(pairs)
    record = {"evaluation_id": str(uuid.uuid4()), "dataset_id": dataset_id, "dataset_sha256": dataset["sha256"],
        "task": task, "actual_column": actual_column, "predicted_column": predicted_column,
        "row_count": len(pairs), "metrics": metrics, "visualization": visualization,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "warning": "Descriptive metrics from the supplied rows only; no inferential comparison or generalization claim is made."}
    folder = Path(output_dir) / "experiments" / "evaluations"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{record['evaluation_id']}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return record


def _read_rows(path: Path, fmt: str) -> list[dict]:
    if fmt == "csv":
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as file:
                return list(csv.DictReader(file))
        except (UnicodeDecodeError, csv.Error) as exc:
            raise ValueError("Stored CSV could not be decoded for metric evaluation.") from exc
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Stored JSON could not be decoded for metric evaluation.") from exc
    return value


def _classification(pairs: list[tuple[object, object]]) -> dict:
    normalized = [(str(actual), str(predicted)) for actual, predicted in pairs]
    labels = sorted({label for pair in normalized for label in pair})
    if len(labels) > MAX_CLASS_LABELS:
        raise ValueError("Classification metrics support at most 50 distinct labels.")
    if any(len(label) > 200 for label in labels):
        raise ValueError("Classification labels must be 200 characters or fewer.")
    actual_counts = Counter(actual for actual, _ in normalized)
    confusion = {actual: Counter() for actual in labels}
    for actual, predicted in normalized:
        confusion[actual][predicted] += 1
    total = len(normalized)
    correct = sum(confusion[label][label] for label in labels)
    per_class = {}
    for label in labels:
        tp = confusion[label][label]
        fp = sum(confusion[other][label] for other in labels if other != label)
        fn = sum(confusion[label][other] for other in labels if other != label)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[label] = {"support": actual_counts[label], "precision": precision, "recall": recall, "f1": f1}
    macro_f1 = sum(value["f1"] for value in per_class.values()) / len(labels)
    majority = max(actual_counts.values()) / total
    return {"accuracy": correct / total, "macro_f1": macro_f1,
        "majority_class_baseline_accuracy": majority, "labels": labels,
        "confusion_matrix": [[confusion[actual][predicted] for predicted in labels] for actual in labels],
        "per_class": per_class}


def _confusion_visualization(metrics: dict) -> dict:
    return {"type": "confusion_matrix", "labels": metrics["labels"],
            "matrix": metrics["confusion_matrix"], "actual_rows_predicted_columns": True}


def _regression(pairs: list[tuple[object, object]]) -> dict:
    values = []
    for index, (actual, predicted) in enumerate(pairs, 1):
        try:
            y, estimate = float(actual), float(predicted)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Regression columns must contain numeric values (row {index}).") from exc
        if not math.isfinite(y) or not math.isfinite(estimate) or abs(y) > 1e150 or abs(estimate) > 1e150:
            raise ValueError(f"Regression columns must contain finite values within a safe numeric range (row {index}).")
        values.append((y, estimate))
    n = len(values)
    mean = sum(y for y, _ in values) / n
    absolute = sum(abs(y - estimate) for y, estimate in values)
    squared = sum((y - estimate) ** 2 for y, estimate in values)
    baseline_absolute = sum(abs(y - mean) for y, _ in values)
    baseline_squared = sum((y - mean) ** 2 for y, _ in values)
    r2 = 1 - squared / baseline_squared if baseline_squared > 0 else None
    return {"mae": absolute / n, "rmse": math.sqrt(squared / n), "r2": r2,
        "mean_baseline_mae": baseline_absolute / n, "mean_baseline_rmse": math.sqrt(baseline_squared / n)}


def _regression_visualization(pairs: list[tuple[object, object]]) -> dict:
    numeric = [(float(actual), float(predicted)) for actual, predicted in pairs]
    stride = max(1, math.ceil(len(numeric) / 1000))
    points = [[actual, predicted] for index, (actual, predicted) in enumerate(numeric) if index % stride == 0][:1000]
    return {"type": "actual_predicted_scatter", "points": points,
            "sampled": len(points) < len(numeric), "reference_line": "y=x"}
