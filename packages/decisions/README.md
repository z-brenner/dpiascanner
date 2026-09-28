# lantern-decisions

The decision layer. Answers fixed, typed, versioned questions about graph nodes and edges with calibrated probabilities. Never generates free text.

**Inputs:** batches of `DecisionRequest` (state payload JSON, question set id and version, target id).
**Outputs:** `DecisionResult` per question (answer, probability, full distribution, provider metadata).

Status: skeleton. Implemented in Prompt 2.
