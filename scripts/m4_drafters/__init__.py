"""Module 4 — Communicator: 3 drafter pilots (M4.2).

Drafters convert Module 3 structured findings into bidder-facing or
internal communications. Each drafter:
  1. Reads source findings (EligibilityMatrix / TenderRanking / BidAnomalyFinding)
  2. Composes content_en from a fixed template + finding citations
  3. Computes deterministic audit_id (SHA256 of type+recipient+tender+sorted_finding_ids)
  4. Emits Communication kg_node with source_finding_node_ids[] for drilldown
  5. Writes Markdown artifact to /tmp/m4_drafts/

Idempotent re-runs: _delete_prior_communications_of_type() before emit.
"""
