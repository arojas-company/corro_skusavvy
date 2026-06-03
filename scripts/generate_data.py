#!/usr/bin/env python3
"""
Generate static dashboard data for GitHub Pages.
- Reads SKUSAVVY_TOKEN from GitHub Actions secrets / environment.
- Writes data/dashboard.json and data/schema-debug.json.
- Does not print or save the token.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

GRAPHQL_URL = os.getenv("SKUSAVVY_GRAPHQL", "https://app.skusavvy.com/graphql")
TOKEN = os.getenv("SKUSAVVY_TOKEN", "").strip()
PAGE_SIZE = int(os.getenv("PAGE_SIZE", "100"))
MAX_PAGES = int(os.getenv("MAX_PAGES", "250"))
PAGE_DELAY_SECONDS = float(os.getenv("PAGE_DELAY_SECONDS", "1.2"))
DEFAULT_WAREHOUSE_ID = "019b6b44-4eea-7613-9f82-9af97d2255d"

KNOWN_WAREHOUSES = [
    {"id": DEFAULT_WAREHOUSE_ID, "name": "Wellington Warehouse", "location": "Wellington, FL"},
    {"id": "drop-ship", "name": "Drop Ship", "location": "Wellington, FL"},
    {"id": "corro-trailer-1", "name": "Corro Trailer 1", "location": "Saugerties, NY"},
]

VARIANTS_QUERY = """
query DashboardVariants($limit: Int, $offset: Int) {
  variants(limit: $limit, offset: $offset) {
    id
    sku
    price
    averageSales
    totalQuantity
    backorderable
    shopifyId
    product {
      id
      name
      type
      status
      shopifyId
      deletedAt
    }
    inventoryItem {
      id
      sku
      totalQuantity
    }
  }
}
"""

WAREHOUSE_VARIANTS_QUERY = """
query DashboardVariantsByWarehouse($limit: Int, $offset: Int, $warehouseId: UUID!) {
  variants(limit: $limit, offset: $offset, inStock: $warehouseId) {
    id
    sku
    price
    averageSales
    totalQuantity
    backorderable
    shopifyId
    product {
      id
      name
      type
      status
      shopifyId
      deletedAt
    }
    inventoryItem {
      id
      sku
      totalQuantity
    }
  }
}
"""

# Discover warehouse list. SKUSavvy warehouses has no limit/offset args.
# We only require id/name so the dropdown can use real UUIDs for Wellington, Drop Ship, etc.
WAREHOUSES_CANDIDATES = [
    ("warehouses_id_name", """
    query Warehouses {
      warehouses { id name }
    }
    """),
    ("warehouses_with_location_name", """
    query Warehouses {
      warehouses { id name location { name } }
    }
    """),
    ("warehouses_with_location_city_state", """
    query Warehouses {
      warehouses { id name location { city state } }
    }
    """),
]

# Candidate warehouse inventory queries. They are intentionally isolated: one invalid query does not stop the dashboard.
WAREHOUSE_INVENTORY_CANDIDATES = [
    ("warehouse_inventory_id", """
    query WarehouseInventory($id: ID!) {
      warehouse(id: $id) {
        id name
        inventory { sku quantity qty totalQuantity availableQuantity onHand onHandQuantity unitCost variant { id sku } inventoryItem { id sku totalQuantity } product { id name } }
      }
    }
    """, lambda wid: {"id": wid}),
    ("warehouse_inventory_string", """
    query WarehouseInventory($id: String!) {
      warehouse(id: $id) {
        id name
        inventory { sku quantity qty totalQuantity availableQuantity onHand onHandQuantity unitCost variant { id sku } inventoryItem { id sku totalQuantity } product { id name } }
      }
    }
    """, lambda wid: {"id": wid}),
    ("inventory_warehouseId", """
    query InventoryByWarehouse($warehouseId: ID!, $limit: Int, $offset: Int) {
      inventory(warehouseId: $warehouseId, limit: $limit, offset: $offset) { sku quantity qty totalQuantity availableQuantity onHand onHandQuantity unitCost variant { id sku } inventoryItem { id sku totalQuantity } product { id name } }
    }
    """, lambda wid: {"warehouseId": wid, "limit": PAGE_SIZE, "offset": 0}),
    ("inventory_warehouse", """
    query InventoryByWarehouse($warehouse: ID!, $limit: Int, $offset: Int) {
      inventory(warehouse: $warehouse, limit: $limit, offset: $offset) { sku quantity qty totalQuantity availableQuantity onHand onHandQuantity unitCost variant { id sku } inventoryItem { id sku totalQuantity } product { id name } }
    }
    """, lambda wid: {"warehouse": wid, "limit": PAGE_SIZE, "offset": 0}),
    ("inventoryItems_warehouseId", """
    query InventoryItemsByWarehouse($warehouseId: ID!, $limit: Int, $offset: Int) {
      inventoryItems(warehouseId: $warehouseId, limit: $limit, offset: $offset) { sku quantity qty totalQuantity availableQuantity onHand onHandQuantity unitCost variant { id sku } product { id name } }
    }
    """, lambda wid: {"warehouseId": wid, "limit": PAGE_SIZE, "offset": 0}),
    ("inventoryItems_warehouse", """
    query InventoryItemsByWarehouse($warehouse: ID!, $limit: Int, $offset: Int) {
      inventoryItems(warehouse: $warehouse, limit: $limit, offset: $offset) { sku quantity qty totalQuantity availableQuantity onHand onHandQuantity unitCost variant { id sku } product { id name } }
    }
    """, lambda wid: {"warehouse": wid, "limit": PAGE_SIZE, "offset": 0}),
    ("warehouse_inventoryItems", """
    query WarehouseInventoryItems($id: ID!, $limit: Int, $offset: Int) {
      warehouse(id: $id) {
        id name
        inventoryItems(limit: $limit, offset: $offset) { sku quantity qty totalQuantity availableQuantity onHand onHandQuantity unitCost variant { id sku } product { id name } }
      }
    }
    """, lambda wid: {"id": wid, "limit": PAGE_SIZE, "offset": 0}),
    ("warehouse_items", """
    query WarehouseItems($id: ID!, $limit: Int, $offset: Int) {
      warehouse(id: $id) {
        id name
        items(limit: $limit, offset: $offset) { sku quantity qty totalQuantity availableQuantity onHand onHandQuantity unitCost variant { id sku } inventoryItem { id sku totalQuantity } product { id name } }
      }
    }
    """, lambda wid: {"id": wid, "limit": PAGE_SIZE, "offset": 0}),
    ("bins_by_warehouse", """
    query BinsByWarehouse($warehouseId: ID!, $limit: Int, $offset: Int) {
      bins(warehouseId: $warehouseId, limit: $limit, offset: $offset) { id name inventory { sku quantity qty totalQuantity availableQuantity onHand onHandQuantity variant { id sku } inventoryItem { id sku totalQuantity } } }
    }
    """, lambda wid: {"warehouseId": wid, "limit": PAGE_SIZE, "offset": 0}),
]

SCHEMA_DEBUG_QUERY = """
query QueryArgsDebug {
  __schema {
    queryType {
      fields {
        name
        args { name type { name kind ofType { name kind ofType { name kind ofType { name kind } } } } }
        type { name kind ofType { name kind ofType { name kind ofType { name kind } } } }
      }
    }
  }
}
"""

TYPE_DEBUG_QUERY = """
query TypeDebug($name: String!) {
  __type(name: $name) {
    name kind
    fields {
      name
      args { name type { name kind ofType { name kind } } }
      type { name kind ofType { name kind } }
    }
  }
}
"""


def ensure_dirs() -> None:
    os.makedirs("data", exist_ok=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: str, payload: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def gql(query: str, variables: Dict[str, Any] | None = None) -> Dict[str, Any]:
    if not TOKEN:
        raise RuntimeError("Missing SKUSAVVY_TOKEN. Add it in GitHub → Settings → Secrets and variables → Actions.")
    body = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
    req = urllib.request.Request(
        GRAPHQL_URL,
        data=body,
        headers={"accept": "application/json", "content-type": "application/json", "x-token": TOKEN},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=75) as res:
            payload = json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        message = exc.read().decode("utf-8", errors="ignore")[:700]
        raise RuntimeError(f"SKUSavvy HTTP {exc.code}: {message}") from exc
    if payload.get("errors"):
        raise RuntimeError(" | ".join(str(e.get("message", e)) for e in payload["errors"]))
    return payload.get("data") or {}


def to_num(value: Any, fallback: float = 0) -> float:
    try:
        if value is None or value == "":
            return fallback
        return float(value)
    except (TypeError, ValueError):
        return fallback


def cents(value: Any) -> float:
    return round(to_num(value, 0) / 100, 2)


def clean_status(status: Any) -> str:
    return str(status or "active").lower()


def fetch_variants() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    seen = set()
    for page in range(MAX_PAGES):
        offset = page * PAGE_SIZE
        data = gql(VARIANTS_QUERY, {"limit": PAGE_SIZE, "offset": offset})
        batch = data.get("variants") or []
        for item in batch:
            key = item.get("id") or item.get("sku")
            if key and key not in seen:
                seen.add(key)
                rows.append(item)
        print(f"variants offset={offset} page={len(batch)} total={len(rows)}")
        if len(batch) < PAGE_SIZE:
            break
        time.sleep(PAGE_DELAY_SECONDS)
    return rows



def fetch_variants_by_warehouse(warehouse_id: str) -> List[Dict[str, Any]]:
    """Fetch variants that SKUSavvy reports as in stock for a warehouse.

    This uses the schema-provided variants(inStock: UUID) argument. It is the closest
    match to the warehouse inventory screen until the account exposes a bulk
    InventoryQty query. It makes the dashboard change by warehouse and avoids showing
    SKUs that are not present in the selected warehouse.
    """
    rows: List[Dict[str, Any]] = []
    seen = set()
    for page in range(MAX_PAGES):
        offset = page * PAGE_SIZE
        data = gql(WAREHOUSE_VARIANTS_QUERY, {"limit": PAGE_SIZE, "offset": offset, "warehouseId": warehouse_id})
        batch = data.get("variants") or []
        for item in batch:
            key = item.get("id") or item.get("sku")
            if key and key not in seen:
                seen.add(key)
                rows.append(item)
        print(f"warehouse variants warehouse={warehouse_id} offset={offset} page={len(batch)} total={len(rows)}")
        if len(batch) < PAGE_SIZE:
            break
        time.sleep(PAGE_DELAY_SECONDS)
    return rows

def simple_location(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        parts = [value.get("city"), value.get("state"), value.get("name")]
        return ", ".join(str(x) for x in parts if x) or ""
    return ""


def fetch_warehouses() -> List[Dict[str, str]]:
    for name, query in WAREHOUSES_CANDIDATES:
        try:
            data = gql(query)
            items = data.get("warehouses") or []
            out = []
            for wh in items:
                if isinstance(wh, dict) and wh.get("id") and wh.get("name"):
                    out.append({"id": str(wh["id"]), "name": str(wh["name"]), "location": simple_location(wh.get("location") or wh)})
            if out:
                print(f"warehouses OK via {name}: {len(out)}")
                # Keep Wellington first/default if present.
                out.sort(key=lambda x: 0 if x["id"] == DEFAULT_WAREHOUSE_ID else 1)
                return out
        except Exception as exc:  # noqa: BLE001
            print(f"warehouse list candidate failed {name}: {exc}")
    return KNOWN_WAREHOUSES


def extract_sku(obj: Dict[str, Any]) -> str | None:
    for key in ("sku", "SKU"):
        if obj.get(key):
            return str(obj[key])
    for nested_key in ("variant", "inventoryItem", "productVariant", "item"):
        nested = obj.get(nested_key)
        if isinstance(nested, dict) and nested.get("sku"):
            return str(nested["sku"])
    return None


def extract_qty(obj: Dict[str, Any]) -> float | None:
    # Prefer on-hand / total style quantities. Use available only if that is all the API returns.
    for key in ("onHandQuantity", "onHand", "quantity", "qty", "totalQuantity", "stock", "stockAvailable", "availableQuantity"):
        if key in obj and obj[key] is not None:
            return to_num(obj[key], 0)
    inv = obj.get("inventoryItem")
    if isinstance(inv, dict) and inv.get("totalQuantity") is not None:
        return to_num(inv.get("totalQuantity"), 0)
    return None


def walk_inventory(node: Any, out: Dict[str, float]) -> None:
    if isinstance(node, list):
        for item in node:
            walk_inventory(item, out)
        return
    if isinstance(node, dict):
        sku = extract_sku(node)
        qty = extract_qty(node)
        if sku and qty is not None:
            out[sku] = out.get(sku, 0) + qty
        for value in node.values():
            if isinstance(value, (list, dict)):
                walk_inventory(value, out)


def fetch_warehouse_inventory(warehouse_id: str) -> Tuple[Dict[str, float], str | None, str | None]:
    errors: List[str] = []
    for name, query, variables_fn in WAREHOUSE_INVENTORY_CANDIDATES:
        try:
            data = gql(query, variables_fn(warehouse_id))
            stock_by_sku: Dict[str, float] = {}
            walk_inventory(data, stock_by_sku)
            if stock_by_sku:
                print(f"warehouse inventory OK via {name}: {len(stock_by_sku)} SKUs")
                return stock_by_sku, None, name
            errors.append(f"{name}: query returned but no SKU/QTY pairs were found")
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            errors.append(f"{name}: {msg[:280]}")
            print(f"warehouse inventory candidate failed {name}: {msg[:280]}")
    return {}, " || ".join(errors[-4:]), None



def variant_stock_map(variants: List[Dict[str, Any]]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for v in variants:
        sku = str(v.get("sku") or (v.get("inventoryItem") or {}).get("sku") or "").strip()
        if not sku:
            continue
        qty = to_num(v.get("totalQuantity"), to_num((v.get("inventoryItem") or {}).get("totalQuantity"), 0))
        out[sku] = qty
    return out

def write_schema_debug() -> None:
    debug: Dict[str, Any] = {"generatedAt": now_iso()}
    try:
        data = gql(SCHEMA_DEBUG_QUERY)
        fields = data.get("__schema", {}).get("queryType", {}).get("fields", [])
        debug["queryFields"] = [
            f for f in fields if any(term in f.get("name", "").lower() for term in ["warehouse", "inventory", "location", "bin", "stock", "variant"])
        ]
        for type_name in ["Warehouse", "Inventory", "InventoryItem", "Variant", "ProductVariant", "Bin", "Lot"]:
            try:
                debug[type_name] = gql(TYPE_DEBUG_QUERY, {"name": type_name}).get("__type")
            except Exception as exc:  # noqa: BLE001
                debug[type_name] = {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        debug["error"] = str(exc)
    write_json("data/schema-debug.json", debug)


def normalize_rows(variants: List[Dict[str, Any]], stock_maps: Dict[str, Dict[str, float]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for idx, v in enumerate(variants):
        sku = str(v.get("sku") or (v.get("inventoryItem") or {}).get("sku") or "—")
        total_stock = to_num(v.get("totalQuantity"), to_num((v.get("inventoryItem") or {}).get("totalQuantity"), 0))
        price = cents(v.get("price"))
        avg_daily = to_num(v.get("averageSales"), 0)
        product = v.get("product") or {}
        status = clean_status(product.get("status") or ("archived" if product.get("deletedAt") else "active"))
        stock_by_wh = {wid: stock_map.get(sku, 0) for wid, stock_map in stock_maps.items()}
        normalized.append({
            "rank": idx + 1,
            "id": v.get("id"),
            "sku": sku,
            "productName": product.get("name") or sku or "Untitled product",
            "category": product.get("type") or "—",
            "productStatus": status,
            "shopifyId": v.get("shopifyId") or product.get("shopifyId") or "—",
            "backorderable": bool(v.get("backorderable")),
            "totalStock": total_stock,
            "stockByWarehouse": stock_by_wh,
            "price": price,
            "unitCost": price,
            "avgDailySales": avg_daily,
            "marginBySku": None,
        })
    return normalized


def main() -> None:
    ensure_dirs()
    if not TOKEN:
        write_json("data/dashboard.json", {
            "generatedAt": now_iso(),
            "error": "Missing SKUSAVVY_TOKEN. Add it as a GitHub Actions secret and run the workflow again.",
            "warehouses": KNOWN_WAREHOUSES,
            "defaultWarehouseId": DEFAULT_WAREHOUSE_ID,
            "warehouseDataStatus": "missing_token",
            "rows": [],
        })
        return

    warehouse_errors: Dict[str, str] = {}
    warehouse_query_used: Dict[str, str] = {}
    stock_maps: Dict[str, Dict[str, float]] = {}

    write_schema_debug()
    warehouses = fetch_warehouses()
    variants = fetch_variants()

    # First use the schema-supported warehouse filter: variants(inStock: warehouseId).
    # This matches what the SKUSavvy warehouse inventory screen does: a SKU appears in
    # Wellington Warehouse but not in Drop Ship when its warehouse stock is only in Wellington.
    for wh in warehouses:
        try:
            wh_variants = fetch_variants_by_warehouse(wh["id"])
            stock = variant_stock_map(wh_variants)
            if stock:
                stock_maps[wh["id"]] = stock
                warehouse_query_used[wh["id"]] = "variants(inStock: warehouseId)"
            else:
                warehouse_errors[wh["id"]] = "variants(inStock: warehouseId) returned no in-stock SKUs for this warehouse"
        except Exception as exc:  # noqa: BLE001
            warehouse_errors[wh["id"]] = str(exc)[:500]
            print(f"warehouse variants failed {wh['name']} {wh['id']}: {exc}")

    # Optional fallback for future schema versions; kept as a secondary attempt.
    for wh in warehouses:
        if wh["id"] in stock_maps:
            continue
        stock, err, query_used = fetch_warehouse_inventory(wh["id"])
        if stock:
            stock_maps[wh["id"]] = stock
            warehouse_query_used[wh["id"]] = query_used or "warehouse inventory candidate"
        elif err and wh["id"] not in warehouse_errors:
            warehouse_errors[wh["id"]] = err

    warehouse_status = "ok" if stock_maps else "needs_mapping"
    warning = None
    if stock_maps:
        warning = (
            "Warehouse filter is using SKUSavvy variants(inStock: warehouseId). "
            "Validate a few sample SKUs against Warehouse → Inventory in SKUSavvy."
        )
    else:
        warning = (
            "Warehouse-level inventory was not confirmed from SKUSavvy GraphQL. "
            "The dashboard will keep showing total inventory as a safe fallback instead of false zeroes. "
            "Open data/schema-debug.json from the repo and share it to map the exact inventory-by-warehouse field."
        )

    payload = {
        "generatedAt": now_iso(),
        "source": "SKUSavvy GraphQL via GitHub Actions Python",
        "warehouses": warehouses,
        "defaultWarehouseId": DEFAULT_WAREHOUSE_ID,
        "warehouseDataStatus": warehouse_status,
        "warehouseWarning": warning,
        "warehouseErrors": warehouse_errors,
        "warehouseQueryUsed": warehouse_query_used,
        "rows": normalize_rows(variants, stock_maps),
    }
    write_json("data/dashboard.json", payload)
    print(f"Wrote data/dashboard.json rows={len(payload['rows'])} warehouse_status={warehouse_status}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Always write a JSON file so GitHub Pages never returns a 404/HTML error.
        ensure_dirs()
        write_json("data/dashboard.json", {
            "generatedAt": now_iso(),
            "source": "SKUSavvy GraphQL via GitHub Actions Python",
            "error": str(exc),
            "warehouses": KNOWN_WAREHOUSES,
            "defaultWarehouseId": DEFAULT_WAREHOUSE_ID,
            "warehouseDataStatus": "error",
            "warehouseWarning": "Data generation failed. Check GitHub Actions logs and verify SKUSAVVY_TOKEN.",
            "rows": [],
        })
        print(f"Wrote fallback data/dashboard.json because generation failed: {exc}")
        raise
