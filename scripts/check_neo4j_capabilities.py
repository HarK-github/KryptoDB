#!/usr/bin/env python3
"""
scripts/check_neo4j_capabilities.py
Checks Neo4j version, edition, and verifies whether relationship vector indexes
(fact_vector_index) can be created and queried on relationships in this Neo4j instance.
"""
import os
import sys
import json
from neo4j import GraphDatabase

def check_capabilities():
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "password")

    report = {
        "connected": False,
        "version": None,
        "edition": None,
        "node_vector_index_supported": False,
        "relationship_vector_index_supported": False,
        "reified_fact_required": False,
        "errors": []
    }

    try:
        driver = GraphDatabase.driver(uri, auth=(user, password))
        driver.verify_connectivity()
        report["connected"] = True
    except Exception as exc:
        report["errors"].append(f"Connection failed: {exc}")
        print(json.dumps(report, indent=2))
        return report

    with driver.session() as session:
        # 1. Query version and edition
        try:
            res = session.run("CALL dbms.components() YIELD name, versions, edition RETURN name, versions, edition")
            rec = res.single()
            if rec:
                report["version"] = rec["versions"][0] if rec["versions"] else "unknown"
                report["edition"] = rec["edition"]
        except Exception as exc:
            report["errors"].append(f"Failed to query components: {exc}")

        # 2. Check Node Vector Index Support
        try:
            session.run("""
                CREATE VECTOR INDEX test_node_vector_idx IF NOT EXISTS
                FOR (n:TestNode) ON (n.embedding)
                OPTIONS {indexConfig: {`vector.dimensions`: 4, `vector.similarity_function`: 'cosine'}}
            """)
            report["node_vector_index_supported"] = True
            # Clean up test index
            session.run("DROP INDEX test_node_vector_idx IF EXISTS")
        except Exception as exc:
            report["errors"].append(f"Node vector index failed: {exc}")

        # 3. Check Relationship Vector Index Support
        try:
            session.run("""
                CREATE VECTOR INDEX test_rel_vector_idx IF NOT EXISTS
                FOR ()-[r:TEST_REL]-() ON (r.embedding)
                OPTIONS {indexConfig: {`vector.dimensions`: 4, `vector.similarity_function`: 'cosine'}}
            """)
            report["relationship_vector_index_supported"] = True
            # Clean up test index
            session.run("DROP INDEX test_rel_vector_idx IF EXISTS")
        except Exception as exc:
            report["relationship_vector_index_supported"] = False
            report["reified_fact_required"] = True
            report["errors"].append(f"Relationship vector index unsupported in this Neo4j version/edition: {exc}")

    driver.close()
    print(json.dumps(report, indent=2))
    return report

if __name__ == "__main__":
    check_capabilities()
