"""
Synthetic nested benchmark graph seeder (Section 1 of TESTING.md).
Builds a 6-level hierarchy crossed with a 4-hop lineage chain,
temporal supersession chain, and arbitrated conflict pair across 4 partitions.
"""
from datetime import datetime, timezone
import json
from kryptodb.daemon import keyring
from kryptodb.integrity.partition_crypto import Encryptor

# Fixed scope KEKs for evaluation
EVAL_SCOPE_KEYS = {
    "ORG:acme-structure": b"k" * 32,
    "PROJECT:alpha": b"p" * 32,
    "RESEARCH:threat-intel": b"r" * 32,
}

def setup_eval_keyring():
    """Populates the keyring with evaluation scope keys."""
    for scope, key in EVAL_SCOPE_KEYS.items():
        keyring.add_key(scope, key)

CYPHER_CLEANUP = """
MATCH (n) DETACH DELETE n
"""

CYPHER_SEED_STRUCTURAL = """
// ORG:acme-structure partition - org chart
CREATE (org:ORGANIZATION {id:"ACME-Corp", permission_scope:"ORG:acme-structure",
        source_agent_id:"org-sync", trust_weight:1.0, valid_from:datetime(), valid_to:null})

CREATE (divSec:DIVISION {id:"ACME-Security", permission_scope:"ORG:acme-structure"})
CREATE (divEng:DIVISION {id:"ACME-Engineering", permission_scope:"ORG:acme-structure"})
CREATE (org)-[:HAS_DIVISION {permission_scope:"ORG:acme-structure", source_agent_id:"org-sync",
        trust_weight:1.0, valid_from:datetime(), valid_to:null}]->(divSec)
CREATE (org)-[:HAS_DIVISION {permission_scope:"ORG:acme-structure", source_agent_id:"org-sync",
        trust_weight:1.0, valid_from:datetime(), valid_to:null}]->(divEng)

CREATE (teamIR:TEAM {id:"Security-IR", permission_scope:"ORG:acme-structure"})
CREATE (teamAppSec:TEAM {id:"Security-AppSec", permission_scope:"ORG:acme-structure"})
CREATE (teamPlatform:TEAM {id:"Eng-Platform", permission_scope:"ORG:acme-structure"})
CREATE (teamFrontend:TEAM {id:"Eng-Frontend", permission_scope:"ORG:acme-structure"})
CREATE (divSec)-[:HAS_TEAM {permission_scope:"ORG:acme-structure", trust_weight:1.0,
        source_agent_id:"org-sync", valid_from:datetime(), valid_to:null}]->(teamIR)
CREATE (divSec)-[:HAS_TEAM {permission_scope:"ORG:acme-structure", trust_weight:1.0,
        source_agent_id:"org-sync", valid_from:datetime(), valid_to:null}]->(teamAppSec)
CREATE (divEng)-[:HAS_TEAM {permission_scope:"ORG:acme-structure", trust_weight:1.0,
        source_agent_id:"org-sync", valid_from:datetime(), valid_to:null}]->(teamPlatform)
CREATE (divEng)-[:HAS_TEAM {permission_scope:"ORG:acme-structure", trust_weight:1.0,
        source_agent_id:"org-sync", valid_from:datetime(), valid_to:null}]->(teamFrontend)

CREATE (alice:PERSON {id:"Alice", permission_scope:"ORG:acme-structure"})
CREATE (bob:PERSON   {id:"Bob",   permission_scope:"ORG:acme-structure"})
CREATE (carol:PERSON {id:"Carol", permission_scope:"ORG:acme-structure"})
CREATE (dave:PERSON  {id:"Dave",  permission_scope:"ORG:acme-structure"})
CREATE (teamIR)-[:HAS_MEMBER {permission_scope:"ORG:acme-structure", trust_weight:1.0,
        source_agent_id:"org-sync", valid_from:datetime(), valid_to:null}]->(alice)
CREATE (teamIR)-[:HAS_MEMBER {permission_scope:"ORG:acme-structure", trust_weight:1.0,
        source_agent_id:"org-sync", valid_from:datetime(), valid_to:null}]->(bob)
CREATE (teamPlatform)-[:HAS_MEMBER {permission_scope:"ORG:acme-structure", trust_weight:1.0,
        source_agent_id:"org-sync", valid_from:datetime(), valid_to:null}]->(carol)
CREATE (teamPlatform)-[:HAS_MEMBER {permission_scope:"ORG:acme-structure", trust_weight:1.0,
        source_agent_id:"org-sync", valid_from:datetime(), valid_to:null}]->(dave)

// PROJECT:alpha partition
CREATE (proj:PROJECT {id:"Project-Alpha-Response", permission_scope:"PROJECT:alpha"})
CREATE (alice)-[:LEADS {permission_scope:"PROJECT:alpha", trust_weight:0.9,
        source_agent_id:"malware-detector", valid_from:datetime(), valid_to:null}]->(proj)

CREATE (task1:TASK {id:"Contain-Fox-Kitten", permission_scope:"PROJECT:alpha"})
CREATE (task2:TASK {id:"Patch-CVE-2026-11342", permission_scope:"PROJECT:alpha"})
CREATE (proj)-[:HAS_TASK {permission_scope:"PROJECT:alpha", trust_weight:0.9,
        source_agent_id:"malware-detector", valid_from:datetime(), valid_to:null}]->(task1)
CREATE (proj)-[:HAS_TASK {permission_scope:"PROJECT:alpha", trust_weight:0.9,
        source_agent_id:"malware-detector", valid_from:datetime(), valid_to:null}]->(task2)

// RESEARCH:threat-intel partition
CREATE (fk:MALWARE_FAMILY {id:"Fox-Kitten", permission_scope:"RESEARCH:threat-intel"})
CREATE (cve:CVE {id:"CVE-2026-11342", permission_scope:"RESEARCH:threat-intel"})
CREATE (fk)-[r0:EXPLOITS {id:"e-root", permission_scope:"RESEARCH:threat-intel", trust_weight:0.9,
        source_agent_id:"malware-detector", valid_from:datetime(), valid_to:null,
        derived_from:[]}]->(cve)
CREATE (task2)-[:TARGETS {permission_scope:"PROJECT:alpha", trust_weight:0.9,
        source_agent_id:"malware-detector", valid_from:datetime(), valid_to:null}]->(cve)
"""

CYPHER_SEED_LINEAGE = """
MATCH (cve:CVE {id:"CVE-2026-11342"})
MATCH (proj:PROJECT {id:"Project-Alpha-Response"})
CREATE (hyp:CONCEPT {id:"Initial-Access-Hypothesis", permission_scope:"RESEARCH:threat-intel"})
CREATE (cve)-[r1:SUGGESTS {id:"e-l1", permission_scope:"RESEARCH:threat-intel", trust_weight:0.85,
        source_agent_id:"threat-analyst", valid_from:datetime(), valid_to:null,
        derived_from:["e-root"]}]->(hyp)

CREATE (plan:CONCEPT {id:"Mitigation-Plan-DRAFT", permission_scope:"PROJECT:alpha"})
CREATE (hyp)-[r2:INFORMS {id:"e-l2", permission_scope:"PROJECT:alpha", trust_weight:0.85,
        source_agent_id:"threat-analyst", valid_from:datetime(), valid_to:null,
        derived_from:["e-l1"]}]->(plan)

MATCH (task1:TASK {id:"Contain-Fox-Kitten"})
CREATE (plan)-[r3:EXECUTES_AS {id:"e-l3", permission_scope:"PROJECT:alpha", trust_weight:0.9,
        source_agent_id:"ops-agent", valid_from:datetime(), valid_to:null,
        derived_from:["e-l2"]}]->(task1)

MATCH (alice:PERSON {id:"Alice"})
CREATE (task1)-[r4:ASSIGNED_TO {id:"e-l4", permission_scope:"ORG:acme-structure", trust_weight:1.0,
        source_agent_id:"ops-agent", valid_from:datetime(), valid_to:null,
        derived_from:["e-l3"]}]->(alice)
"""

CYPHER_SEED_TEMPORAL = """
MATCH (bob:PERSON {id:"Bob"})
CREATE (pLegacy:PROJECT {id:"Project-Legacy-Audit", permission_scope:"PROJECT:alpha"})
CREATE (bob)-[w1:WORKS_ON {id:"e-w1", permission_scope:"PROJECT:alpha", trust_weight:0.8,
        source_agent_id:"personal-assistant", valid_from:date("2026-06-01"),
        valid_to:date("2026-07-01"), superseded_by:"e-w2"}]->(pLegacy)

CREATE (proj2:PROJECT {id:"Project-Alpha-Response-Interim", permission_scope:"PROJECT:alpha"})
CREATE (bob)-[w2:WORKS_ON {id:"e-w2", permission_scope:"PROJECT:alpha", trust_weight:0.8,
        source_agent_id:"personal-assistant", valid_from:date("2026-07-01"),
        valid_to:date("2026-07-20"), superseded_by:"e-w3"}]->(proj2)

MATCH (proj:PROJECT {id:"Project-Alpha-Response"})
CREATE (bob)-[w3:WORKS_ON {id:"e-w3", permission_scope:"PROJECT:alpha", trust_weight:0.8,
        source_agent_id:"personal-assistant", valid_from:date("2026-07-20"), valid_to:null,
        superseded_by:null}]->(proj)
"""

CYPHER_SEED_CONFLICT = """
CREATE (:IOC {id:"C2-Server-IP-203.0.113.44", permission_scope:"PROJECT:alpha"})
CREATE (:IOC {id:"C2-Server-IP-203.0.113.91", permission_scope:"PROJECT:alpha"})
WITH 1 AS dummy
MATCH (ip1:IOC {id:"C2-Server-IP-203.0.113.44"}), (ip2:IOC {id:"C2-Server-IP-203.0.113.91"})
MATCH (org:ORGANIZATION {id:"ACME-Corp"})
CREATE (org)-[:HOSTED_ON {id:"e-c1", permission_scope:"PROJECT:alpha", trust_weight:0.5,
        source_agent_id:"recon-agent", valid_from:datetime(), valid_to:datetime(),
        superseded_by:"e-c2"}]->(ip1)
CREATE (org)-[:HOSTED_ON {id:"e-c2", permission_scope:"PROJECT:alpha", trust_weight:0.9,
        source_agent_id:"malware-detector", valid_from:datetime(), valid_to:null}]->(ip2)
"""

def seed_benchmark_graph():
    """Seeds the full benchmark graph into Neo4j."""
    if not neo4j_client.driver:
        return False
    setup_eval_keyring()
    neo4j_client.execute_query_with_retry(CYPHER_CLEANUP)
    neo4j_client.execute_query_with_retry(CYPHER_SEED_STRUCTURAL)
    neo4j_client.execute_query_with_retry(CYPHER_SEED_LINEAGE)
    neo4j_client.execute_query_with_retry(CYPHER_SEED_TEMPORAL)
    neo4j_client.execute_query_with_retry(CYPHER_SEED_CONFLICT)
    return True
