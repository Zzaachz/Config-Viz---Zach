import json
import re
from pathlib import Path

import boto3

PROFILE = "logging"
BUCKET = "log-storage-aws-config-143009743682"
BASE_PREFIX = "AWSLogs/"
REGION = "us-east-2"
ACCOUNTS = [
    "124074140119",
    "162521700530",
    "387075078863",
    "886031930818",
    "954475336656",
]

CACHE_DIR = Path(__file__).parent / "cache"
MANIFEST_PATH = CACHE_DIR / "manifest.json"

# Filename pattern: <account>_Config_<region>_ConfigHistory_<ResourceType>_<start>_<end>_1.json.gz
KEY_RE = re.compile(
    r"AWSLogs/(?P<account>\d+)/Config/us-east-2/\d+/\d+/\d+/ConfigHistory/"
    r"(?P=account)_Config_us-east-2_ConfigHistory_"
    r"(?P<resource_type>.+?)_(?P<start>\d{8}T\d{6}Z)_\S+_1\.json\.gz$"
)


def load_manifest():
    if MANIFEST_PATH.exists():
        with open(MANIFEST_PATH) as f:
            return json.load(f)
    return {}


def save_manifest(manifest):
    with open(MANIFEST_PATH, "w") as f:
        json.dump(manifest, f, indent=2)


def list_config_keys(s3, account):
    prefix = f"AWSLogs/{account}/Config/{REGION}/"
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            yield obj["Key"], obj["LastModified"].isoformat()


def select_latest_per_resource(keys):
    # keys: list of (key, last_modified_iso)
    # returns dict of resource_type -> (key, last_modified_iso)
    best = {}
    for key, last_modified in keys:
        m = KEY_RE.match(key)
        if not m:
            continue
        resource_type = m.group("resource_type")
        start = m.group("start")
        if resource_type not in best or start > best[resource_type][1]:
            best[resource_type] = (key, start, last_modified)
    return best  # resource_type -> (key, start_ts, last_modified)


def download_file(s3, key, dest_path):
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    s3.download_file(BUCKET, key, str(dest_path))


def crawl():
    CACHE_DIR.mkdir(exist_ok=True)
    manifest = load_manifest()

    session = boto3.Session(profile_name=PROFILE)
    s3 = session.client("s3")

    for account in ACCOUNTS:
        print(f"[{account}] listing keys...")
        keys = list(list_config_keys(s3, account))
        latest = select_latest_per_resource(keys)
        print(f"[{account}] {len(latest)} resource types found")

        for resource_type, (key, start_ts, last_modified) in sorted(latest.items()):
            manifest_key = f"{account}/{resource_type}"
            cached = manifest.get(manifest_key, {})

            if cached.get("key") == key and cached.get("last_modified") == last_modified:
                print(f"  skip  {resource_type}")
                continue

            safe_resource_type = resource_type.replace("::", "__")
            dest = CACHE_DIR / account / f"{safe_resource_type}.json.gz"
            print(f"  fetch {resource_type}")
            download_file(s3, key, dest)

            manifest[manifest_key] = {
                "key": key,
                "last_modified": last_modified,
                "local_path": str(dest),
            }
            save_manifest(manifest)

    print("Crawl complete.")


if __name__ == "__main__":
    crawl()
