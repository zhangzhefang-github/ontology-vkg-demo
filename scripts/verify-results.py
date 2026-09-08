"""Compare this fixture's SQL rows with typed SPARQL JSON (stdlib only)."""
import csv
import json
from decimal import Decimal
from pathlib import Path


def verify():
    artifacts = Path(__file__).resolve().parent.parent / ".artifacts"
    with (artifacts / "sql.tsv").open(encoding="utf-8", newline="") as source:
        sql_rows = [(int(row[0]), row[1], Decimal(row[2])) for row in csv.reader(source, delimiter="\t")]
    result = json.loads((artifacts / "sparql.json").read_text(encoding="utf-8"))
    if result["head"]["vars"] != ["order", "supplierName", "amount"]:
        raise ValueError(f"Unexpected SPARQL columns: {result['head']}")
    sparql_rows = []
    for row in result["results"]["bindings"]:
        order, name, amount = row["order"], row["supplierName"], row["amount"]
        prefix = "https://example.org/purchase-order/"
        if order["type"] != "uri" or not order["value"].startswith(prefix):
            raise ValueError(f"Invalid order URI: {order}")
        if name["type"] != "literal" or "xml:lang" in name or name.get(
            "datatype", "http://www.w3.org/2001/XMLSchema#string"
        ) != "http://www.w3.org/2001/XMLSchema#string":
            raise ValueError(f"Invalid supplier name literal: {name}")
        if amount["type"] != "literal" or amount.get("datatype") != "http://www.w3.org/2001/XMLSchema#decimal":
            raise ValueError(f"Invalid decimal amount: {amount}")
        sparql_rows.append((int(order["value"][len(prefix):]), name["value"], Decimal(amount["value"])))
    expected = [(101, "华北钢材", Decimal("50000"))]
    if sql_rows != expected or sparql_rows != expected or sql_rows != sparql_rows:
        raise ValueError(f"Result mismatch: SQL={sql_rows!r}; SPARQL={sparql_rows!r}; expected={expected!r}")
    print("SPARQL PASS：101\t华北钢材\t50000.00")
    print("SQL结果 = SPARQL结果：PASS（恰好 1 行，URI、名称、金额及类型均通过）")


if __name__ == "__main__":
    verify()
