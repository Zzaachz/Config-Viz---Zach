"""
model.py -- normalize raw configurationItems into resource + edge tables,
assign layer labels, and persist to cache/model.json.
"""

import json
from pathlib import Path
from typing import Any

from parser import build_resource_index, parse_cache

MODEL_PATH = Path(__file__).parent / "cache" / "model.json"

# ---------------------------------------------------------------------------
# Layer assignment
# ---------------------------------------------------------------------------

# Maps AWS resource type prefixes/exact types -> layer name.
# Order matters: first match wins.
_LAYER_RULES: list[tuple[str, str]] = [
    # Networking
    ("AWS::EC2::VPC",                              "networking"),
    ("AWS::EC2::Subnet",                           "networking"),
    ("AWS::EC2::RouteTable",                       "networking"),
    ("AWS::EC2::Route",                            "networking"),
    ("AWS::EC2::InternetGateway",                  "networking"),
    ("AWS::EC2::NatGateway",                       "networking"),
    ("AWS::EC2::NetworkInterface",                 "networking"),
    ("AWS::EC2::NetworkAcl",                       "networking"),
    ("AWS::EC2::SecurityGroup",                    "networking"),
    ("AWS::EC2::VPCEndpoint",                      "networking"),
    ("AWS::EC2::VPCPeeringConnection",             "networking"),
    ("AWS::EC2::TransitGateway",                   "networking"),
    ("AWS::EC2::TransitGatewayAttachment",         "networking"),
    ("AWS::EC2::CustomerGateway",                  "networking"),
    ("AWS::EC2::VPNGateway",                       "networking"),
    ("AWS::EC2::VPNConnection",                    "networking"),
    ("AWS::EC2::EgressOnlyInternetGateway",        "networking"),
    ("AWS::Route53::",                             "networking"),
    ("AWS::ElasticLoadBalancing::",                "networking"),
    ("AWS::ElasticLoadBalancingV2::",              "networking"),
    ("AWS::CloudFront::",                          "networking"),
    ("AWS::DirectConnect::",                       "networking"),
    ("AWS::NetworkFirewall::",                     "networking"),
    ("AWS::GlobalAccelerator::",                   "networking"),
    # Storage
    ("AWS::S3::",                                  "storage"),
    ("AWS::EFS::",                                 "storage"),
    ("AWS::FSx::",                                 "storage"),
    ("AWS::DynamoDB::",                            "storage"),
    ("AWS::RDS::",                                 "storage"),
    ("AWS::Redshift::",                            "storage"),
    ("AWS::ElastiCache::",                         "storage"),
    ("AWS::Kinesis::",                             "storage"),
    ("AWS::Firehose::",                            "storage"),
    ("AWS::Glacier::",                             "storage"),
    ("AWS::EC2::Volume",                           "storage"),
    ("AWS::EC2::Snapshot",                         "storage"),
    ("AWS::Backup::",                              "storage"),
    ("AWS::Glue::",                                "storage"),
    ("AWS::Athena::",                              "storage"),
    # IAM
    ("AWS::IAM::",                                 "iam"),
    ("AWS::SSO::",                                 "iam"),
    ("AWS::IdentityStore::",                       "iam"),
    ("AWS::Organizations::",                       "iam"),
    ("AWS::KMS::",                                 "iam"),
    ("AWS::SecretsManager::",                      "iam"),
    ("AWS::ACM::",                                 "iam"),
    ("AWS::SSM::Parameter",                        "iam"),
    # Compute
    ("AWS::EC2::Instance",                         "compute"),
    ("AWS::EC2::SpotFleet",                        "compute"),
    ("AWS::EC2::LaunchTemplate",                   "compute"),
    ("AWS::AutoScaling::",                         "compute"),
    ("AWS::Lambda::",                              "compute"),
    ("AWS::ECS::",                                 "compute"),
    ("AWS::EKS::",                                 "compute"),
    ("AWS::Batch::",                               "compute"),
    ("AWS::ElasticBeanstalk::",                    "compute"),
    ("AWS::Lightsail::",                           "compute"),
    ("AWS::AppRunner::",                           "compute"),
    ("AWS::SageMaker::",                           "compute"),
    ("AWS::StepFunctions::",                       "compute"),
    ("AWS::CodeBuild::",                           "compute"),
    ("AWS::CodePipeline::",                        "compute"),
]


def assign_layer(resource_type: str) -> str:
    for prefix, layer in _LAYER_RULES:
        if resource_type.startswith(prefix):
            return layer
    return "other"


# ---------------------------------------------------------------------------
# Key config field extraction
# ---------------------------------------------------------------------------

def _extract_config_fields(item: dict) -> dict[str, Any]:
    """Pull a small set of interesting fields from the configuration blob."""
    raw = item.get("configuration")
    if not raw:
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    if not isinstance(raw, dict):
        return {}

    resource_type = item.get("resourceType", "")
    out: dict[str, Any] = {}

    def _pick(*keys: str):
        for k in keys:
            v = raw.get(k)
            if v not in (None, "", [], {}):
                out[k] = v

    if resource_type == "AWS::EC2::VPC":
        _pick("cidrBlock", "isDefault", "state", "dhcpOptionsId")
    elif resource_type == "AWS::EC2::Subnet":
        _pick("cidrBlock", "availabilityZone", "availableIpAddressCount",
              "defaultForAz", "mapPublicIpOnLaunch", "state")
    elif resource_type == "AWS::EC2::Instance":
        _pick("instanceType", "imageId", "state", "privateIpAddress",
              "publicIpAddress", "keyName", "iamInstanceProfile")
    elif resource_type == "AWS::EC2::SecurityGroup":
        _pick("groupName", "description", "ipPermissions", "ipPermissionsEgress")
    elif resource_type == "AWS::S3::Bucket":
        _pick("creationDate", "bucketPolicy", "versioning",
              "loggingEnabled", "serverSideEncryptionConfiguration")
    elif resource_type in ("AWS::IAM::Role", "AWS::IAM::User", "AWS::IAM::Group"):
        _pick("path", "assumeRolePolicyDocument", "createDate", "attachedManagedPolicies")
    elif resource_type == "AWS::Lambda::Function":
        _pick("runtime", "handler", "timeout", "memorySize",
              "role", "vpcConfig", "environment")
    elif resource_type == "AWS::RDS::DBInstance":
        _pick("dBInstanceClass", "engine", "engineVersion", "multiAZ",
              "storageEncrypted", "endpoint", "vpcSecurityGroups")
    elif resource_type == "AWS::ECS::Cluster":
        _pick("clusterName", "status", "activeServicesCount", "runningTasksCount")
    elif resource_type == "AWS::EKS::Cluster":
        _pick("name", "version", "status", "endpoint", "resourcesVpcConfig")
    else:
        # generic: grab any top-level scalar that looks useful
        for k, v in raw.items():
            if isinstance(v, (str, int, float, bool)) and k not in ("arn", "tags"):
                out[k] = v
            if len(out) >= 8:
                break

    return out


# ---------------------------------------------------------------------------
# Name resolution
# ---------------------------------------------------------------------------

def _resolve_name(item: dict) -> str:
    """Best human-readable name for a resource."""
    # tags take priority
    tags = item.get("tags") or {}
    if isinstance(tags, dict):
        name = tags.get("Name") or tags.get("name")
        if name:
            return name
    # resourceName field
    name = item.get("resourceName")
    if name:
        return name
    # fall back to resourceId
    return item.get("resourceId", "")


# ---------------------------------------------------------------------------
# Main normalization
# ---------------------------------------------------------------------------

def normalize(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    Returns (resources, edges).

    resources: one dict per live resource with normalized fields.
    edges: one dict per intra-account relationship from relationships[].
    """
    resources: list[dict] = []
    edges: list[dict] = []

    for item in items:
        account = item.get("awsAccountId", "")
        region = item.get("awsRegion", "")
        resource_type = item.get("resourceType", "")
        resource_id = item.get("resourceId", "")
        arn = item.get("ARN") or item.get("arn", "")

        resource = {
            "resourceId":    resource_id,
            "account":       account,
            "region":        region,
            "resourceType":  resource_type,
            "arn":           arn,
            "name":          _resolve_name(item),
            "layer":         assign_layer(resource_type),
            "tags":          item.get("tags") or {},
            "configFields":  _extract_config_fields(item),
        }
        resources.append(resource)

        for rel in item.get("relationships") or []:
            dst_id = rel.get("resourceId", "")
            if not dst_id:
                continue
            edges.append({
                "srcId":            resource_id,
                "dstId":            dst_id,
                # Config stores the human label under "name" (e.g.
                # "Is attached to NetworkAcl"), not "relationshipName".
                "relationshipName": rel.get("name", "") or rel.get("relationshipName", ""),
                "dstType":          rel.get("resourceType", ""),
                "account":          account,
            })

    return resources, edges


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def save_model(resources: list[dict], edges: list[dict]) -> None:
    MODEL_PATH.parent.mkdir(exist_ok=True)
    with open(MODEL_PATH, "w", encoding="utf-8") as f:
        json.dump({"resources": resources, "edges": edges}, f, indent=2)
    print(f"Saved {len(resources)} resources, {len(edges)} edges -> {MODEL_PATH}")


def load_model() -> tuple[list[dict], list[dict]]:
    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"No model at {MODEL_PATH}. Run model build first.")
    with open(MODEL_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data["resources"], data["edges"]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    items = parse_cache()
    resources, edges = normalize(items)

    layer_counts: dict[str, int] = {}
    for r in resources:
        layer_counts[r["layer"]] = layer_counts.get(r["layer"], 0) + 1
    for layer, count in sorted(layer_counts.items()):
        print(f"  {count:>5}  {layer}")

    save_model(resources, edges)
