"""
scripts/load_concepts_to_qdrant.py

STEP 2 + STEP 3 of the vector-search approach:
  - Build 11 concept nodes covering the typologies we saw in the baseline
  - Each concept aggregates ALL Supabase rule_ids for that typology
  - Embed (canonical + ' '.join(aliases) + ' [typology]') with BGE-M3
  - Load into Qdrant collection "clause_concepts" (vector size 1024, cosine)

Usage:
    python scripts/load_concepts_to_qdrant.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import requests

from builder.config import settings


# ── Concept catalogue ──────────────────────────────────────────────────────

CONCEPTS: list[dict] = [
    {
        "concept_id": "concept-integrity-pact",
        "canonical":  "Integrity Pact clause",
        "aliases": [
            "pre-bid integrity agreement",
            "anti-corruption undertaking",
            "vigilance pact",
            "IP clause",
            "integrity agreement per CVC",
        ],
        "typology_codes": ["Missing-Integrity-Pact"],
        "similarity_threshold": 0.72,
        "severity": "HARD_BLOCK",
    },
    {
        "concept_id": "concept-anti-collusion",
        "canonical":  "Anti-collusion certificate",
        "aliases": [
            "Form 3N",
            "anti-collusion undertaking",
            "no cartel certificate",
            "bid rigging declaration",
        ],
        "typology_codes": ["Missing-Anti-Collusion"],
        "similarity_threshold": 0.70,
        "severity": "WARNING",
    },
    {
        "concept_id": "concept-price-variation",
        "canonical":  "Price variation clause",
        "aliases": [
            "price adjustment clause",
            "PVC",
            "escalation clause",
            "price escalation",
            "variation in prices",
            "PVC formula",
        ],
        "typology_codes": ["Missing-PVC-Clause"],
        "similarity_threshold": 0.70,
        "severity": "HARD_BLOCK",
    },
    {
        "concept_id": "concept-judicial-preview",
        "canonical":  "Judicial Preview clearance",
        "aliases": [
            "judicial preview certificate",
            "HC judge clearance",
            "infrastructure transparency review",
            "pre-publication judicial review",
        ],
        "typology_codes": ["Judicial-Preview-Bypass"],
        "similarity_threshold": 0.75,
        "severity": "HARD_BLOCK",
    },
    {
        "concept_id": "concept-emd",
        "canonical":  "Earnest Money Deposit",
        "aliases": [
            "EMD",
            "bid security",
            "earnest money",
            "tender deposit",
            "bid bond",
        ],
        "typology_codes": ["EMD-Shortfall"],
        "similarity_threshold": 0.80,
        "severity": "HARD_BLOCK",
    },
    {
        "concept_id": "concept-pbg",
        "canonical":  "Performance Guarantee",
        "aliases": [
            "performance security",
            "PBG",
            "performance bank guarantee",
            "contract security",
            "performance bond",
            "due-fulfilment guarantee",
        ],
        "typology_codes": ["PBG-Shortfall"],
        "similarity_threshold": 0.78,
        "severity": "HARD_BLOCK",
    },
    {
        "concept_id": "concept-bid-validity",
        "canonical":  "Bid validity period",
        "aliases": [
            "tender validity",
            "validity of bid",
            "bid validity period",
            "tender shall remain valid",
            "validity of tender",
        ],
        "typology_codes": ["Bid-Validity-Short"],
        "similarity_threshold": 0.74,
        "severity": "WARNING",
    },
    {
        "concept_id": "concept-e-procurement",
        "canonical":  "E-procurement portal",
        "aliases": [
            "e-procurement",
            "electronic procurement",
            "AP e-procurement portal",
            "apeprocurement.gov.in",
            "GePNIC",
            "CPP portal",
            "online tender submission",
        ],
        "typology_codes": ["E-Procurement-Bypass"],
        "similarity_threshold": 0.74,
        "severity": "HARD_BLOCK",
    },
    {
        "concept_id": "concept-reverse-tender",
        "canonical":  "Reverse tendering procedure",
        "aliases": [
            "reverse auction",
            "reverse tendering",
            "electronic reverse auction",
            "downward bidding",
            "L1 reverse auction",
            "tender-cum-reverse-auctioning",
        ],
        "typology_codes": ["Reverse-Tender-Evasion"],
        "similarity_threshold": 0.74,
        "severity": "HARD_BLOCK",
    },
    {
        "concept_id": "concept-open-tender",
        "canonical":  "Open tender / public advertisement",
        "aliases": [
            "open tender enquiry",
            "OTE",
            "advertised tender",
            "public advertisement",
            "press notification of tender",
            "Indian Trade Journal advertisement",
        ],
        "typology_codes": ["Single-Source-Undocumented"],
        "similarity_threshold": 0.72,
        "severity": "HARD_BLOCK",
    },
    {
        "concept_id": "concept-force-majeure",
        "canonical":  "Force Majeure clause",
        "aliases": [
            "act of god",
            "FM event",
            "non-political event",
            "indirect political event",
            "political event",
            "uncontrollable event",
            "excused performance",
        ],
        "typology_codes": ["Missing-Force-Majeure"],
        "similarity_threshold": 0.70,
        "severity": "WARNING",
    },
]


# ── Fetch rule_ids by typology from Supabase ────────────────────────────────

def _fetch_rule_ids_by_typology(typologies: list[str]) -> dict[str, list[str]]:
    REST = settings.supabase_rest_url
    headers = {
        "apikey": settings.supabase_anon_key,
        "Authorization": f"Bearer {settings.supabase_anon_key}",
        "Range-Unit": "items",
        "Range": "0-1999",
    }
    quoted = ",".join(f'"{t}"' for t in typologies)
    url = (f"{REST}/rest/v1/rules?select=rule_id,typology_code,severity,layer"
           f"&typology_code=in.({quoted})")
    res = requests.get(url, headers=headers, timeout=30)
    res.raise_for_status()
    out: dict[str, list[str]] = {t: [] for t in typologies}
    for r in res.json():
        out[r["typology_code"]].append(r["rule_id"])
    return out


def hydrate_concepts() -> list[dict]:
    """Attach rule_ids to each concept based on typology_codes."""
    all_typs = sorted({t for c in CONCEPTS for t in c["typology_codes"]})
    by_typ = _fetch_rule_ids_by_typology(all_typs)
    print(f"Fetched rule_ids for {len(all_typs)} typologies:")
    for t in all_typs:
        print(f"  {t:30s} → {len(by_typ.get(t, []))} rules")
    print()
    out = []
    for c in CONCEPTS:
        rids = sorted({rid for t in c["typology_codes"] for rid in by_typ.get(t, [])})
        out.append({**c, "rule_ids": rids})
    return out


# ── Embed + load into Qdrant ────────────────────────────────────────────────

def main() -> int:
    from sentence_transformers import SentenceTransformer
    from qdrant_client import QdrantClient
    from qdrant_client.http import models as qm

    QDRANT_HOST = os.environ.get("QDRANT_HOST", "localhost")
    QDRANT_PORT = int(os.environ.get("QDRANT_PORT", "6333"))
    COLLECTION  = "clause_concepts"

    concepts = hydrate_concepts()
    total_rules = sum(len(c["rule_ids"]) for c in concepts)
    print(f"Built {len(concepts)} concept nodes covering {total_rules} rule_ids total")

    print("\nLoading BGE-M3 model… (first run downloads ~2 GB)")
    t0 = time.perf_counter()
    model = SentenceTransformer("BAAI/bge-m3")
    print(f"  loaded in {time.perf_counter()-t0:.1f}s")
    dim = model.get_sentence_embedding_dimension()
    print(f"  embedding dimension: {dim}")

    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, timeout=30)
    if client.collection_exists(COLLECTION):
        print(f"\nDropping existing collection '{COLLECTION}'…")
        client.delete_collection(COLLECTION)
    client.create_collection(
        collection_name=COLLECTION,
        vectors_config=qm.VectorParams(size=dim, distance=qm.Distance.COSINE),
    )
    print(f"Created collection '{COLLECTION}' (size={dim}, cosine)")

    # Build texts to embed: canonical + aliases joined
    texts: list[str] = []
    payloads: list[dict] = []
    for i, c in enumerate(concepts):
        text = f"{c['canonical']}. {' '.join(c['aliases'])}."
        texts.append(text)
        payloads.append({
            "concept_id":           c["concept_id"],
            "canonical":            c["canonical"],
            "aliases":              c["aliases"],
            "typology_codes":       c["typology_codes"],
            "rule_ids":             c["rule_ids"],
            "similarity_threshold": c["similarity_threshold"],
            "severity":             c["severity"],
            "embed_text":           text,
        })

    print(f"\nEmbedding {len(texts)} concepts…")
    t0 = time.perf_counter()
    vectors = model.encode(texts, normalize_embeddings=True).tolist()
    print(f"  embedded in {time.perf_counter()-t0:.1f}s")

    points = [
        qm.PointStruct(id=i, vector=vectors[i], payload=payloads[i])
        for i in range(len(concepts))
    ]
    client.upsert(collection_name=COLLECTION, points=points)
    info = client.get_collection(COLLECTION)
    print(f"Loaded {info.points_count} vectors into '{COLLECTION}'")

    # Persist concept catalogue alongside
    OUT = Path("data/vector_concepts.json")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(concepts, indent=2, ensure_ascii=False))
    print(f"Concept catalogue saved to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
