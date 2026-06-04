"""
Knowledge Graph Agent — deterministic, không gọi AI.
Build knowledge graph từ entities.json + requirements.md.
"""
import os
import json
from planning.knowledge_graph_builder import (
    build_knowledge_graph,
    save_knowledge_graph,
)


def run(prompt: str) -> str:
    ent_path = "docs/entities.json"
    req_path = "docs/requirements.md"

    if not os.path.exists(ent_path):
        raise RuntimeError("entities.json not found — run requirement-agent first")

    with open(ent_path, encoding="utf-8") as f:
        entities = json.load(f)

    req_text = ""
    if os.path.exists(req_path):
        with open(req_path, encoding="utf-8") as f:
            req_text = f.read()

    kg = build_knowledge_graph(entities, req_text)
    save_knowledge_graph(kg)

    print(
        f"      [knowledge-graph] {kg['node_count']} nodes, "
        f"{kg['edge_count']} edges, "
        f"{len(kg['clusters'])} clusters, "
        f"{len(kg['architect_hints'])} hints"
    )
    return "KNOWLEDGE_GRAPH_DONE"