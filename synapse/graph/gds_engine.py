"""
Neo4j Graph Data Science (GDS) integration for global retrieval (Community Detection).
Implements Milestone 9.1: Louvain Community Detection.
"""
from synapse.storage.neo4j_client import neo4j_client
import logging

logger = logging.getLogger(__name__)

class GDSEngine:
    def __init__(self):
        pass

    def run_louvain_community_detection(self, partition_scope: str) -> bool:
        """
        Projects a subgraph for the given partition and runs the Louvain algorithm
        to detect communities. Writes the resulting community ID back to the nodes.
        """
        if not neo4j_client.driver:
            return False
            
        graph_name = f"graph_{partition_scope.replace(':', '_')}"
        
        # 1. Drop existing projection if it exists
        try:
            neo4j_client.execute_query_with_retry(
                "CALL gds.graph.drop($graph_name, false)", 
                graph_name=graph_name
            )
        except Exception:
            pass
            
        # 2. Project the active subgraph for this partition using Cypher Projection
        project_query = """
        CALL gds.graph.project.cypher(
            $graph_name,
            'MATCH (n:Entity {permission_scope: $scope}) RETURN id(n) AS id, labels(n) AS labels',
            'MATCH (n:Entity)-[r:FACT {permission_scope: $scope}]->(m:Entity) WHERE r.valid_to IS NULL RETURN id(n) AS source, id(m) AS target, r.trust_weight AS weight'
        )
        YIELD graphName, nodeCount, relationshipCount
        """
        try:
            neo4j_client.execute_query_with_retry(project_query, graph_name=graph_name, scope=partition_scope)
        except Exception as e:
            logger.error(f"Failed to project graph {graph_name} (GDS may not be installed): {e}")
            return False
            
        # 3. Run Louvain and write community_id back to nodes
        louvain_query = """
        CALL gds.louvain.write(
            $graph_name,
            {
                relationshipWeightProperty: 'weight',
                writeProperty: 'community_id'
            }
        )
        YIELD communityCount, modularity
        """
        try:
            res, _, _ = neo4j_client.execute_query_with_retry(louvain_query, graph_name=graph_name)
            logger.info(f"Louvain completed on {partition_scope}. Communities: {res[0]['communityCount']}")
            return True
        except Exception as e:
            logger.error(f"Failed to run Louvain on {graph_name}: {e}")
            return False
        finally:
            try:
                neo4j_client.execute_query_with_retry("CALL gds.graph.drop($graph_name, false)", graph_name=graph_name)
            except Exception:
                pass

    def run_knn_similarity(self, partition_scope: str) -> bool:
        """
        Projects nodes for a partition and runs the KNN algorithm using their 
        semantic embeddings. Draws [SIMILAR_TO] edges for deduplication.
        """
        if not neo4j_client.driver:
            return False
            
        graph_name = f"knn_{partition_scope.replace(':', '_')}"
        
        # 1. Drop existing projection if it exists
        try:
            neo4j_client.execute_query_with_retry(
                "CALL gds.graph.drop($graph_name, false)", 
                graph_name=graph_name
            )
        except Exception:
            pass
            
        # 2. Project nodes with embeddings
        project_query = """
        CALL gds.graph.project(
            $graph_name,
            {
                Entity: {
                    label: 'Entity',
                    properties: 'embedding'
                }
            },
            '*'
        )
        """
        # Note: We must filter by partition_scope, but GDS node projections with Cypher allow WHERE.
        project_query = """
        CALL gds.graph.project.cypher(
            $graph_name,
            'MATCH (n:Entity {permission_scope: $scope}) WHERE n.embedding IS NOT NULL RETURN id(n) AS id, labels(n) AS labels, n.embedding AS embedding',
            'MATCH (n:Entity)-[r:FACT]->(m:Entity) WHERE 1=0 RETURN id(n) AS source, id(m) AS target' // No edges needed for KNN
        )
        YIELD graphName, nodeCount
        """
        try:
            neo4j_client.execute_query_with_retry(project_query, graph_name=graph_name, scope=partition_scope)
        except Exception as e:
            logger.error(f"Failed to project graph {graph_name} for KNN: {e}")
            return False
            
        # 3. Run KNN to draw SAME_AS edges
        knn_query = """
        CALL gds.knn.write(
            $graph_name,
            {
                nodeProperties: ['embedding'],
                writeRelationshipType: 'SAME_AS',
                writeProperty: 'score',
                topK: 1,
                sampleRate: 1.0,
                randomSeed: 42,
                concurrency: 1,
                similarityCutoff: 0.95
            }
        )
        YIELD relationshipsWritten
        """
        try:
            res, _, _ = neo4j_client.execute_query_with_retry(knn_query, graph_name=graph_name)
            logger.info(f"KNN Similarity completed on {partition_scope}. New SAME_AS edges: {res[0]['relationshipsWritten']}")
            return True
        except Exception as e:
            logger.error(f"Failed to run KNN on {graph_name}: {e}")
            return False
        finally:
            try:
                neo4j_client.execute_query_with_retry("CALL gds.graph.drop($graph_name, false)", graph_name=graph_name)
            except Exception:
                pass

    def get_communities(self, partition_scopes: list[str]) -> dict:
        """
        Returns nodes grouped by their community_id for the given partitions.
        """
        if not neo4j_client.driver:
            return {}
            
        query = """
        MATCH (n:Entity)
        WHERE n.permission_scope IN $scopes AND n.community_id IS NOT NULL
        RETURN n.community_id AS community, collect(n.id) AS nodes, n.permission_scope AS scope
        ORDER BY size(nodes) DESC
        LIMIT 50
        """
        try:
            records, _, _ = neo4j_client.execute_query_with_retry(query, scopes=partition_scopes)
            
            communities = {}
            for r in records:
                scope = r["scope"]
                comm_id = f"{scope}_{r['community']}"
                communities[comm_id] = r["nodes"]
                
            return communities
        except Exception:
            return {}
