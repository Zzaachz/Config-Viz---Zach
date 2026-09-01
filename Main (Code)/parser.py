import gzip
import json
from pathlib import Path

CACHE_DIR = Path(__file__).parent / "cache"
MANIFEST_PATH = CACHE_DIR / "manifest.json"


def _load_manifest():
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(f"No manifest at {MANIFEST_PATH}. Run crawler first.")
    with open(MANIFEST_PATH) as f:
        return json.load(f)


def _iter_items(gz_path: Path):
    """Yield raw configurationItems from a single .json.gz file."""
    with gzip.open(gz_path, "rt", encoding="utf-8") as f:
        data = json.load(f)
    yield from data.get("configurationItems", [])


def parse_cache() -> list[dict]:
    """
    Read every cached .json.gz file referenced in the manifest.

    Returns a deduplicated list of live configurationItems -- one entry per
    (awsAccountId, resourceId), keeping the most recent capture time and
    dropping any item whose configurationItemStatus is ResourceDeleted.
    """
    manifest = _load_manifest()

    # (account_id, resource_id) -> best item so far
    best: dict[tuple[str, str], dict] = {}

    for manifest_key, meta in manifest.items():
        local_path = Path(meta["local_path"])
        if not local_path.exists():
            print(f"  warn  missing cache file: {local_path}")
            continue

        for item in _iter_items(local_path):
            status = item.get("configurationItemStatus", "")
            if status in ("ResourceDeleted", "ResourceDeletedNotRecorded"):
                continue

            account_id = item.get("awsAccountId", "")
            resource_id = item.get("resourceId", "")
            if not resource_id:
                continue

            key = (account_id, resource_id)
            capture_time = item.get("configurationItemCaptureTime", "")

            existing = best.get(key)
            if existing is None or capture_time > existing.get("configurationItemCaptureTime", ""):
                best[key] = item

    return list(best.values())


def build_resource_index(items: list[dict]) -> dict[tuple[str, str], dict]:
    """
    Return a dict keyed by (awsAccountId, resourceId) for O(1) lookups
    during graph stitching.
    """
    return {(i["awsAccountId"], i["resourceId"]): i for i in items}


if __name__ == "__main__":
    items = parse_cache()
    print(f"Loaded {len(items)} live resources")

    by_type: dict[str, int] = {}
    for item in items:
        t = item.get("resourceType", "Unknown")
        by_type[t] = by_type.get(t, 0) + 1
    for t, count in sorted(by_type.items()):
        print(f"  {count:>5}  {t}")
