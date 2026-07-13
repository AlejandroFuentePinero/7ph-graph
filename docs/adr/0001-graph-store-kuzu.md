# Use Kùzu as the graph store

We store the graph in Kùzu, an embedded, file-backed graph database with a Cypher interface. It runs in-process with no server, so it ships inside the same Hugging Face Space or Colab as the app, and its Cypher surface is the natural query target for the planned agentic RAG (it has existing LangChain and LlamaIndex integrations).

## Considered Options

- **NetworkX (in-memory)**: simplest, but no query language for the future RAG to target.
- **Neo4j**: richest tooling, but a server, too heavy for a personal Space or Colab.
- **DuckDB**: excellent analytics, but relational; multi-hop traversal and neighbourhood rendering are awkward.

pyvis renders the subgraphs pulled from Kùzu; Kùzu is not the visualisation layer.
