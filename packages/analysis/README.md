# lantern-analysis

Deterministic program analysis. No model calls happen here.

**Inputs:** a checked-out repository directory and the SDK registry.
**Outputs:** a data-flow graph (networkx `DiGraph`, serialized to JSON) of personal-data sources, transforms, sinks, and retention points, with file and line evidence for every node and propagation step, plus a coverage summary.

Status: skeleton. Implemented in Prompt 3.
