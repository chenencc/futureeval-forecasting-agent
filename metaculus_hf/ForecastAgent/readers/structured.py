"""Parse saved CSV/JSON locally; do not fetch or silently replace source data."""
import csv
import io
import json
from ForecastAgent.evidence.document import Document


def parse_csv(text, source):
    reader = csv.DictReader(io.StringIO(text))
    return [Document(json.dumps(row, ensure_ascii=False),
                     {"source": source, "format": "csv", "row": i + 1, "columns": reader.fieldnames})
            for i, row in enumerate(reader)]


def parse_json(text, source):
    json.loads(text)  # Fail explicitly for an invalid JSON response.
    return [Document(text, {"source": source, "format": "json"})]
