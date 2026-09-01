"""
stitch.py -- infer cross-account edges from shared identifiers.

Input:  raw configurationItems from parser.parse_cache()
Output: list of cross-account edge dicts with edgeClass="cross_account"

Heuristics:
  tgw_attachment -- two TGW attachments share the same transitGatewayId
  vpc_peering    -- VPCPeeringConnection requester/accepter span accounts
  vpc_endpoint   -- Interface endpoint serviceName embeds an in-scope account ID
  iam_trust      -- IAM role trust Principal.AWS contains an in-scope ARN
  ram_share      -- RAM ResourceShare principal is an in-scope account ID
"""

import json
import re
from collections import defaultdict
from pathlib import Path

IN_SCOPE_ACCOUNTS = {
    "124074140119",
    "162521700530",
    "387075078863",
    "886031930818",
    "954475336656",
}

CROSS_EDGES_PATH = Path(__file__).parent / "cache" / "cross_account_edges.json"

_ACCOUNT_RE = re.compile(r'\b(' + '|'.join(IN_SCOPE_ACCOUNTS) + r')\b')
_ARN_ACCOUNT_RE = re.compile(r'arn:aws[^:]*:iam::(\d{12}):')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_config(item: dict) -> dict:
    raw = item.get("configuration")
    if not raw:
        return {}
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}


def _edge(src_id: str, dst_id: str, src_account: str, dst_account: str,
          reason: str, label: str = "") -> dict:
    return {
        "srcId":            src_id,
        "dstId":            dst_id,
        "srcAccount":       src_account,
        "dstAccount":       dst_account,
        "edgeClass":        "cross_account",
        "stitchReason":     reason,
        "relationshipName": label or reason,
    }


# ---------------------------------------------------------------------------
# Heuristics
# ---------------------------------------------------------------------------

def _stitch_tgw(items: list[dict]) -> list[dict]:
    # tgw_id -> [(account, attachment_resource_id)]
    by_tgw: dict[str, list[tuple[str, str]]] = defaultdict(list)

    for item in items:
        if item.get("resourceType") != "AWS::EC2::TransitGatewayAttachment":
            continue
        cfg = _parse_config(item)
        tgw_id = cfg.get("transitGatewayId") or cfg.get("TransitGatewayId")
        if not tgw_id:
            continue
        by_tgw[tgw_id].append((item.get("awsAccountId", ""), item.get("resourceId", "")))

    edges = []
    for tgw_id, attachments in by_tgw.items():
        for i in range(len(attachments)):
            for j in range(i + 1, len(attachments)):
                a1, r1 = attachments[i]
                a2, r2 = attachments[j]
                if a1 != a2:
                    edges.append(_edge(r1, r2, a1, a2, "tgw_attachment",
                                       f"shares TransitGateway {tgw_id}"))
    return edges


def _stitch_vpc_peering(items: list[dict]) -> list[dict]:
    edges = []
    for item in items:
        if item.get("resourceType") != "AWS::EC2::VPCPeeringConnection":
            continue
        cfg = _parse_config(item)
        requester = cfg.get("requesterVpcInfo") or {}
        accepter = cfg.get("accepterVpcInfo") or {}

        req_account = requester.get("ownerId", "")
        acc_account = accepter.get("ownerId", "")
        req_vpc = requester.get("vpcId", "")
        acc_vpc = accepter.get("vpcId", "")
        peering_id = item.get("resourceId", "")
        my_account = item.get("awsAccountId", "")

        if req_account == acc_account:
            continue

        if acc_account in IN_SCOPE_ACCOUNTS and acc_vpc:
            edges.append(_edge(peering_id, acc_vpc, my_account, acc_account,
                               "vpc_peering", "VPC Peering accepter"))
        if req_account in IN_SCOPE_ACCOUNTS and req_account != my_account and req_vpc:
            edges.append(_edge(peering_id, req_vpc, my_account, req_account,
                               "vpc_peering", "VPC Peering requester"))
    return edges


def _stitch_vpc_endpoints(items: list[dict]) -> list[dict]:
    # Interface endpoint serviceNames for PrivateLink to shared services
    # embed the service owner's account ID, e.g.:
    # com.amazonaws.vpce.us-east-2.vpce-svc-<id>  (no account)
    # or a custom service name that contains the owner account
    edges = []
    for item in items:
        if item.get("resourceType") != "AWS::EC2::VPCEndpoint":
            continue
        cfg = _parse_config(item)
        if cfg.get("vpcEndpointType") not in ("Interface", "interface"):
            continue
        service_name = cfg.get("serviceName", "")
        my_account = item.get("awsAccountId", "")
        endpoint_id = item.get("resourceId", "")

        m = _ACCOUNT_RE.search(service_name)
        if m:
            target_account = m.group(1)
            if target_account != my_account:
                edges.append(_edge(endpoint_id, target_account, my_account, target_account,
                                   "vpc_endpoint",
                                   f"Interface endpoint to service in {target_account}"))
    return edges


def _stitch_iam_trust(items: list[dict]) -> list[dict]:
    # Build ARN -> (resource_id, account) index for roles we know about
    role_by_arn: dict[str, tuple[str, str]] = {}
    for item in items:
        if item.get("resourceType") == "AWS::IAM::Role":
            arn = item.get("ARN") or item.get("arn", "")
            if arn:
                role_by_arn[arn] = (item.get("resourceId", ""), item.get("awsAccountId", ""))

    edges = []
    for item in items:
        if item.get("resourceType") != "AWS::IAM::Role":
            continue
        cfg = _parse_config(item)
        trust_doc = cfg.get("assumeRolePolicyDocument")
        if isinstance(trust_doc, str):
            try:
                trust_doc = json.loads(trust_doc)
            except json.JSONDecodeError:
                continue
        if not isinstance(trust_doc, dict):
            continue

        my_account = item.get("awsAccountId", "")
        role_id = item.get("resourceId", "")

        for stmt in trust_doc.get("Statement", []):
            principal = stmt.get("Principal", {})
            aws_principals: list[str] = []
            if isinstance(principal, str):
                aws_principals = [principal]
            elif isinstance(principal, dict):
                p = principal.get("AWS", [])
                aws_principals = [p] if isinstance(p, str) else (p if isinstance(p, list) else [])
            elif isinstance(principal, list):
                aws_principals = principal

            for arn in aws_principals:
                if not isinstance(arn, str):
                    continue
                m = _ARN_ACCOUNT_RE.search(arn)
                if m:
                    target_account = m.group(1)
                    if target_account != my_account and target_account in IN_SCOPE_ACCOUNTS:
                        target_res = role_by_arn.get(arn, (arn, target_account))[0]
                        edges.append(_edge(role_id, target_res, my_account, target_account,
                                           "iam_trust", f"trusts {arn}"))
                elif arn in IN_SCOPE_ACCOUNTS and arn != my_account:
                    edges.append(_edge(role_id, arn, my_account, arn,
                                       "iam_trust", f"trusts account {arn}"))
    return edges


def _stitch_ram(items: list[dict]) -> list[dict]:
    edges = []
    for item in items:
        if item.get("resourceType") != "AWS::RAM::ResourceShare":
            continue
        cfg = _parse_config(item)
        principals = cfg.get("principals") or cfg.get("Principals") or []
        if isinstance(principals, str):
            principals = [principals]
        my_account = item.get("awsAccountId", "")
        share_id = item.get("resourceId", "")
        seen_targets: set[str] = set()

        for principal in principals:
            if not isinstance(principal, str):
                continue
            # bare account ID
            if principal in IN_SCOPE_ACCOUNTS and principal != my_account:
                if principal not in seen_targets:
                    seen_targets.add(principal)
                    edges.append(_edge(share_id, principal, my_account, principal,
                                       "ram_share", f"RAM share to account {principal}"))
                continue
            # embedded account ID inside an ARN or org path
            m = re.search(r'\b(\d{12})\b', principal)
            if m:
                target = m.group(1)
                if target in IN_SCOPE_ACCOUNTS and target != my_account and target not in seen_targets:
                    seen_targets.add(target)
                    edges.append(_edge(share_id, target, my_account, target,
                                       "ram_share", f"RAM share to account {target}"))
    return edges


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def infer_cross_account_edges(items: list[dict]) -> list[dict]:
    """Run all heuristics and return a deduplicated cross-account edge list."""
    all_edges: list[dict] = []
    all_edges.extend(_stitch_tgw(items))
    all_edges.extend(_stitch_vpc_peering(items))
    all_edges.extend(_stitch_vpc_endpoints(items))
    all_edges.extend(_stitch_iam_trust(items))
    all_edges.extend(_stitch_ram(items))

    # deduplicate: treat (srcId, dstId) and (dstId, srcId) as the same pair per reason
    seen: set[tuple[str, str, str]] = set()
    unique: list[dict] = []
    for e in all_edges:
        key = (min(e["srcId"], e["dstId"]), max(e["srcId"], e["dstId"]), e["stitchReason"])
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return unique


def save_cross_account_edges(edges: list[dict]) -> None:
    CROSS_EDGES_PATH.parent.mkdir(exist_ok=True)
    with open(CROSS_EDGES_PATH, "w", encoding="utf-8") as f:
        json.dump(edges, f, indent=2)
    print(f"Saved {len(edges)} cross-account edges -> {CROSS_EDGES_PATH}")


def load_cross_account_edges() -> list[dict]:
    if not CROSS_EDGES_PATH.exists():
        raise FileNotFoundError(f"No cross-account edges at {CROSS_EDGES_PATH}. Run stitch first.")
    with open(CROSS_EDGES_PATH, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from parser import parse_cache
    from model import load_model

    items = parse_cache()
    resources, intra_edges = load_model()

    cross_edges = infer_cross_account_edges(items)

    by_reason: dict[str, int] = {}
    for e in cross_edges:
        r = e["stitchReason"]
        by_reason[r] = by_reason.get(r, 0) + 1

    print(f"Cross-account edges: {len(cross_edges)}")
    for reason, count in sorted(by_reason.items()):
        print(f"  {count:>4}  {reason}")
    print(f"Intra-account edges: {len(intra_edges)}")
    print(f"Total edges:         {len(intra_edges) + len(cross_edges)}")

    save_cross_account_edges(cross_edges)
