import json
from pathlib import Path

import networkx as nx
from pyvis.network import Network


BASE_DIR = Path(__file__).resolve().parent

KG_FILE = BASE_DIR / "knowledge_graph.json"

OUTPUT_DIR = BASE_DIR / "graph_output"
OUTPUT_DIR.mkdir(exist_ok=True)


with open(KG_FILE, encoding="utf-8") as f:
    kg = json.load(f)


# =====================================================
# DEPENDENCY GRAPH
# =====================================================

G = nx.DiGraph()

for node_id, node in kg["nodes"].items():

    if node["component"] == "backend":
        color = "#ff9999"

    elif node["component"] == "frontend":
        color = "#99ccff"

    else:
        color = "#dddddd"

    G.add_node(
        node_id,
        label=node["name"],
        title=f"""
        <b>{node['name']}</b><br>
        component: {node['component']}<br>
        complexity: {node['complexity']}<br>
        security: {node['security_level']}<br>
        lifecycle: {node['lifecycle']}
        """,
        color=color,
    )



# Chỉ lấy dependency thật
for edge in kg["edges"]:

    rel = edge["relationship_type"]

    color = "#888888"

    if rel == "api_call":
        color = "#2196F3"

    elif rel == "service_call":
        color = "#FF9800"

    G.add_edge(
        edge["from"],
        edge["to"],
        label=rel,
        color=color,
        title=f"{rel}"
    )

# size node theo degree
degree_map = dict(G.degree())

for node in G.nodes():

    G.nodes[node]["size"] = (
        20 + degree_map.get(node, 0) * 4
    )


net = Network(
    height="1000px",
    width="100%",
    directed=True,
    bgcolor="#ffffff",
)

net.from_nx(G)

net.set_options("""
{
  "layout": {
    "hierarchical": {
      "enabled": true,
      "direction": "LR",
      "sortMethod": "directed"
    }
  },

  "physics": {
    "enabled": false
  },

  "edges": {
    "smooth": {
      "enabled": true,
      "type": "dynamic"
    }
  }
}
""")

dependency_html = OUTPUT_DIR / "dependency_graph.html"

net.write_html(str(dependency_html))

print(f"[OK] Generated: {dependency_html}")


# =====================================================
# SIMPLE SERVICE GRAPH
# =====================================================

service_graph = nx.DiGraph()

for cluster in kg.get("clusters", []):

    service_graph.add_node(
        cluster["suggested_service"],
        label=cluster["suggested_service"],
    )


# service dependency từ entity dependency
entity_to_service = {}

for cluster in kg.get("clusters", []):

    service_name = cluster["suggested_service"]

    for ent in cluster["entities"]:
        entity_to_service[ent] = service_name


for edge in kg["edges"]:

    rel = edge.get("relationship_type")

    if rel not in (
        "api_call",
        "service_call",
    ):
        continue

    src = entity_to_service.get(edge["from"])
    dst = entity_to_service.get(edge["to"])

    if not src or not dst:
        continue

    if src == dst:
        continue

    service_graph.add_edge(src, dst)


service_net = Network(
    height="800px",
    width="100%",
    directed=True,
)

service_net.from_nx(service_graph)

service_net.set_options("""
{
  "physics": {
    "enabled": false
  }
}
""")

service_html = OUTPUT_DIR / "service_graph.html"

service_net.write_html(str(service_html))

print(f"[OK] Generated: {service_html}")


# =====================================================
# STATS
# =====================================================

stats = {
    "nodes": len(kg["nodes"]),
    "edges": len(kg["edges"]),
    "clusters": len(kg.get("clusters", [])),
}

stats_file = OUTPUT_DIR / "stats.json"

with open(
    stats_file,
    "w",
    encoding="utf-8"
) as f:
    json.dump(
        stats,
        f,
        indent=2,
        ensure_ascii=False,
    )
# =====================================================
# BUSINESS FLOW
# =====================================================

business = nx.DiGraph()

domains = {}

for node in kg["nodes"].values():

    domain = node["canonical_domain"]

    if domain not in domains:
        domains[domain] = True

        business.add_node(
            domain,
            label=domain.upper()
        )

for edge in kg["edges"]:

    src_domain = kg["nodes"][edge["from"]]["canonical_domain"]
    dst_domain = kg["nodes"][edge["to"]]["canonical_domain"]

    if src_domain != dst_domain:

        business.add_edge(
            src_domain,
            dst_domain
        )

business_net = Network(
    height="900px",
    width="100%",
    directed=True
)

business_net.from_nx(business)

business_net.set_options("""
{
  "layout": {
    "hierarchical": {
      "enabled": true,
      "direction": "LR"
    }
  },
  "physics": {
    "enabled": false
  }
}
""")

business_net.write_html(
    str(
        OUTPUT_DIR /
        "01_business_flow.html"
    )
)
print(f"[OK] Generated: {stats_file}")