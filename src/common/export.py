import csv
import json
from pathlib import Path

from .models import utc_now
from .tables import STAT_KEYS


def csv_safe(value):
    # Prevent spreadsheet applications interpreting source text as formulas.
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def export_records(records, output, kind="boxscore"):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": 1, "kind": kind, "generated_at": utc_now(),
               "record_count": len(records),
               "records_with_issues": sum(bool(row.validation) for row in records),
               "records": [record.to_dict() for record in records]}
    json_path, csv_path = output / f"{kind}.json", output / f"{kind}.csv"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    fields = ["source", "source_url", "game_id", "game_date", "team", "player", "player_id",
              "jersey", "position", "minutes", "participation", "provenance", "extracted_at",
              *STAT_KEYS, "validation"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for record in records:
            row = record.to_dict()
            row.update(row.pop("stats"))
            row["validation"] = "|".join(row["validation"])
            writer.writerow({key: csv_safe(value) for key, value in row.items()})
    return payload
