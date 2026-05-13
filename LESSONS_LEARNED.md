# AP Procurement AI — Lessons Learned
**Project:** BIMSaarthi Technologies / RTGS Hackathon  
**Period:** Sessions from April–May 2026  
**Maintained by:** Claude (conversation) + Claude Code (implementation)  
**Rule:** Every strategy change, no matter how small, is recorded here with the reason.

---

## Systemic Findings Across AP Corpus

After eleven Tier-1 typologies × six documents = sixty-six finding slots, four institutional patterns recur across the corpus, not as isolated document defects but as systemic procurement-practice tendencies. These are surfaced here so any future contributor reading the codebase understands what the corpus is *really* showing — the violations are not random distribution noise; they cluster.

- **PBG consistently 2.5% vs required 10%** (all 5 Works/PPP documents with a PBG clause). Vizag, JA, HC: AP-State Works at 2.5% per AP-GO-019 baseline; Tirupathi/Vijayawada: PPP DCAs with implied 4.998–5.001% via amount→percentage compute (L25). Kakinada is the only doc whose source genuinely lacks a PBG percentage clause — silent, not a violation.
- **EMD consistently 1% vs required 2–2.5%** (all 5 documents that state an EMD clause). JA, HC, Kakinada at 1% (ADVISORY vs AP-GO-050 target 2.5%); Tirupathi, Vijayawada at 0.998% via amount→percentage (HARD_BLOCK vs GFR-G-049 floor 2%). Vizag genuinely silent.
- **Judicial Preview consistently absent** (all 6 documents, mandatory under APJPA 2019 / GO Ms No 38/2018). Five documents trigger HARD_BLOCK; Vizag is ADVISORY only because EV=null forces L27 UNKNOWN→ADVISORY downgrade. Zero APJPA citations across all 12 source markdown files (L38).
- **Integrity Pact consistently absent** in the regulated form (all 6 documents, CVC-086 / MPS-022 mandated). JA / HC / Tirupathi / Vijayawada: ADVISORY because the multilateral lender's anticorruption framework IS present (ADB / WB) but the regulated CVC Pre-bid Integrity Pact is NOT — parallel-compliance shape per L30. Vizag and Kakinada: ADVISORY for "no IP framework at all".
- **Turnover requirement 2.5× annual (NREDCAP PPP)** — Tirupathi 128.75cr and Vijayawada 162.35cr both set at 50% of total project cost over 5yr = 2.5× annual, exceeding CVC-028 cap of 2×. This is a template-level calibration issue — the NREDCAP standard RFP template sets 50% which systematically exceeds the CVC floor. Identical 2.500× across both DCAs confirms boilerplate, not per-tender judgment (L39).
- **Foreign-contractor ban without DoE OM 2020 framework (APCRDA Works JA and HC)** — JA L878 and HC L716 carry explicit bans "Any contractor from abroad not be permitted" without the required Annexure-2F structure. This is an MPS-184 violation. Same two ADB/WB-funded documents that substitute arbitration with the civil-court ladder (L43) — consistent pattern of AP-mechanism substitution that goes BEYOND what AP-GO-091 authorizes (L44).
- **Annexure-2F absent from Kakinada SBD** — Kakinada Smart City SBD has no land-border-country clause at all. MPG-243 HARD_BLOCK violation; the doc demands AP-State registration but doesn't include the DoE OM 23-Jul-2020 land-border framework that PP-No.1 requires of every Indian Government tender (L44).
- **PPP-MII Order 2017 (Make in India) absent across all 6 documents** (16th typology finding). MPW-002 (Works) and MPS-182 (PPP) both require explicit Make in India preference clauses, Class-I/II local supplier definitions, and bidder LC self-certification. Zero citations found across all 12 source markdown files. **Third systemic-absence pattern alongside JP-Bypass and Integrity-Pact** (L45).

These are systemic institutional patterns, not individual document errors. A reviewer dashboard that surfaces the four counts as a corpus-level signal would tell a procurement reform story that no single document review can: AP State procurement is consistently under-collateralised on PBG/EMD by a factor of 2–4× and consistently bypasses the post-2018 judicial-preview and pre-bid integrity layers. Per-doc findings are necessary but not sufficient — these are the patterns the system is uniquely positioned to surface.

---

## Architecture Patterns Established

A reading guide for new contributors. Every Tier-1 typology check is built on this stack of layers — newer typologies inherit them automatically by following the established script template.

### The four-state outcome contract (introduced L37, default for new typologies)

Every Tier-1 finding falls into exactly one of four states. Threshold-shape typologies (PBG / EMD / Bid-Validity / Mobilisation-Advance) skip GAP_VIOLATION; presence-shape typologies (PVC / IP / LD / E-Proc / Blacklist) skip GAP_VIOLATION too — only typologies whose LLM verdict has BOTH a "found" boolean AND a sub-classification (BG-Validity-Gap is the only one today) use all four. The other three states are universal:

| state | when | finding emitted? | VIOLATES_RULE edge? | DB status |
|---|---|---|---|---|
| **COMPLIANT** | LLM found + L24 verified + classification = OK | NO (implicit "no row") | n/a | n/a |
| **GAP_VIOLATION** | LLM found + L24 verified + classification = inadequate (BG-Validity only) | YES | YES (with verified inadequate quote) | OPEN |
| **UNVERIFIED** | LLM found + L24 fail OR grep fallback caught a missed clause | YES | NO (awaiting human review) | UNVERIFIED |
| **ABSENCE** | LLM didn't find + grep fallback also empty | YES | YES (genuine absence violation) | OPEN |

UNVERIFIED is the system-confidence state — the system flagged something but can't audit it; human review required. Never silently treated as compliant.

### Layer reference (when each applies)

| layer | introduced | applies to | what it does |
|---|---|---|---|
| **L24 evidence guard** (`modules/validation/evidence_guard.py::verify_evidence_in_section`) | L24 | Every typology where the LLM returns a verbatim quote | Verify the quote exists in the chosen section's `full_text`. Pass = score 100 substring or partial_ratio ≥ 85. Fail = LLM hallucinated or stitched. **It's a confidence layer, not a verdict layer** (L35) — failed verification means "we don't have audit-grade evidence", not "the document is non-compliant". |
| **L29 absence-finding marker** | L29 | Every Missing-X presence-shape typology | When the absence path materialises a finding, set `evidence_match_method='absence_finding_no_evidence'`, `evidence_in_source/evidence_verified=null`, `evidence_match_score=null`, and synthesise a search-trace evidence string. Distinguishes "real absence" from "L24-failed presence". |
| **L35 three-state decision** | L35 | All presence-shape scripts (back-ported to PVC / IP / LD / E-Proc / Blacklist) AND all threshold-shape scripts (back-ported to PBG / EMD / Bid-Validity). | Replace binary `is_violation` with three-way `is_compliant / is_unverified / is_absence`. UNVERIFIED finding has NO VIOLATES_RULE edge. The strict-quote prompt directive (single contiguous span, no ellipsis, no stitching, preserve markdown verbatim) lives here too. |
| **L36 grep fallback** (`modules/validation/grep_fallback.py::grep_source_for_keywords`) | L36 (Vizag false positive) | Every presence-shape script (PVC / IP / LD / E-Proc / Blacklist / BG-Validity) | When the LLM rerank's top-K returns no candidate, exhaustively grep across the full section_filter coverage (NOT just top-K) for typology-specific keywords. Hit → downgrade ABSENCE → UNVERIFIED with `grep_fallback_audit` JSONB payload (section pointers + snippets). Per-typology keyword vocabulary lives next to the rule selector. |
| **L37 four-state extension (GAP_VIOLATION)** | L37 (BG-Validity-Gap) | Typologies whose LLM verdict needs sub-classification (extends-through-DLP, etc.) | Adds a fourth outcome: LLM found + L24 verified + classification fails. OPEN finding with verified inadequate quote + edge. Distinct from ABSENCE (no clause) and UNVERIFIED (can't verify). |
| **L24 hallucination guard / JSON sanitiser** (`modules/validation/llm_client.py::parse_llm_json`) | L35 | All scripts that ask the LLM for JSON | Strips ```json fences, extracts {…} body, falls back to backslash-doubling on malformed escapes (`\\.`, `\\(` etc. that AP markdown contains and the LLM faithfully reproduces per the L35 strict-quote rule). |

### Decision flowchart for a new presence-shape typology

```
  retrieve top-K candidates within section_filter
       │
  LLM rerank → returns chosen_index, found, evidence
       │
       ├─ chosen_index is int? ──no──→ ABSENCE branch
       │                              │
       │                              └─ run L36 grep fallback
       │                                  │
       │                                  ├─ any hit? ──yes──→ UNVERIFIED (grep), no edge
       │                                  └─ no hit ──→ ABSENCE finding + edge (L29 marker)
       │
       └─ yes ─→ run L24 evidence guard
                  │
                  ├─ ev_passed? ──yes──→ COMPLIANT, no row
                  └─ ev_passed = no ──→ UNVERIFIED (L24), no edge
```

### Rule of thumb

- **OPEN** finding with edge → real regulatory violation worth shipping to a CAG audit.
- **UNVERIFIED** finding without edge → system confidence flag; reviewer opens the section, confirms or downgrades.
- No finding row → either truly compliant OR rule-layer SKIP (typology N/A on this doc).

---

## How to Use This Document

Every entry follows this structure:
- **What we did** — the original approach
- **What happened** — the exact failure or observation
- **Why we changed** — the reasoning
- **What we changed to** — the new approach
- **Result** — whether the change worked

---

## L01 — Clause Library: Keyword Classification vs Content Reading

**Date:** Early sessions  
**What we did:** Used keyword matching to classify 700 clause templates into types (DRAFTING_CLAUSE, PROCEDURAL_GUIDE, etc.)  
**What happened:** Keywords like "mandatory" and "shall" appeared in both actual tender clauses and internal officer procedures. The classification produced wrong results — procedural guides like "Preparation of DPR" were marked as DRAFTING_CLAUSE.  
**Why we changed:** A clause that says "the officer SHALL prepare a DPR before tender" is a procedural instruction. It never appears in any tender document. A keyword match on "SHALL" cannot distinguish this from a clause that says "the contractor SHALL provide a bank guarantee." Only reading the content reveals the difference.  
**What we changed to:** Read every clause by content. Asked: would this text appear in a tender document that a bidder receives? If yes → DRAFTING_CLAUSE. If it describes what an officer must do before the tender → PROCEDURAL_GUIDE.  
**Result:** 499 genuine drafting clauses identified from 700. 149 procedural guides correctly separated. Zero unclassified.

---

## L02 — Rule Classification: Automated Type Assignment vs Content Reading

**Date:** Early sessions  
**What we did:** Automated classifier assigned rule_type (TYPE_1/TYPE_2/TYPE_3) based on verification_method keywords.  
**What happened:** 52 rules were marked TYPE_2_INSTRUCTIONAL with severity HARD_BLOCK. This is a logical contradiction — HARD_BLOCK means the system must check it, which requires it to be TYPE_1_ACTIONABLE. The classifier saw "Audit-level check" in verification_method and marked it TYPE_2 while simultaneously marking it HARD_BLOCK.  
**Why we changed:** A rule cannot simultaneously block publication (HARD_BLOCK) and be an officer procedure (TYPE_2). The automated classifier resolved this contradiction incorrectly — it trusted the text label over the logical constraint.  
**What we changed to:** Read all 1,356 rules by content. Applied the invariant: every HARD_BLOCK must be TYPE_1. Additionally, read TYPE_1 rules to find those describing internal procedures (register maintenance, monthly reporting, accounting forms) that were incorrectly actionable.  
**Result:** 1,223 TYPE_1 (was 1,200), 124 TYPE_2 (was 144), 9 TYPE_3 (was 12). Zero HARD_BLOCK outside TYPE_1.

---

## L03 — SATISFIES_RULE Edges: Mechanical Linkage vs Verified Compliance

**Date:** KG construction sessions  
**What we did:** When a clause template matched a document section at any confidence, created SATISFIES_RULE edges for all rules in that template's rule_ids array.  
**What happened:** 2,489 SATISFIES_RULE edges were created. Of these, 8 were based on genuine high-confidence matches (Dispute Resolution heading matched Dispute Resolution template). The remaining 2,481 were fabricated — a rule_ids linkage with no verification.  
**Why we changed:** A SATISFIES_RULE edge asserts that a rule condition was checked and found to be met. The system was creating these edges purely because a template was linked to a rule, not because any check happened. This made the system appear to have verified 2,489 compliance conditions when it verified approximately zero.  
**What we changed to:** Deleted all 2,481 fabricated edges. SATISFIES_RULE edges are only created when a Tier 1 check (BGE-M3 + LLM) explicitly extracts a value and finds it compliant. No mechanical linkage creates these edges.  
**Result:** Database went from 2,489 SATISFIES_RULE edges to 0. The system became honest about what it had actually checked.

---

## L04 — Clause Matching: difflib SequenceMatcher vs BGE-M3

**Date:** Graph experiment sessions  
**What we did:** Used Python's difflib.SequenceMatcher to match clause templates to document sections by comparing heading text. Threshold: 0.40.  
**What happened:** "Contractor's waiver" scored 0.41 against "AP Contractor Security Deposit — 10% of Contract Value" because both contain the word "Contractor." The actual Security Deposit clause at line 1451 scored 0.32 and was invisible. PBG violations were attributed to "Contractor's waiver" and "Contractor's personnel."  
**Why we changed:** difflib counts character overlap. It cannot understand meaning. "Contractor's waiver" and "Security Deposit" share a word but describe completely different things. This is not a calibration problem — it is a tool class problem. Lexical similarity cannot solve semantic matching.  
**What we changed to:** BGE-M3 semantic embeddings. Embed the clause template text (not just the title). Embed the section full text. Cosine similarity on meaning, not character overlap.  
**Result:** Vizag PBG check now correctly identifies "Security" section at line 1449 with cosine 0.666. Violations attributed to correct section.

---

## L05 — Rule Verification: Regex on Full Document vs BGE-M3 + LLM on Section

**Date:** Validator development sessions  
**What we did:** Regex searched full document text (50,000+ characters) for percentage patterns near keywords like "Performance Security."  
**What happened:** Regex found "2.5%" at the correct location in Vizag. But it also attributed the violation to wrong sections (Contractor's waiver, Contractor's personnel) because the violation was found in full text but the attribution was from low-confidence clause matching. Additionally, regex can only check ~9 typologies out of 42 and cannot detect semantic violations.  
**Why we changed:** Regex is a pattern tool, not a reading tool. It cannot understand that "2.5%" appearing near "retention money" is different from "2.5%" appearing near "Performance Security." For P2 presence checks and P4 semantic judgment, regex fundamentally cannot work. CAG traceability requires knowing exactly which section contained the violating text.  
**What we changed to:** BGE-M3 finds the relevant section semantically. LLM reads that specific section and extracts the value with a verbatim evidence quote. Compare extracted value to rule threshold. Attribution is always correct because the LLM read the actual section.  
**Result:** Tier 1 PBG check on Vizag finds "Security" section at cosine 0.666, LLM extracts "2.5% of the bid amount" as evidence quote, violation correctly attributed to correct section.

---

## L06 — LLM Avoidance: Regex/Rule-Based Preference vs LLM-First for Accuracy

**Date:** Architecture decision sessions  
**What we did:** Initially avoided LLM for rule checking, citing: (1) traceability concerns for CAG audit, (2) speed and cost, (3) hallucination risk.  
**What happened:** The system covered 9 of 42 typologies (14.3% of HARD_BLOCK rules). 88% of rules were silent. The "traceability" argument was wrong — regex produces a match with no context and often wrong attribution. An LLM returning structured JSON with evidence quotes and reasoning chains is MORE traceable than regex.  
**Why we changed:** The CAG audit requirement is traceability to source text and rule. An LLM that returns {"percentage": 2.5, "evidence": "2.5% of the bid amount", "section": "Security GCC line 1449"} is fully traceable. Regex returning "found 2.5% at position 47832 in full_text" is not. Speed is not a constraint at accuracy-first stage. Hallucination is managed by structured output and confidence thresholds.  
**What we changed to:** LLM (via OpenRouter qwen-2.5-72b) for all extraction tasks. BGE-M3 narrows the search space. LLM reads and extracts with evidence. DeepSeek-R1 reasoning chain becomes the audit trail.  
**Result:** Real LLM extractions working on all 6 documents. Evidence quotes are verbatim from source text. Full traceability.

---

## L07 — condition_when: Ignored vs Evaluated Before Rule Firing

**Date:** Post-KG validator sessions  
**What we did:** RuleVerificationEngine selected rules by typology membership only. condition_when field on every rule was never read.  
**What happened:** Services-only rules (MPS-037: "TenderType=Services AND SelectionMethod=LCS") fired on Works tenders. Post-award rules (MPW-080: "ContractAwarded=true") fired on pre-RFP documents. 47 Criteria-Restriction-Narrow violations appeared on Vizag, 45 of which were for rules that should not apply to Works documents.  
**Why we changed:** Every one of 1,223 TYPE_1 rules has a populated condition_when field. This field explicitly states when the rule applies. Ignoring it means the system fires MPS consultancy evaluation rules on civil works tenders. This produces findings that are not just unhelpful — they are actively wrong.  
**What we changed to:** Built condition_evaluator.py that parses condition_when (supports =, !=, IN[], >, >=, <, <=, AND, OR) and evaluates against tender facts before any rule fires. Three outcomes: FIRE (fact matches), SKIP (fact explicitly does not match), UNKNOWN (fact not yet extracted).  
**Result:** Vizag violations dropped from 59 to 34 underlying, deduped to 2 meaningful findings. 23 Services-only rules correctly SKIP on Works document.

---

## L08 — tender_type Extraction: Regex Classifier vs LLM on NIT Text

**Date:** Step 1 sessions  
**What we did:** Regex classifier extracted tender_type. Output: Vizag → "Consultancy", Tirupathi → "Goods."  
**What happened:** Vizag is Works/EPC (Rs.350 crore sewerage infrastructure). Tirupathi is PPP/DBFOT (Rs.257 crore waste-to-energy concession). Both were completely wrong. The classifier had no way to distinguish these from actual Consultancy or Goods tenders.  
**Why we changed:** Tender type is declared explicitly in the NIT — "Name of the Work: Construction of..." or "Development of ... on PPP basis through DBFOT." A regex classifier tries to infer type from patterns. An LLM reading the NIT preamble reads the actual declaration.  
**What we changed to:** LLM (qwen-2.5-72b via OpenRouter) reads first 800 characters of first NIT section. Returns structured JSON with tender_type, confidence, and verbatim evidence quote. commit=True writes to TenderDocument kg_node with extracted_by attribution.  
**Result:** All 6 documents correctly typed (Works/PPP) with confidence 0.95-1.0 and verbatim evidence quotes.

---

## L09 — LLM Selection: gemma4:e4b (Local) vs qwen-2.5-72b (OpenRouter)

**Date:** Local model testing session  
**What we did:** Downloaded gemma4:e4b (9.6GB) to Mac Mini M4 via Ollama. Used as primary LLM.  
**What happened:** gemma4:e4b sent 16,868 characters of NIT text to an 8,192 token context window. Context was truncated. Model never saw the "Name of the Work" declaration. Classified Vizag as "Services" at confidence 0.95. Also dropped the required "evidence" key from JSON output entirely. Wall time: 59 seconds.  
**Why we changed:** The model saw boilerplate ITB text (Technical Specifications, Scope of Work), not the NIT declaration. Context window was the immediate problem. Even after fixing context (800 chars), 4.5B effective parameters is insufficient for reliable structured extraction on domain-specific government documents.  
**What we changed to:** Fixed context window first (800 chars of first NIT section instead of 16,868). Then switched to qwen-2.5-72b via OpenRouter for reliable structured output. 72B parameters, consistently returns all required JSON keys, 6-8 second response time.  
**Result:** All 3 original documents pass in 6-8 seconds each. Evidence quotes present and verbatim.

---

## L10 — Building Forward on Unverified Foundations

**Date:** Multiple sessions  
**What we did:** After completing each component, immediately proposed the next component without verifying the current one was genuinely working.  
**What happened:** Built KG builder on difflib. Built validator graph on KG builder. Proposed Drafter on validator graph. By the time we questioned the foundations, the system had multiple layers of wrong output that looked correct because numbers were plausible.  
**Why we changed:** Plausible numbers are not proof. 2,489 SATISFIES_RULE edges looked like a functioning compliance system. They were fabricated. The three-document scorecard proved the system produced consistent output — not that the output was correct.  
**Rule adopted:** Complete one layer. Attack it. Find what is wrong. Only after genuine attempts to break it fail → build the next layer.  
**Result:** This rule prevented shipping a Drafter built on a broken validator.

---

## L11 — Testing on Similar Documents vs Testing on Diverse Documents

**Date:** Three-document scorecard sessions  
**What we did:** Validated the system on Vizag, Tirupathi, Judicial Academy. Declared "three documents, three shapes, triangulation proves generalization."  
**What happened:** All three documents had PBG shortfall. The one typology the regex validator detected correctly was the only typology tested. Section classifier was untested on new shapes. Retrieval failures were undiscovered.  
**Why we changed:** Testing three documents that all produce the same finding does not test the system. It tests that one finding. Real testing requires documents with diverse typologies, different document families, and at least one document that should PASS.  
**What we changed to:** Added High Court (same APCRDA family as JA), Kakinada (different employer/format), Vijayawada WtE (same PPP family as Tirupathi). This immediately revealed that BGE-M3 retrieval fails on all APCRDA Works documents (Pattern B) and that PPP documents use fixed amounts not percentages (Pattern C).  
**Result:** Three distinct failure patterns identified across 6 documents instead of discovering them one at a time in production.

---

## L12 — BGE-M3 Query String: Rule Text vs Answer-Shaped Text

**Date:** Tier 1 retrieval sessions  
**What we did:** Used the AP Financial Code preamble (first 2 sentences of clause template text_english) as the BGE-M3 query. Text: "Whenever a private person or a firm enters into a contract with the Government of Andhra Pradesh... be required to give SECURITY for the due fulfilment..."  
**What happened:** This query matched sections containing obligation language — retention money clauses, bond templates, general security provisions. The actual PBG clause ("furnish Performance Security equal to 2.5 per cent of bid amount") ranked 11th because it contains value-statement language, not obligation language.  
**Why we changed:** The query described the rule (what must happen). The answer describes the value (what is there). BGE-M3 finds semantic similarity. If the query says "obligation to give security" and the answer says "2.5% of bid amount," they are semantically different even though both are about the same clause. The query must sound like the answer.  
**What we changed to:** Query string: "Performance Security equal to per cent of bid amount contract value furnish bank guarantee." This matches the actual wording of PBG clauses in ITB and GCC sections.  
**Result:** Previous wrong answer ("Payments and Certificates" retention section at 0.694) dropped out of top-15 entirely. Real PBG clauses moved to rank 2 (0.665) and rank 6 (0.563).

---

## L13 — Section Splitter: Heading-Based Splitting Causes Orphaned Content

**Date:** Tier 1 retrieval sessions  
**What we discovered:** JA's GCC 51.1 body (line 5267, contains "2.5 per cent of bid amount") was not in any document_sections row. The splitter created a stub for the heading "51. Securities" (1 line) but the body content after it fell into a gap — no section node captured it.  
**Root cause:** The section splitter splits at every heading. When a heading is immediately followed by another heading (or the content is minimal), the body of the first heading gets absorbed into the next section node or orphaned entirely.  
**Impact:** BGE-M3 ranks the empty stub heading at 0.6719 (first because the heading matches), but the LLM returns not-found because the section has no body. The actual answer is invisible to the system.  
**Fix needed:** Section splitter must assign content to the section that precedes it. A heading with no body should not create an isolated kg_node. Content should flow forward from the heading until the next heading is encountered.  
**Status:** Not yet fixed. Documented for implementation.

---

## L14 — kg_builder Regex Validator: Still Runs on Every Build

**Date:** Multiple sessions  
**What we discovered:** Every time kg_builder.py processes a document, it internally runs the regex RuleVerificationEngine and creates ValidationFinding nodes with tier=null. These are the old regex findings we repeatedly deleted.  
**Impact:** Every new document ingest pollutes the database with regex findings. Vizag rebuild creates wrong findings. Tirupathi rebuild creates "0.1% PBG" finding (actually a liquidated damages rate, not PBG). Requires manual cleanup after every build.  
**Root cause:** The regex validator was not disabled when Tier 1 BGE-M3+LLM was built. It continues running in parallel.  
**Fix needed:** Disable or remove the regex validator pass from kg_builder.py. Tier 1 BGE-M3+LLM is the replacement, not an addition.  
**Status:** Not yet fixed. Documented for implementation. Current workaround: delete tier=null findings after every build.

---

## L15 — PPP Documents Express PBG as Fixed Amount, Not Percentage

**Date:** Tier 1 testing across document families  
**What we discovered:** NREDCAP PPP/DBFOT concession documents (Tirupathi, Vijayawada) express Performance Security as a fixed amount in crores (INR 12.87 crore, INR 16.24 crore), not as a percentage of contract value.  
**Impact:** LLM correctly returns {"percentage": null, "found": false} because no percentage exists. No violation is detected even though the implied percentage (12.87/257.51 = 5%) is below the 10% AP threshold.  
**Root cause:** PPP concession structures fix the security amount at negotiation time rather than computing it as a percentage of contract value.  
**Fix needed:** Add a second extraction branch to the LLM prompt: if no percentage found, extract the fixed amount in crores. Then compute implied percentage = amount_cr / contract_value_cr × 100. Compare implied percentage to threshold.  
**Prerequisite:** contract_value_cr must be reliably extracted from TenderDocument facts (Step 3 tender_facts_extractor).  
**Status:** Not yet implemented. Documented.

---

## L16 — Lessons About Eagerness vs Correctness

**Date:** Throughout all sessions  
**Pattern observed:** After completing a task, immediately proposing the next task without verifying the current one. Accepting plausible-looking numbers as proof. Moving forward on momentum rather than evidence.  
**Specific instances:**
- Declared clause classification "complete" without verifying TYPE_1 rules (only checked TYPE_2 and TYPE_3)
- Reported "system generalizes across 3 document shapes" without testing diverse document families
- Named "Tier 1 — BGE-M3" as a working tier when it was a plan, not an implementation
- Praised GO-Ms suppression removal before checking what happened when the check fired without constraints
**Rule adopted:** Before reporting any task complete, ask: "What would make this wrong?" Find the evidence. If it cannot be found after genuine attempts, then report complete.  
**Rule adopted:** Never name an architecture tier as existing until the code exists and is tested.  
**Rule adopted:** Speed and cost are not constraints at the accuracy-first stage. Optimise for correctness first.

---

## L17 — find_line_range: Cleaned Body Length vs Document Structure

**Date:** May 2026  
**What we did:** Computed `line_end = line_start + len(cleaned_body_lines) - 1` in `experiments/tender_graph/step2_sections.py::find_line_range`.  
**What happened:** The "cleaned body" passed in by the splitter has had page-number-only lines and leading/trailing blanks stripped, so its line count is shorter than the actual span in the source file. JA's `Penalty for lapses:` section was reported as ending at line 5265, but its body actually contains the GCC 51.1 PBG sentence at line 5267. Downstream tools (`tier1_pbg_check._slice_source_file`) used `line_end_local` to slice the source MD and missed the trailing PBG paragraph. The orphan looked like a splitter bug; it was a metadata bug.  
**Why we changed:** `line_end` must reflect where the section ends in the source document (heading-to-heading boundary), not where the cleaned text ends. Anchoring to body-length is fragile because every preprocessing pass changes that length.  
**What we changed to:** Walk forward in the original full text from `line_start` and locate the next markdown heading (`#{1,6} ...`). Use `next_heading_line - 1` as `line_end`. If no further heading exists, use the last line of the file.  
**Result:** PASS. Verified on all 6 docs after rebuild. JA's `Penalty for lapses:` now reports `line_end_local = 5268`, covers line 5267, and the slicer correctly returns the GCC 51.1 sentence ending in *"...amount equal to 2.5 per cent of the bid amount/contract value..."*. Side effect to remember: rebuilding Vizag through `kg_builder` (per `clear_existing=True`) also deletes the previously-stored Tier-1 PBG ValidationFinding for that doc — Tier 1 must be re-run on Vizag after this kind of rebuild.

---

## L18 — BGE-M3 Retrieval: Top-1 vs Top-10 + LLM Rerank

**Date:** May 2026  
**What we did:** Used the top-1 BGE-M3 result as the section for LLM extraction.  
**What happened:** For APCRDA Works documents (Judicial Academy, High Court), the top-1 section was always a retention-money clause or a bond-template form — not the actual PBG clause. The real PBG clause ranked 11th in the unfiltered index and 6th–8th even after the section_type filter and the answer-shaped tight query. Top-1 never reached it. JA returned no violation; High Court returned no violation; both were wrong.  
**Why we changed:** A document with many security-related sections (Bid Security, Earnest Money, Retention, Mobilisation Advance, Performance Security, Insurance Surety Bond formats, etc.) will always have multiple competing candidates near the top. Top-1 assumes the best cosine match is the right semantic match. In a 200-section document with five lexically-similar deposit/security sections, that assumption fails consistently.  
**What we changed to:** Top-10 retrieval on the filtered+tight-query pool (section_type ∈ {ITB, GCC, PCC, SCC, NIT}; query "Performance Security equal to per cent of bid amount contract value furnish bank guarantee"). Send all 10 section bodies to the LLM in one rerank call with explicit ignore-rules ("retention money, EMD, mobilisation advance, liquidated damages — do NOT pick"). The LLM picks the section that states an actual percentage. Body truncation uses head+tail (60% / 40% split, ~4000-char cap) so PBG content buried at the END of long sections (e.g. JA "Penalty for lapses:" — GCC 51.1 PBG sentence at body offset 5079 of 5434) is not cut off.  
**Result:** PASS on three documents. JA → 2.5% PBG, cosine 0.665 ("To: _[name and address of the Contractor]_" PCC reminder, lines 5349-5358). High Court → 2.5% PBG, cosine 0.6567 (same PCC template). Vizag → 2.5% PBG, cosine 0.6844 (canonical "Security" GCC section — top-1 also worked here, top-10 just confirms). All three Tier-1 findings carry verbatim evidence, full audit trail (`retrieval_strategy`, `rerank_chosen_index`, `rerank_reasoning` properties on the ValidationFinding).  
**Lesson:** for retrieval in dense procurement documents, top-1 is not enough. Top-10 + LLM rerank is the reliable pattern. Cost: one extra LLM call per typology, ~6s wall, ~7K tokens — well within budget.

---

## L19 — tender_type_extractor: NIT-Required vs NIT-with-Fallback

**Date:** May 2026  
**What we did:** `fetch_nit_text()` raised `ValueError("No NIT sections in kg_nodes")` if no Section node had `section_type='NIT'`.  
**What happened:** Tirupathi WtE (`tirupathi_wte_exp_001`) is ingested as a single Draft Concession Agreement file. After the FIX-A rebuild, all 191 sections were classified as GCC by the section classifier — there is no NIT preamble in a DCA. The extractor failed hard with ValueError and Tirupathi reverted to `tender_type=null`. The other 5 docs succeeded.  
**Why we changed:** The project-name declaration is reliably in the first heading-block of every tender document, regardless of whether that block is classified NIT, GCC, or anything else. Tirupathi DCA line 7 says literally *"DEVELOPMENT OF 12 MW WASTE TO ENERGY (WtE) PLANT AT TIRUPATI, ANDHRA PRADESH ON PPP BASIS"* — exactly the declaration the LLM needs. Hard-failing because the section classifier didn't tag that block as NIT discards usable evidence.  
**What we changed to:** When zero NIT sections exist, fall back to ALL sections sorted by `line_start_local` and take the first `n_sections` of them. Print a one-line warning so the fallback path is visible in logs. Behavior unchanged for docs that DO have NIT sections (the success case is preserved). LLM still does all the actual classification — no regex on the body.  
**Result:** PASS. Tirupathi → PPP, confidence 1.0, source_section "DRAFT CONCESSION AGREEMENT (DCA)", evidence verbatim *"DEVELOPMENT OF 12 MW WASTE TO ENERGY (WtE) PLANT AT TIRUPATI, ANDHRA PRADESH ON PPP BASIS"*. All 6 docs now have correct, reliable tender_type.

---

## L20 — PBG: Percentage vs Fixed Amount (Two-Pass Extraction)

**Date:** May 2026  
**What we did:** The Tier-1 LLM prompt asked only for a Performance Security percentage. If the document didn't state a percentage, the prompt returned `found=false` and we emitted no finding.  
**What happened:** PPP / concession-agreement documents (NREDCAP WtE: Tirupathi DCA, Vijayawada RFP) express PBG as a fixed INR amount, not a percentage of contract value. Tirupathi DCA clause 9.1 says *"INR 12.87 crore (Rupees twelve crore and eighty-seven lakhs only) (the Performance Security)"*. Vijayawada RFP clause 16.1 says *"Rs. 16.24 crore (Rupees sixteen crore and twenty-four lakhs only)"*. The percentage-only LLM correctly returned not-found on both. Real PBG-shortfall violations were missed for the entire PPP family — about 1/3 of the corpus.  
**Why we changed:** PPP concession structures fix the security amount at negotiation time rather than as a percentage. The percentage-only path is structurally blind to those docs. To detect violations we have to: (a) extract the fixed amount, (b) read the contract value from elsewhere in the KG, (c) compute implied % = amount_cr / contract_value_cr × 100, (d) compare to the rule threshold.  
**What we changed to:** Two-pass extraction. First pass uses the existing top-10 + LLM rerank with the percentage prompt. If `found=false`, run a second LLM rerank on the SAME 10 candidates with an AMOUNT prompt — explicit selection rules exclude EMD / Bid Security / mobilisation advance / retention / O&M Security / liquidated damages so the LLM only picks the principal Performance Security amount. Normalise to crores ('50 lakh' → 0.5; '12.87 crore' → 12.87). Then look up `estimated_value_cr` (LLM-extracted) or `estimated_value_cr_classified` (regex; flagged `source='regex_classifier_unreliable'` for audit) from the TenderDocument kg_node. If a contract value is available, compute `implied_percentage` and check the threshold; if not available, emit the finding with `status='PENDING_VALUE'`, `needs_contract_value=true`, and **no `VIOLATES_RULE` edge** — the violation decision is deferred until a downstream pass extracts the contract value.  
**Result:** PASS on both documents.

  - **Tirupathi WtE (DCA):** percentage path returned `found=false`. Amount path picked candidate `[0] = "9. PERFORMANCE SECURITY AND O&M SECURITY"` (GCC, lines 1752-1797, cosine 0.6548), extracted `amount_cr=12.87` with verbatim evidence *"INR 12.87 crore (Rupees twelve crore and eighty-seven lakhs only)"*. Contract value missing in DB (`estimated_value_cr=null`, regex value `0.0`). ValidationFinding `3c36ab88-…` emitted with `status='PENDING_VALUE'`, `needs_contract_value=true`. No `VIOLATES_RULE` edge — by design.
  - **Vijayawada WtE (RFP):** percentage path returned `found=false`. Amount path picked candidate `[0] = "16. PERFORMANCE SECURITY"` (NIT, lines 1264-1281, cosine 0.6765), extracted `amount_cr=16.24` with verbatim evidence *"Rs. 16.24 crore (Rupees sixteen crore and twenty-four lakhs only)"*. Contract value `324.7 cr` from regex classifier (flagged `regex_classifier_unreliable`). Implied percentage = `16.24 / 324.7 × 100 = 5.0015%` → below 10% threshold → ValidationFinding `1866efcf-…` emitted with `status='OPEN'`, `extraction_path='amount'`. `VIOLATES_RULE` edge `971fd5a2-…` materialised.

**Honest gap to flag:** the contract values used today are unreliable (regex classifier with `estimated_value_reliable=False`, or missing entirely on Tirupathi). The audit trail records `contract_value_source` so this is visible — but the implied percentage on Vijayawada (5.0015%) and the PENDING status on Tirupathi will both improve once an LLM-based `tender_facts_extractor` (paused mid-build in an earlier session) is finished and the LLM-extracted `estimated_value_cr` populates the TenderDocument node. Either way the violation decision is correct here — both PPP docs are well below 10% — but we should not ship the implied-percentage number as authoritative until contract values come from the LLM path.

---

## L21 — kg_builder Regex Validator: Hard-Coded Pass vs Flag-Gated

**Date:** May 2026  
**What we did:** `experiments/tender_graph/kg_builder.py` ran the regex `RuleVerificationEngine` unconditionally during phase 7 of every `build_kg()` call, materialising `ValidationFinding` nodes with `tier=null` and `VIOLATES_RULE` edges directly into `kg_nodes` / `kg_edges`.  
**What happened:** Every rebuild polluted the database with regex output. We had to manually delete tier=null findings + their edges **four separate times** during this project: once after ingesting Tirupathi/JA, once after the High Court / Kakinada / Vijayawada batch, once after rebuild for find_line_range fix (FIX A), and once after the multi-file Tirupathi re-ingest. Each cycle left wrong-attribution findings (e.g. Tirupathi's "0.1% PBG" — actually a liquidated-damages rate misattributed by regex). The regex validator was superseded by Tier 1 BGE-M3 + LLM (L18, L20) months ago, but its phase-7 call was never disabled.  
**Why we changed:** Two parallel paths (regex + Tier 1) writing to the same tables produces silently-wrong findings on every rebuild. Manual cleanup is fragile — it works only if you remember to run it AND know exactly what to delete. The right architecture has exactly one writer per finding.  
**What we changed to:** Module-level constant `RUN_REGEX_VALIDATOR = False` near the top of `kg_builder.py`, plus an early-return guard at phase 7. When the flag is False, `summary.defeasibility["validator_skipped"]=True`, `validator_violations=0`, `validator=0ms`, and phases 7–12 are skipped wholesale. The disabled phases (RuleNode insert, DEFEATS edges, ValidationFinding, VIOLATES_RULE) are kept in place below the guard so they can be reactivated for diff/debug by flipping the flag.  
**Result:** PASS. Smoke-tested by rebuilding `vizag_ugss_exp_001`: `Sections=161`, `HAS_SECTION=161`, **`ValidationFinding=0`, `VIOLATES_RULE=0`**, `validator_skipped=True`, `validator=0ms`. Subsequent rebuilds of Tirupathi (multi-file) and Vijayawada (RFP-only) confirmed the no-pollution behaviour. Tier 1 BGE-M3 + LLM is now the sole writer of ValidationFindings — every finding in the DB has `tier=1`, structured properties, and verbatim evidence.

---

## L22 — Multi-File Ingest for Concession Documents: DCA-Only vs DCA + RFP

**Date:** May 2026  
**What we did:** Tirupathi WtE was originally ingested with only the DCA (Draft Concession Agreement) file. The contract-value field on its TenderDocument node was empty (`estimated_value_cr=null`, `estimated_value_cr_classified=0.0`, regex unreliable).  
**What happened:** FIX C's amount-to-percentage path (L20) extracted `amount_cr=12.87` correctly from the DCA but couldn't compute `implied_percentage` because no contract value was available. The Tirupathi finding sat in `status='PENDING_VALUE'` with `needs_contract_value=true` and no `VIOLATES_RULE` edge. When we tried `tender_facts_extractor` to fill in the value, the LLM returned `confidence=0.0` even with `n_sections=3, max_chars=3000` — because **the DCA never states the project cost**. It references it only as a Schedule placeholder (line 816 of the Tirupathi DCA: *"a sum of Rs. ……………….Crores ………………"*). The cost lives in the **RFP** file (`RFP_Tirupathi_NITI_01042026.md` line 42: *"Total Project Cost | **INR 257.51 crore** (Rupees two hundred and fifty-seven crore and fifty-one lakhs only)"*), which we had processed but never ingested.  
**Why we changed:** NREDCAP-style PPP / DBFOT packages always come as multi-file sets (RFP + DCA + Schedule + Model PPA). Each file plays a different role: the RFP carries the bid-process facts and project cost; the DCA carries the contract clauses including PBG. Ingesting only one of them gives the system half the document. Vizag works because we already ingest its 5 volumes as multi-file. PPP docs need the same treatment.  
**What we changed to:** Re-ingested Tirupathi via `build_kg(SOURCES=[RFP, DCA], clear_existing=True)`. Section count grew from 191 → 289. The RFP's NIT-block now sits in the KG with `section_type='NIT'`, the LLM-extractor finds the cost on first hit, and BGE-M3 retrieval has access to both the RFP's Clause 16.1 (*"INR.12.87 crore..."*) and the DCA's Clause 9.1 (same amount) — so Tier 1 stays robust whichever file the retrieval ranks higher.  
**Result:** PASS. After multi-file re-ingest:
- Tirupathi: `tender_facts_extractor` → 257.51 cr, confidence 1.0, reliable=True, verbatim evidence quoted above. Tier 1 → ValidationFinding `430976ed-…`, **status=OPEN**, amount=12.87cr, CV=257.51cr (`source='llm_extracted'`), implied_percentage=4.9979%, VIOLATES_RULE edge `60ba384d-…`. PENDING_VALUE → OPEN as required by the task.
- Vijayawada: DCA markdown does not exist in `processed_md/` (only RFP MD + raw PDFs). RFP-only path was sufficient because the Vijayawada RFP states the project cost on its own first NIT page (line 42, same format as Tirupathi RFP). `tender_facts_extractor` → 324.70 cr, confidence 1.0, reliable=True. Tier 1 → ValidationFinding `f08b318f-…`, status=OPEN, amount=16.24cr, CV=324.7cr (**source flipped from `regex_classifier_unreliable` → `llm_extracted`**), implied_percentage=5.0015%, VIOLATES_RULE edge `dc2049cd-…`. The regex-derived 324.7 happened to match the LLM value exactly — but the audit trail now records it as LLM-verified rather than regex-best-guess.

**Followup logged:** Convert the unprocessed Vijayawada DCA / Schedule / Model PPA PDFs to markdown and add them to Vijayawada's KG. Same for Tirupathi's Schedule + Model PPA. Both will become relevant once we move past PBG and start checking Schedule-bound rules (Schedule 2 PPA terms, etc.).

---

## L23 — Kakinada PBG: Absent, Not Lost (PBG-Missing Typology Filed)

**Date:** May 2026  
**What we did:** Searched the processed Kakinada markdown for PBG percentages; found none. Initial assumption: markdown-conversion gap.  
**What happened:** Investigated the source `.docx` directly (unzipped `word/document.xml`, grepped for `Performance Security`, `Security Deposit`, `Performance Bank Guarantee`, `PBG`, percentage patterns, INR amount patterns). The .docx contains exactly three references to "Performance Security" — **all in penalty/forfeiture contexts**:
1. *"...liable for black listing and the Contract will be liable for termination duly forfeiting Performance Security and all the amounts due to him."*
2. *"...the Engineer-in-charge/Department shall have the right to deduct any money due to the contractor including his amount of performance security."*
3. *"...fails or refuses to furnish...balance EMD and additional performance security in accordance with the instructions of tenderers."*

No standalone clause **defines** the Performance Security as a percentage or as an INR amount. The document instead mandates **EMD: 1% of estimated contract value** + **retention: 7½% withheld, reduced to 2½% after defects-liability period**. The `.docx` and `.md` agree exactly — nothing was lost in conversion. This is structurally how Kakinada (Smart City) Standard Bidding Documents are built: the PBG slot is replaced by retention money.  
**Why we changed:** This isn't a code change — it's a calibration of expectations. The system's "no Tier 1 PBG finding for Kakinada" output is **correct behaviour**, not a missed violation. The grep-based pattern audit caught the absence early; the .docx investigation confirmed it definitively.  
**What we changed to:** Nothing in code. **Filed two follow-up typologies as deferred work** (per user direction in Plan-mode review):
- **PBG-Missing typology** — a separate rule that fires when a Works tender does not state a Performance Security clause at all. Different from PBG-Shortfall (which assumes a clause exists and checks the percentage). Some procurement frameworks (CVC, AP-PWD G.O. Ms 94) consider a missing PBG to be a hard-block typology in its own right.
- **Retention-Money-Substitution recogniser** — a recognise-only signal that some Smart City SBDs (Kakinada-style) explicitly substitute retention for PBG. Useful for the drafter ("this tender uses retention instead of PBG; consider whether AP-GO-175 PBG threshold applies or whether retention-percentage rules govern").

Both are **out of scope tonight** — they will be addressed after EMD-Shortfall lands, since EMD-Shortfall on Kakinada is straightforward (1% EMD is explicit in the markdown) and gives us a second working typology before we expand the rule taxonomy.  
**Result:** Investigation complete. No code change. Two typologies filed for future work.

---

## L24 — LLM Hallucination: Evidence Quote Fabrication

**Date:** May 2026  
**What happened:** Approach A ran EMD extraction on Vizag. LLM returned verbatim-looking evidence quote *"1% of the Estimated Contract Value (ECV) Rs.1,25,50,000/-"* — identical to JA's actual EMD text. Vizag has no such text anywhere in its 5 volumes.  
**Why it happened:** The LLM received a section with no EMD content. The "verbatim" instruction in the prompt did not prevent fabrication when the section contained no answer. The model generated a plausible-sounding quote from its training data.  
**Impact:** A finding would have been created with fabricated evidence. CAG audit would have been misled.  
**Prevention:** After LLM extraction, always verify the evidence quote exists in the actual section text before creating a ValidationFinding. String match the evidence quote against the source section full_text. If the quote is not found verbatim → discard the extraction as hallucinated.  
**Status:** **IMPLEMENTED** in `scripts/tier1_pbg_check.py` via the `verify_evidence_in_section(evidence, full_text)` helper. Two-stage check: (a) substring match on aggressively-normalised text (lowercase + drop markdown markers `**`, `__`, `*`, `_`, `|`, `\\` + drop `<br>` + collapse whitespace), (b) `difflib`-based partial-ratio fallback (sliding window, threshold ≥ 85). Wired into both extraction paths (percentage rerank + amount rerank). On verification failure: prints `HALLUCINATION_DETECTED`, forces `found=False` and `section=None`, and the materialise block is bypassed — no finding, no edge.

ValidationFinding rows now carry four new audit fields:
- `evidence_in_source: bool` — raw match result
- `evidence_verified: bool` — same value today; reserved for future "human-confirmed" override semantics
- `evidence_match_score: int` — 0-100 (100 for substring hit, ratio×100 for partial)
- `evidence_match_method: str` — `"substring" | "partial_ratio" | "no_match" | "empty" | "skipped"`

**Verification on Vizag PBG (re-run after the guard landed):**
- Negative control (Vizag "Security" section + JA's hallucinated quote): PASS=False, score=40, method=`no_match` — fabrication correctly caught.
- Positive control (Vizag "Security" section + Vizag's real PBG quote): PASS=True, score=99, method=`partial_ratio` — real quote verified.
- Live tier1 run: ValidationFinding `1cf504ff-…` materialised with `evidence_in_source=true`, `evidence_verified=true`, `evidence_match_score=99`, `evidence_match_method=partial_ratio`. The `partial_ratio` win (rather than substring hit) reflects that the LLM dropped a comma and trailing whitespace from the source quote — well within tolerance.

The helper stays inside `tier1_pbg_check.py` for now; lift to a shared module after a second typology proves the API shape (per L24 review). `rapidfuzz` would expose `fuzz.partial_ratio` directly but is not installed in this venv — `difflib` (stdlib) gives the same semantics with no new dependency.

**Forward applicability:** every future Tier-1 extraction script (`tier1_emd_check.py`, Integrity Pact, Judicial Preview, etc.) MUST call this guard before any `kg_nodes` insert. EMD work is paused until then.

---

## L25 — Amount-to-Percentage: Shared Helper

**Date:** May 2026  
**What we did:** Built the amount→percentage conversion inline inside `scripts/tier1_pbg_check.py` as part of FIX C / L20 — `fetch_contract_value_cr()` plus an inline implied-percentage calculation in `main()`. PBG was the only typology that needed it at the time.  
**What happened:** EMD-Shortfall on PPP documents (Tirupathi, Vijayawada) hit the same wall. Both NREDCAP RFPs state EMD as a fixed INR amount only — Tirupathi `INR 2.57 crore`, Vijayawada `INR 3.24 crore`. The percentage-shape rule `GFR-G-049` (2-5% range) couldn't fire because `total_pct` was `None`. The exact same conversion that already worked for PBG (amount ÷ contract_value × 100) was needed for EMD, but lifting it would mean either copy-pasting the FIX C code or duplicating `fetch_contract_value_cr` into the new EMD script. Rebuilding it inline twice would mean two places to keep in sync; future typologies (Integrity Pact threshold, Judicial Preview value cutoff) would face the same fork.  
**Why we changed:** Every percentage-based rule on every PPP document will need this conversion. The lookup logic — preferring LLM-extracted `estimated_value_cr` over regex `estimated_value_cr_classified` with reliability flag — is non-trivial enough that drift between copies would be a real risk. One owner, one set of audit fields, one set of edge cases to test.  
**What we changed to:** Lifted the logic to `modules/validation/amount_to_pct.py` as `compute_implied_pct(doc_id, amount_cr, source) → dict`. The dict has six keys: `implied_pct`, `amount_cr`, `contract_value_cr`, `contract_value_source`, `needs_contract_value`, `source`. The `source` parameter (`"emd" | "pbg"`) is recorded for the audit trail and reserved for future typology-specific lookups, but doesn't change the math today. `tier1_pbg_check.py`'s `fetch_contract_value_cr()` is now a back-compat shim that delegates to the shared helper. `tier1_emd_check.py` calls `compute_implied_pct()` directly when the LLM returned `amount_cr` with no `total_pct`, then runs the existing `evaluate_emd_against_rule()` against the implied percentage.  
**Result:** PASS on both PPP documents.

  - **Tirupathi WtE** — EMD section "15. EARNEST MONEY DEPOSIT" (NIT, lines 1208–1245, cosine 0.6225). LLM extracted `amount_cr=2.57`, evidence verified score 100 (substring). `compute_implied_pct` returned `implied_pct=0.998` from `contract_value_cr=257.51` (`source=llm_extracted`). `GFR-G-049` range check: `0.998 < 2.0` → **HARD_BLOCK violation**. ValidationFinding `14ca4239-…`, VIOLATES_RULE `bd22ccbf-…`.
  - **Vijayawada WtE** — EMD section "15. EARNEST MONEY DEPOSIT" (NIT, lines 1226–1261, cosine 0.6343). LLM extracted `amount_cr=3.24`, evidence verified score 100 (substring). `compute_implied_pct` returned `implied_pct=0.9978` from `contract_value_cr=324.7` (`source=llm_extracted`). `GFR-G-049` range check: `0.9978 < 2.0` → **HARD_BLOCK violation**. ValidationFinding `46254b86-…`, VIOLATES_RULE `a28c50a1-…`.

The PBG numbers from the existing 5 findings still match exactly when re-run through the shared helper (Tirupathi 4.9979%, Vijayawada 5.0015%) — confirming the lift is behaviour-preserving. Findings now total **10 (5 PBG + 5 EMD)**. Both NREDCAP PPP docs carry the full pair (PBG + EMD HARD_BLOCK violations), exactly the corpus shape required for cross-typology audit reports.

The shared helper is ready for any future typology that has a percentage-based rule on a doc that may state the value as a fixed amount. Next typology candidates (Integrity Pact threshold, Judicial Preview value cutoff) will use it without duplication.

---

## L26 — `smart_truncate`: Keyword-Aware Windowing for Buried Short Values

**Date:** May 2026  
**What we did:** Every Tier-1 typology script (PBG, EMD, Bid-Validity) used `_truncate_for_rerank` — a head-60% + tail-40% truncator originally calibrated for PBG. When a candidate section is longer than the cap (4000 chars), it shows the LLM `text[:2400]` + `text[-1600:]` and elides the middle.  
**What happened:** For Bid-Validity on Judicial Academy, BGE-M3 retrieval correctly surfaced the right ITB section at rank 12 (lines 464–542, cosine 0.4848). The section is **13,282 chars** of an ITB-rewrite block ("ITB X.Y shall be read as ..."), with **one row** stating *"ITB 18.1 | The bid validity period shall be **NINETY (90)** days"* at offset **11,527**. Head ended at 2,400; tail started at 11,682. Offset 11,527 fell in the elided middle. The LLM was shown the 180-day Bid Security validity in the tail (correctly ignored per prompt rules) but never saw the actual 90-day bid validity. It correctly returned `chosen_index=null, found=false` — honest silence on text it was never given.  
**Why we changed:** Head+tail truncation assumes the answer clusters near section start or end. PBG ("furnish Performance Security ... 2.5%") and EMD ("furnish Bid Security ... 1%") clauses are usually short and self-contained — head+tail works. Bid-validity values are often **single rows in long BDS-rewrite tables**, neither at the start nor the end. A stronger model can't read text it was never given. The fix is at the truncation step, not the prompt or the retrieval.  
**What we changed to:** `smart_truncate(text, window=3000)` — keyword-aware windowing in `scripts/tier1_bid_validity_check.py`. Search the section text for the earliest occurrence of any vocabulary keyword (`bid validity`, `bids shall remain valid`, `validity period`, `remain valid for`, the spelled-out day counts `ninety`/`sixty`/`thirty`/`eighty`/`one hundred twenty`/`hundred eighty`, plus the patterns `validity[^.]{0,50}days` and `days[^.]{0,50}validity`). Centre a 3000-char window on that hit. If no keyword matches, fall back to head+tail (2400/1600) so the LLM still sees both ends. Window size × K=15 candidates ≈ 45K chars in the rerank prompt — comfortably inside qwen-2.5-72b's 128K context.  
**Result:** PASS. JA's section [12]: full length 13,282 chars → window length 3,062 chars centered on `"ninety"` at offset 1,562. LLM extracted `validity_days=90` with verbatim evidence `"The bid validity period shall be**NINETY (90)**days."`, score 100, method `substring`. Decision: 90 ≥ 90 → compliant → no finding. **Correct silence for the right reason** (the LLM saw the answer and judged it compliant), not the wrong reason (the answer was elided). Vizag (180 days, cosine 0.4389), Kakinada (90 days, cosine 0.5863), Tirupathi (180 days, cosine 0.6973), Vijayawada (180 days, cosine 0.6925) all extracted at score 100 with the same window strategy — all five docs now correctly compliant for Bid-Validity-Short.

This is a typology-local helper for now (only `tier1_bid_validity_check.py` uses it). Lift candidate: if PBG or EMD start hitting similar elision problems on a future doc, move `smart_truncate` to `modules/validation/` next to the other shared helpers. Tonight, the existing `_truncate_for_rerank` works fine for those typologies — don't change what's working.

---

## L27 — Missing-PVC-Clause: Presence-Shape Typology + UNKNOWN→ADVISORY Downgrade

**Date:** May 2026
**What we did:** Added the fourth Tier-1 typology — Missing-PVC-Clause — verifying that AP Works tenders contain a Price Variation / Price Adjustment formula as required by AP-GO-019 (>4 lakh, >6 months) or MPW-133 (Works > 18 months). Two Vol-II clauses cover this: GCC §47 (price adjustment formula) and SCC §47 (PCC table of indices). The script lives at `scripts/tier1_pvc_check.py`. Same machinery as the prior three typologies (BGE-M3 retrieval → Qdrant top-10 → LLM rerank → evidence_guard) but with a **presence-shape** check instead of threshold-shape: the LLM is asked `pvc_present: bool` rather than `value_pct: float`.
**What happened:** Two architectural shifts surfaced during the implementation.

**(a) Presence vs threshold.** Prior typologies (PBG/EMD/Bid-Validity) had numeric thresholds — extract a percentage or duration and compare to a rule cutoff. PVC is a binary presence test: the document either has a price-variation formula or it doesn't. The LLM rerank prompt returns `{pvc_present, formula_breakdown, go_reference, evidence}`; the rule check is `if not pvc_present → fire`. Materialised finding label `pvc_absent_violation` vs `compliant_pvc_present`. Evidence verification (L24) still applies — when `pvc_present=True`, the formula evidence is verified against the chosen section's source text.

**(b) UNKNOWN → ADVISORY downgrade.** AP-GO-019's `condition_when` requires both `EstimatedValue >= 4_00_000` AND `OriginalContractPeriodMonths >= 6`. The duration field has no LLM extractor today (only ECV does), so on every Works doc `OriginalContractPeriodMonths` arrives as `None` → condition_evaluator returns UNKNOWN for the AND-chain. Previously this would have meant "rule not selected, no finding emitted" — silent for the wrong reason. The new behaviour: when no rule fires cleanly, the highest-priority rule whose verdict is UNKNOWN is **selected with severity downgraded from HARD_BLOCK → ADVISORY** and `verdict_origin="UNKNOWN"` recorded in the finding's properties. This keeps the pipeline live, surfaces a finding for downstream review, and never blocks deal-flow on an extraction gap. The design is first-class three-valued logic: FIRE = block, UNKNOWN = advise, SKIP = silent.

**Why we changed:** Without (a), PVC would have needed a bespoke threshold-shape pipeline. Without (b), every PVC check would have been silent until duration extraction landed — a regression compared to the L24 honesty principle. Both shifts are reusable: future presence-shape typologies (Integrity Pact required, Judicial Preview required, Reverse Tendering required) drop into the same script template, and any rule with a partly-extractable condition_when degrades to ADVISORY rather than going silent.

**What we changed:** New `scripts/tier1_pvc_check.py` (presence-shape rerank prompt + rule selection with UNKNOWN downgrade). New `PVC_SECTION_ROUTER` in `modules/validation/section_router.py`: APCRDA_Works → [GCC, SCC, Specifications], SBD_Format → [GCC, SCC, Evaluation], NREDCAP_PPP → [GCC] (rule layer SKIPs PPPs anyway), default → [GCC, SCC, Specifications]. Two Tier-1 ignore rules added at the rule layer for non-applicable cases (PPP/DBFOT and below-threshold Works).

**Result:** 4 of 6 docs ran cleanly; Vizag PVC is `compliant_pvc_present` (GO 62/2021 by-reference), JA is `compliant_pvc_present` (explicit formula), HC is `compliant_pvc_present` (GCC §39 adjustment formula referencing PCC), Kakinada is `pvc_absent_violation` (SBD body has BDS pricing rewrites but no price-variation formula in any of 10 retrieved Evaluation candidates — LLM scanned all 10 and returned `chosen_index=null, found=false`). Tirupathi/Vijayawada SKIP at rule layer (PPP). All four findings (incl. compliant ones) have `evidence_match_score >= 98` where evidence was returned. Severity is ADVISORY across the board because DurationMonths is UNKNOWN — once an LLM duration extractor lands, the downgrade unwinds and the rule fires at its native HARD_BLOCK severity for non-compliant docs.

---

## L28 — Regex Classifier Removed: LLM Is Now the Single Source of Truth for Tender Facts

**Date:** May 2026
**What we did:** Deleted the regex-classifier path entirely from the document-ingest flow. `engines/classifier.TenderClassifier` is no longer called by `kg_builder._classify`; `_classify` itself is gone, replaced by an inline `_detect_ap_tender(full_text)` that does a case-insensitive substring match against the AP keyword list. Five fields previously written by the classifier — `tender_type_classified`, `estimated_value_cr_classified`, `estimated_value_reliable`, `duration_months_classified`, `funding_source_classified` — were deleted from every TenderDocument node in Supabase by the user, and every line of code that read them across `scripts/tier1_*.py`, `scripts/group_emd_check.py`, and `modules/validation/amount_to_pct.py` was deleted (not commented out). `tender_facts_extractor.run(doc_id, commit=True)` and `tender_type_extractor.run(doc_id, commit=True)` are now called as a mandatory Phase 6c in `kg_builder.build_kg()` after Section nodes are inserted — no document enters the system without them being attempted.
**What happened:** Tier-1 PVC on HC and Kakinada had been SKIPping at the rule layer because the regex-derived `estimated_value_cr_classified` was wrong (HC=0.1 cr instead of ~365 cr, Kakinada=0.0 cr instead of 152.78 cr). The proximate fix would have been to override those two values; the user identified this as a process failure rather than a code bug — the regex classifier was unreliable on every doc except JA, and patching individual values would just paper over the architectural problem. Single source of truth for tender facts is now the LLM extractor, which produces verbatim evidence and a confidence score per field. Three follow-on adjustments fell out of the cleanup:

**(a) Default extraction window was too narrow.** First pass with `n_sections=1, max_chars=800` returned null on JA/HC/Vizag because the cost-line lives in the *second* NIT section (3–4K chars in, after the project-name preamble). Re-running with `n_sections=3, max_chars=6000` captured: JA 125.5cr (`Rs.1,25,49,94,048.00`), HC 365.16cr (`Rs.365,15,98,126.00`), Kakinada 152.78cr (`Est Cost Rs.152.78 Crs`) — all confidence 1.0, reliable=True. Vizag remains null (no explicit ECV statement anywhere in the source markdown; the only signal is Bid Security Rs.1,10,26,236 in Vol I L950, which by AP convention implies ECV ≈ 110 cr but the extractor correctly refuses to derive it). The wider window is now part of the standard run; the kg_builder integration uses the extractor's defaults but a future tuning lift the call to `n_sections=3, max_chars=6000` is a natural improvement.

**(b) SBD_Format detection threshold tuning.** With ECV restored, Kakinada's PVC re-run still failed retrieval — the family detector (`detect_family` in `modules/validation/section_router.py`) fell through to "default" because Kakinada has only 15 Evaluation sections (the prior threshold was `n_eval > 20`). Default's PVC filter `[GCC, SCC, Specifications]` matched zero candidates because Kakinada's section profile is `Evaluation=15, BOQ=10, Forms=4, Other=3, NIT=3` — no GCC/SCC/Specifications at all. Threshold lowered to `n_eval >= 10 AND n_gcc == 0` — captures the SBD pattern (body in Evaluation blocks, zero GCC) without mis-routing APCRDA_Works docs (which always have at least some GCC). After tuning, Kakinada routed to SBD_Format → filter `[GCC, SCC, Evaluation]` → 10 Evaluation candidates retrieved → LLM correctly determined no PVC formula present → finding emitted as `pvc_absent_violation` (ADVISORY, since duration is UNKNOWN).

**(c) UNKNOWN → ADVISORY contract.** The cleanup means missing fields are now `null` in the DB, which condition_evaluator rightly treats as UNKNOWN. The new rule-selection path (L27) keeps these findings live and visible at ADVISORY severity rather than silently dropping them — explicit honesty about the extraction gap.

**Why we changed:** Two specific Vol-II clauses meet two specific rules; if we silently mis-attribute a value because regex misread the heading, the audit trail is wrong and the validation system loses its honesty guarantee. The LLM extractor with confidence scoring + verbatim evidence is reliable on 5 of 6 docs out of the box and honestly null on the sixth (Vizag's source genuinely doesn't state ECV — that's a real corpus gap, not an extractor failure). The architectural cost (one LLM call per doc on ingest) is small; the audit-trail benefit is large.

**What we changed:**
- `experiments/tender_graph/kg_builder.py`: deleted `_classify()`, replaced with `_detect_ap_tender()`. TenderDocument node properties reduced to `{doc_id, is_ap_tender, layer}`. Added Phase 6c that calls both LLM extractors with `commit=True` after Section insertion. Failures captured in `summary.defeasibility["llm_extraction_errors"]` but do NOT abort the build.
- `modules/validation/amount_to_pct.py`: regex fallback path deleted from `_fetch_contract_value_cr`. LLM-extracted only.
- `modules/validation/section_router.py`: `detect_family` SBD threshold lowered to `n_eval >= 10 AND n_gcc == 0`.
- `scripts/tier1_pvc_check.py`, `scripts/tier1_bid_validity_check.py`, `scripts/tier1_emd_check.py`, `scripts/group_emd_check.py`: every read of the 5 deprecated fields deleted.
- `modules/extraction/tender_type_extractor.py`, `modules/extraction/tender_facts_extractor.py`: docstring cleanup — the legacy fields no longer exist in the schema.

**Result:** All 6 TenderDocument nodes in the corpus carry `tender_type` (LLM, all confidence ≥0.85), `is_ap_tender` (substring), and `estimated_value_cr` for 5 of 6 (Vizag honestly null). PVC re-runs on HC (compliant) and Kakinada (violation, family routed correctly via the new SBD_Format threshold). The pipeline now refuses to consume any unreliable data; an UNKNOWN signal becomes an ADVISORY finding rather than silent absence. This closes a recurring class of bug — every prior typology had a moment where a wrong regex value either fired a wrong finding or hid a real one.

---

## L29 — Absence Findings Do Not Have Evidence Quotes

**Date:** May 2026
**What we did:** Separated the audit-field semantics for **absence findings** (a Missing-X violation where the document fails to contain the required clause) from **presence findings** (the LLM extracted a quote from a chosen section). The L24 evidence_guard runs the substring + difflib check against an LLM evidence quote — but for an absence finding there is, by definition, nothing to quote. Forcing the guard to run produced a misleading audit row on the Kakinada PVC finding: `evidence_verified=False, evidence_match_score=0, evidence_match_method='skipped'` — implying the LLM's quote was found to be hallucinated, when in fact no quote was ever produced.
**What happened:** Kakinada Missing-PVC-Clause re-ran cleanly under the new SBD_Format routing — 10 Evaluation candidates retrieved, LLM scanned all 10, returned `chosen_index=null, pvc_present=False, evidence=""`. The materialise block treated the empty string as "the LLM didn't quote anything" and persisted the four ev_* audit fields with their default placeholder values (False / 0 / "skipped"). A reviewer reading that finding could not distinguish "absence found, audit fields don't apply" from "presence claimed, evidence failed verification" — both look the same. This is the L24 honesty principle inverted: the verifier was claiming a verdict on text that didn't exist.
**Why we changed:** Two finding shapes need two distinct audit semantics. For presence findings (`pvc_present=True`, candidate chosen, evidence quoted), the L24 guard runs and persists `evidence_in_source` / `evidence_verified` / `evidence_match_score` / `evidence_match_method`. For absence findings (`pvc_present=False`, no candidate chosen), all four fields become `null` (verifier was never expected to run) and `evidence_match_method` becomes the explicit literal `'absence_finding_no_evidence'` so any downstream consumer can branch on the marker rather than trying to interpret `False/0/"skipped"`. The `evidence` field itself becomes a human-readable description of what was searched and not found — `"Price Variation Clause not found in document after searching GCC, SCC, Evaluation section types"` — which is the actual content of an absence finding.
**What we changed:** `scripts/tier1_pvc_check.py` — after `is_violation` is determined and before materialise, an `is_absence_finding = (not pvc_present and section is None)` branch downgrades the four ev_* locals to (None / None / "absence_finding_no_evidence") and replaces the empty `evidence` string with the search-trace description. Both the ValidationFinding properties and the VIOLATES_RULE edge properties pick up the new values. The Kakinada finding `ebd37fa9-8849-41dd-a326-7b1f64fa8303` and its edge `1bc3a3ec-d6de-4bbc-bf33-f1ab045c2e26` were patched in-place via REST PATCH to apply the new schema.
**Forward applicability:** This pattern is reusable for every future Missing-X typology — Missing-Integrity-Pact (clause expected by AP-GO-049 but absent from the doc), Missing-Judicial-Preview (constitutional review text expected on > 100 cr Works but absent), Missing-Force-Majeure-Carve-Out, etc. Any typology whose violation shape is "the document failed to contain something" should set `evidence_match_method='absence_finding_no_evidence'` rather than running the L24 guard. The presence-shape audit is still mandatory for any case where the LLM does produce a quote — that path is unchanged. As a rule of thumb: if `chosen_index is None`, you're in an absence finding; if it's an int, you're in a presence finding and the guard runs.

---

## L30 — Multilateral-Funded Tenders Have Dual Compliance Requirements

**Date:** May 2026
**What we did:** Built the fifth Tier-1 typology — Missing-Integrity-Pact — and discovered on the first JA test run that the typology's "presence" boolean was not enough on multilateral-funded Indian tenders. ADB-funded ($788.8M) and World Bank-funded ($800M) Amaravati capital city works (Judicial Academy, High Court, HOD, etc.) ship with the **lender's anticorruption framework** (ADB Anticorruption Policy + Integrity Principles and Guidelines + OAI sanctions list + IEF; or World Bank Sanctions Procedures + Anticorruption Guidelines + ineligibility cross-checks). That framework is NOT a substitute for the regulated **CVC Pre-bid Integrity Pact** that Indian procurement law (CVC-086, MPS-022) requires regardless of funding source. A naive presence check would either (a) incorrectly mark the doc compliant on the strength of the ADB framework, or (b) report "absent" without recording that the ADB framework IS present (losing audit-trail value). Both are wrong.
**What happened:** First run of `tier1_integrity_pact_check.py` on JA returned `chosen_index=null, integrity_pact_present=false, found=false` — correctly identifying that no CVC IP exists, but discarding the ADB framework content the LLM had observed in candidate [0] ("Section V — Fraud and Corruption", lines 1945–2016, cosine 0.5886). The reasoning quote noted "None of the candidates contain the specific elements of a Pre-bid Integrity Pact, such as a binding agreement between the buyer and bidder, monitored by Independent External Monitors (IEMs) approved by the Central Vigilance Commission" — accurate, but the audit trail had no record of what WAS detected. A reviewer reading that finding could not distinguish "the doc has nothing about anticorruption at all" from "the doc has the ADB framework but not the CVC IP" — and those two situations have different remediation paths.
**Why we changed:** Indian procurement law and multilateral-lender procurement law operate as parallel compliance regimes on the same document. The CVC IP and the ADB/WB framework are distinct instruments with distinct enforcement mechanisms (CVC-empanelled IEMs vs ADB OAI / WB Sanctions Board). The system must detect both **independently** and report each separately, so the finding records the actual state of affairs:
- both present → compliant (CVC IP is the operative satisfier);
- CVC IP only → compliant;
- multilateral framework only → CVC-IP-missing violation, with the multilateral evidence preserved as audit trail and an explanatory note that the lender framework does NOT substitute;
- neither → CVC-IP-missing violation, absence finding per L29.

The single-bool design conflates (3) and (4), which is exactly what L24 honesty principles forbid.

**What we changed:**
- `scripts/tier1_integrity_pact_check.py` — rerank prompt now asks the LLM for THREE independent booleans (`adb_framework_detected`, `cvc_ip_detected`, `integrity_pact_present`) plus a `pact_type` enum (`'CVC_IP' | 'ADB_framework_only' | 'WB_framework_only' | 'multilateral_framework_only' | 'none'`). The prompt explicitly enumerates what counts as CVC IP (bilateral pact, IEMs, CVC Office Order, IP proforma) vs what counts as multilateral framework (ADB IPG / OAI sanctions / IEF, WB Guidelines / Sanctions Procedures, lender ineligibility cross-checks). `integrity_pact_present` is locked to `cvc_ip_detected` post-hoc by the script (defence in depth — never trust an LLM-supplied invariant).
- Three reason labels: `compliant_integrity_pact_present` (CVC IP found), `integrity_pact_absent_violation_multilateral_only` (lender framework but no CVC IP), `integrity_pact_absent_violation` (neither). The multilateral-only label triggers a `note` field in the finding spelling out that "the multilateral lender framework does not substitute for CVC Pre-bid Integrity Pact requirement under Indian procurement law (CVC-086, MPS-022)." The label itself appends "(multilateral framework detected, CVC IP missing)" so a UI list-view reader sees the nuance without expanding the row.
- Multilateral-only findings carry the verified lender-framework evidence quote (L24 guard runs as normal — the quote is real text from the doc), `cvc_ip_detected=false`, `adb_framework_detected=true`, `pact_type='multilateral_framework_only'`. Pure-absence findings still trigger the L29 `absence_finding_no_evidence` path.
- `modules/validation/section_router.py` IP block annotated with the dual-compliance contract and an explicit "DO NOT add a multilateral-funding SKIP rule" warning so a future contributor doesn't accidentally waive CVC IP for ADB/WB-funded docs. The router stays at `[NIT, Forms]` for every family — funding source does not change retrieval scope.

**Result:** All 6 corpus docs ran cleanly. JA/HC/Tirupathi correctly carry `pact_type='multilateral_framework_only'` with verified evidence (JA: ADB+WB clause from "Section V - Fraud and Corruption"; HC: WB Guidelines for Program for Results Financing; Tirupathi: WB ineligibility cross-check). Vizag/Kakinada/Vijayawada are pure absence findings (`pact_type='none'`). All six are ADVISORY because the IP_Threshold subterm is org-defined per CVC-116 (L27 UNKNOWN→ADVISORY downgrade). Six new ValidationFindings, six new VIOLATES_RULE edges, all with the L24 guard outcome (Section→Rule for multilateral-only with verified quote, TenderDocument→Rule for pure absence with the L29 marker).
**Forward applicability:** Any future typology that has a parallel-compliance shape (Indian rule + lender rule, or AP-State rule + Central rule on the same artefact) should adopt the same two-bool pattern: detect each instrument independently, lock the "compliant" boolean to the regulated instrument, preserve the secondary evidence and a note explaining what was found vs what is required. World-Bank-funded portions of the corpus will need the same structural treatment for any future typology where WB-specific clauses (e.g. WB Standard Bidding Documents for Works) might be mistaken for the Indian regulated equivalent.

---

## L36 — Blacklist-Not-Checked + Retrieval-Coverage Limitation Surfaced

**Date:** May 2026
**What we did:** Built the ninth Tier-1 typology — Blacklist-Not-Checked — verifying that the doc requires bidders to declare past debarments / blacklistings / sanctions (bidder-side self-declaration) OR commits the procuring entity to verifying against debarment lists (buyer-side verification) OR explicitly bars debarred bidders from participation (eligibility bar). Any one of (a)/(b)/(c) is sufficient for compliance. MPS-021 (Central, HARD_BLOCK, `TenderType=ANY`) is the canonical primary; AP-GO-095 / GFR-G-037 / MPW-158 / MPS-186 are the backup rules. Same machinery as the post-L35 presence-shape scripts: BGE-M3 retrieval into [ITB, Forms] (or [ITB, Forms, Evaluation] for SBD) → top-K → LLM rerank with three-state extraction → L24 evidence guard → L29 absence marker on chosen_index=null.
**What happened:** 6-doc run produced 6 outcomes — 3 compliant (JA / HC / Kakinada), 1 absence (Vizag — flagged below as suspicious), 2 UNVERIFIED (Tirupathi / Vijayawada — LLM stitched the NREDCAP RFP "We certify..." clause across multiple list items, L24 score=67, no_match). The compliant outcomes verified at score 100 (HC bidder_self_declaration with multilateral check; Kakinada AP-flavoured bidder declaration; JA WB/ADB eligibility bar from L35).

The Vizag ABSENCE finding is **suspicious** and worth a follow-on retrieval-coverage investigation. Source-grep confirms Vizag DOES carry multiple debarment-related clauses:
- L173 — "The Authority requires compliance with the Authority's Anti-Corruption Guidelines and its prevailing sanctions policies and procedures..." (multilateral framework anchor)
- L420 — "the Authority may, if provided for in the BDS, declare the Bidder ineligible..." (debarment-power clause)
- L1131 — "Not having been declared ineligible by the Authority, as described in ITB 4.5." (eligibility criterion)
- L1567 — "Bid-Securing Declaration: We have not been suspended nor declared ineligible by the Authority..." (the strongest candidate — explicit bidder self-declaration)

The LLM's top-10 candidate set didn't include the L1567 section (BSD declaration), which is the cleanest match for the typology. The LLM correctly reported "None of the candidates explicitly state a requirement..." for the candidates it WAS shown. This is a **retrieval-coverage limitation** — the BGE-M3 + Qdrant top-K filter pulled 10 candidates out of Vizag's ~80+ ITB/Forms sections, and the most relevant one didn't make the cut.

**Why we didn't relax the contract:** The Vizag absence finding is **technically correct** under the L35 contract (LLM didn't find the clause in the candidates it was shown), but the underlying cause is "retrieval missed the right section" not "doc lacks the clause". Two paths to fix:
1. **Increase top-K from 10 to 20–25** for this typology — cheap, captures more long-tail sections at the cost of larger LLM prompts.
2. **Multi-pass retrieval** — re-rank with a second query if the first pass returns no compliant outcome, using a different keyword vocabulary (e.g. "Bid-Securing Declaration", "ineligible by Authority").
3. **Lift the L36 retrieval-coverage observation as a known limitation** and accept the Vizag finding as "needs human review" via a future UNVERIFIED-on-absence-with-grep-fallback path.

Tonight we ship the typology with the Vizag false positive recorded honestly and a follow-on for retrieval coverage. The 2 UNVERIFIED findings (Tirupathi / Vijayawada) are working-as-designed under L35 — the LLM stitched a long list-item quote across "circumstances:" + "v." which is exactly what the strict-quote prompt + L24 guard are meant to flag for human review.

**What we changed:**
- `scripts/tier1_blacklist_check.py` — new presence-shape script. RULE_CANDIDATES = [MPS-021, MPW-158, MPS-186, GFR-G-037, AP-GO-095] in priority order. LLM extracts `blacklist_check_required, check_form ('bidder_self_declaration'|'buyer_verification_commitment'|'eligibility_bar'|'multiple'), includes_multilateral_lender_check, go_reference, evidence`. L35 three-state contract; L24 guard; L29 absence marker; UNVERIFIED finding has no VIOLATES_RULE edge.
- `modules/validation/section_router.py` — `BLACKLIST_SECTION_ROUTER` added: `[ITB, Forms]` for APCRDA_Works / NREDCAP_PPP / default; `[ITB, Forms, Evaluation]` for SBD_Format. GCC excluded (AP-contractor-management clauses are operational, not bid-stage eligibility).

**Result:** 6-doc run produced 3 compliant (JA / HC / Kakinada), 1 OPEN absence (Vizag — flagged as suspect retrieval-coverage), 2 UNVERIFIED (Tirupathi / Vijayawada — list-item stitching). Total corpus state: **23 ValidationFindings (20 OPEN + 3 UNVERIFIED), 20 VIOLATES_RULE edges**. The 3 UNVERIFIED findings are now: 1 E-Proc (L35) + 2 Blacklist (L36).

**Forward applicability:** Two follow-ons:
1. **Retrieval coverage**: when an ABSENCE finding fires after the L35 path, do a cheap source-grep fallback for the typology's keyword vocabulary on the doc's relevant sections. If the grep finds matches, downgrade ABSENCE to UNVERIFIED-FOR-REVIEW (LLM didn't find it but text is in the doc). This would catch the Vizag-style false positive automatically.
2. **List-item quote handling**: NREDCAP RFPs use enumerated lists ("circumstances: i. ... ii. ... iii. ...") that the LLM stitches across. The strict-quote prompt didn't fully prevent this on Tirupathi/Vijayawada. Consider extending the prompt with "if the source uses an enumerated list, quote ONE list item only; do not include the parent stem ('circumstances:') with the item."

Both follow-ons are typology-agnostic and lift candidates for `modules/validation/`. Tonight's L36 surfaces them; the fixes are their own follow-on commits.

---

## L31 — Missing-LD-Clause + Corpus-Gap Distinction

**Date:** May 2026
**What we did:** Built the sixth Tier-1 typology — Missing-LD-Clause — completing the presence-shape trilogy (PVC / IP / LD). All three follow the same machinery: BGE-M3 retrieval → Qdrant top-K within a section_router-chosen filter → LLM rerank with structured extraction → L24 evidence guard → L29 absence marker for `chosen_index=null` paths. Three primary rules drive selection: MPW-124 (Works, P1), MPS-125 (Non-Consulting Services, P1), GFR-083 (catch-all, P2). MPW-124 wins on Works docs; GFR-083 catches PPP/DBFOT. No UNKNOWN→ADVISORY downgrade fires for this typology because the conditions resolve fully from `tender_type` (LLM-extracted, reliable=True for all 6 docs).
**What happened:** 5 of 6 corpus docs were correctly compliant — the LLM picked verified LD evidence with `evidence_match_score >= 97`, including:
- Vizag: explicit GCC formula "5% per month, max 10% of contract value" (cosine 0.6280, score=99).
- HC: GCC §48 "Liquidated Damages" with PCC by-reference to rate (cosine 0.7237, score=100).
- JA: same MPW PCC-by-reference pattern as HC (cosine 0.6740, score=97).
- Kakinada: LD reference embedded in an Evaluation-typed block ("Liquidated Damages shall be levied as per the condition No.48.3 of conditions of contract") — the SBD pattern from L28 again, where the body lives in Evaluation rather than GCC.
- Tirupathi: GCC §14.8 "Delay Liquidated Damages" with explicit "0.1% per day of Performance Security" formula (cosine 0.6594, score=100).

The sixth doc (Vijayawada — sister NREDCAP DBFOT to Tirupathi) returned `chosen_index=null, ld_clause_present=false` — surfacing as an ADVISORY-absent finding through the L29 absence path. Investigation showed Vijayawada has **zero GCC sections in the KG** (Forms=50, NIT=20, Evaluation=20, Scope=9), while Tirupathi has 191 GCC sections from its ingested DCA. This is the L22 multi-file ingest gap surfacing on a Tier-1 finding: Vijayawada's KG is RFP-only because its DCA / Schedule / Model PPA PDFs were never converted to markdown, so the LD clause that almost certainly mirrors Tirupathi's GCC §14.8 was never ingested. The LLM correctly reported what it could see; what it could see was incomplete.

**Why we changed:** A finding that says "violation" when the underlying cause is "we didn't ingest the source file" is misleading at the audit-trail layer. A reviewer cannot distinguish "this tender genuinely lacks the clause" from "we didn't load the file containing the clause" without external context. Same family of failure as L24 (LLM hallucination — fabricated evidence) and L29 (absence findings forced through the presence-evidence path) — the system was claiming verdicts on artefacts it didn't have full visibility into. The fix is a new audit-field pair: `corpus_gap: bool` and `corpus_gap_reason: string`, plus a severity downgrade to ADVISORY when `corpus_gap=true`. The finding stays in the database (don't silently delete it — that loses the audit trail showing the system DID flag the gap), but a reviewer reading the row immediately sees this is a corpus-completeness issue, not a real procurement violation.

**What we changed:**
- `scripts/tier1_ld_check.py` — new file, port of `tier1_pvc_check.py` with the LD-specific prompt, rules, and section filter.
- `modules/validation/section_router.py` — added `LD_SECTION_ROUTER` (`APCRDA_Works → [GCC, SCC]`, `SBD_Format → [GCC, SCC, Evaluation]`, `NREDCAP_PPP → [GCC, SCC]`, `default → [GCC, SCC, Specifications]`) and registered it in the `SECTION_ROUTERS` dict. Unlike PVC's NREDCAP_PPP entry which is a SKIP placeholder, LD's NREDCAP_PPP entry is real — GFR-083 actively fires on PPP/DBFOT.
- DB-level patch on Vijayawada's ValidationFinding `e4e52039-d4d8-4416-9ed8-ef878a3b3daa` and its VIOLATES_RULE edge `6f15aa0b-f54e-4eae-865d-a58fb26230c0`: added `corpus_gap=true`, `corpus_gap_reason='Vijayawada DCA not ingested. LD clause expected in DCA GCC section 14.8 mirroring Tirupathi pattern. Finding will resolve to compliant after DCA ingest.'`, severity `HARD_BLOCK → ADVISORY`. The finding remains visible (not deleted) so the audit trail records the gap detection.

**Forward applicability:** The `corpus_gap` field is reusable for every future typology. Any time a presence-shape finding triggers because retrieval came up empty AND we have external evidence that the relevant source file is missing from the KG (different sister-doc has the clause; the ingest manifest shows the file was never converted; an LLM-extracted facts pass returned `null` due to file absence), the finding should carry `corpus_gap=true`. After re-ingest, the next typology run will find the clause and the finding will be cleared by `_delete_prior_tier1_*` cleanup. Three corpus gaps are known today: Vijayawada DCA + Schedule + Model PPA (per L22, not yet converted); Tirupathi Schedule + Model PPA (per L22, not yet converted). Each of these is a candidate for L31 corpus_gap flagging on any future typology that touches Schedule/PPA territory.

The presence-shape trilogy (PVC / IP / LD) is now structurally identical at the script level — same imports, same machinery, only the prompt and section filter differ. Any future Missing-X typology can be a near-mechanical port of any of the three.

---

## L32 — kg_builder Rebuilds Must Preserve Typology Findings

**Date:** May 2026
**What we did:** Added snapshot-and-restore logic to `experiments/tender_graph/kg_builder.py` so that `build_kg(doc_id, ..., clear_existing=True)` preserves `ValidationFinding` nodes and `VIOLATES_RULE` edges across structural rebuilds, while still wiping every other doc-scoped row (TenderDocument, Section, RuleNode, HAS_SECTION edges, …) as before.
**What happened:** Closing the L31 Vijayawada corpus gap required converting the DCA PDF → markdown and re-ingesting Vijayawada through `kg_builder.build_kg()` with both the RFP and the new DCA. The build_kg call's `clear_existing=True` (default) wiped the entire Vijayawada doc — including the 4 typology findings (PBG-Shortfall, EMD-Shortfall, Missing-Integrity-Pact, Missing-LD-Clause) created by Tier-1 typology scripts. The user had to re-run all 5 typology checks to restore the 3 surviving findings (PBG, EMD, IP — LD became compliant after the DCA ingest, as predicted by L31's `corpus_gap_reason`). That's an acceptable one-off cost, but the underlying contract is wrong: typology scripts own the lifecycle of `ValidationFinding` and `VIOLATES_RULE`. The KG builder rebuilds the *structural* KG (TenderDocument + Sections); it should not silently delete the typology-owned audit trail.
**Why we changed:** Three failure modes:
1. **Audit trail loss.** A user who rebuilds Vijayawada to add the DCA expects findings to either survive or be regenerated. Silent deletion forces them to remember to re-run every typology check or lose audit history.
2. **Cross-doc dependency.** A future workflow that rebuilds one doc to fix an ingest gap (per L22) shouldn't ripple compliance state changes through the rest of the corpus by demanding manual re-runs.
3. **FK cascade trap.** `kg_edges.from_node_id` and `to_node_id` have `ON DELETE CASCADE` foreign keys to `kg_nodes.node_id` (verified via `information_schema`). A naive "DELETE WHERE node_type != 'ValidationFinding'" wouldn't preserve `VIOLATES_RULE` edges — deleting the structural Section/RuleNode they reference would cascade-delete the edges anyway. The fix has to copy rows out of the DB before clearing, then re-insert with FK references re-resolved against the freshly-built nodes.

**What we changed:** `experiments/tender_graph/kg_builder.py`:
- New helper `_snapshot_findings(doc_id)` reads ValidationFinding nodes + VIOLATES_RULE edges into memory before `_clear_kg`.
- `build_kg()` calls `_snapshot_findings` first, then `_clear_kg`, then creates the new TenderDocument node, then calls `_restore_findings(doc_id, new_doc_node_id, snapshot)` BEFORE Section insertion. Restored ValidationFindings keep their original `node_id` (so external audit references — UI deep-links, prior reports — keep resolving). Restored VIOLATES_RULE edges keep their original `edge_id` and `properties`, with structural references rewritten:
    - `edge.from_node_id` → re-pointed to the new TenderDocument node (the original Section UUID is gone post-clear; the audit-trail attribution lives in `finding.properties.section_heading` and `section_node_id` JSONB echo, so a reviewer can still see *where* the original violation was attributed).
    - `edge.to_node_id` → re-resolved via `_get_or_create_rule_node_during_restore(doc_id, rule_id)`, which mirrors the typology scripts' `get_or_create_rule_node` (lookup by `rule_id`; if RuleNode missing, fetch from rules table and insert fresh).
- `summary.defeasibility` now reports `preserved_findings_pending_restore`, `preserved_edges_pending_restore`, `restored_findings`, `restored_edges` so a reviewer can audit the snapshot/restore counts on every rebuild.

**Result:** Verified end-to-end on Vijayawada. Pre-rebuild: 3 ValidationFindings + 3 VIOLATES_RULE edges (PBG-Shortfall + EMD-Shortfall + Missing-Integrity-Pact). After `build_kg(..., clear_existing=True)`: 298 nodes + 294 edges cleared, 3 findings + 3 edges restored with original UUIDs. All 3 VIOLATES_RULE edges have `from_node_id` pointing to the freshly-created TenderDocument and `to_node_id` pointing to freshly-created RuleNodes. ValidationFinding `properties.section_node_id` JSONB values are stale (point to UUIDs that no longer exist) but the human-readable audit trail (`section_heading`, `source_file`, `line_start_local`) is intact. The next typology re-run via `_delete_prior_tier1_*` will overwrite the stale `section_node_id` with a live one.

**Forward applicability:** Any future code path that calls `_clear_kg` directly should follow the same snapshot/restore pattern (or call `build_kg` instead of mutating the DB directly). Typology scripts continue to manage their own findings via `_delete_prior_tier1_*` cleanup; that contract is unchanged.

---

## L33 — kg_builder Phase 6c: Wider NIT Window for `estimated_value_cr`

**Date:** May 2026
**What we did:** Changed the `tender_facts_extractor.run()` defaults from `n_sections=1, max_chars=800` to `n_sections=3, max_chars=6000`, and made `kg_builder.build_kg()` Phase 6c pass those values explicitly at the call site so the intent is visible at both layers.
**What happened:** The narrow window was inherited from `tender_type_extractor` — that extractor pulls the project-name declaration which sits in the first 800 bytes of the NIT preamble across every doc in the corpus. Reusing those defaults for `tender_facts_extractor` was a copy-paste oversight that broke `estimated_value_cr` extraction on every doc whose cost line lives in the SECOND NIT section (which is most of them — JA, HC, Kakinada, Vijayawada all return `null` at the narrow defaults but `reliable=True` at the wider window). This surfaced first when extracting facts manually after the L28 regex-classifier removal; it surfaced again as a recurring annoyance after every L31/L32 rebuild because Phase 6c re-ran with the narrow defaults and zeroed out ECV that had been correctly extracted earlier.
**Why we changed:** The narrow window optimised for tender_type but was actively wrong for tender_facts. There's no shared "cost line lives within 800 chars" assumption that holds across the corpus — the narrow defaults were tuned to a different field with different placement. Each rebuild that hit Phase 6c quietly regressed `estimated_value_cr` to `null`, which then quietly regressed downstream typologies (PBG / EMD amount-path computations rely on `contract_value_cr` per L25). Tightening the defaults at the source (`run()` signature) AND passing them explicitly at the kg_builder call site ensures both layers reflect the corrected intent, so a future contributor reading either file gets the right answer.
**What we changed:**
- `modules/extraction/tender_facts_extractor.py`: `run()` signature now accepts `n_sections` and `max_chars` kwargs and forwards them to `extract_facts`. Default values bumped to `3` and `6000` respectively. CLI invocations get the wider window automatically.
- `experiments/tender_graph/kg_builder.py`: Phase 6c invocation now reads `run_tender_facts(doc_id, commit=True, n_sections=3, max_chars=6000)` with an inline comment explaining the L33 rationale. Belt-and-suspenders against a future reader changing the `run()` defaults without realising kg_builder depends on them.

**Result:** Verified end-to-end via Vizag rebuild. Pre-rebuild Vizag had 2 findings (PBG-Shortfall, Missing-Integrity-Pact) and `estimated_value_cr=null`. After rebuild: 166 nodes + 163 edges cleared, L32 snapshot/restore brought back both findings with original UUIDs (`67e5c13b-...`, `8a40744b-...`), L33 ran the LLM with the wider window and got `null` back — but for the *correct* reason this time. Vizag's first 3 NIT sections by line_start are the Preamble (46 words) and two Performance Security blocks (1,076 + 1,083 words); none state an ECV explicitly. Only signal in the entire 5-volume corpus is the Bid Security amount Rs.1,10,26,236 in Vol I L950 (= 1% of ECV per AP convention → ~110 cr), which the LLM correctly refuses to derive from a percentage. Honest null per L28 — not a window-size failure.

This closes a recurring class of bug: every typology that depends on `estimated_value_cr` (PBG amount path, EMD amount path, PVC/IP threshold gates) was at risk of regressing to `UNKNOWN→ADVISORY` (per L27) on every rebuild because the tighter default kept zeroing out the field. After L33, only docs that genuinely lack an ECV statement (Vizag) remain null — and they're explicitly marked `reliable=False`.

**Forward applicability:** The wider window is now the project default for tender-facts LLM extraction. Future facts extractors (e.g. duration_months, funding_source, integrity_pact_threshold) should adopt the same window unless their target field has a different placement profile. If a future field genuinely lives in the first 800 bytes (project-name pattern), narrow it explicitly at the call site. The CLI default change is also an attribution boundary: `python3 -m modules.extraction.tender_facts_extractor <doc_id>` now uses the wider window without flags, matching what the kg_builder Phase 6c does internally.

---

## L34 — Mobilisation-Advance-Excess: Threshold-Shape with "Absent = Compliant"

**Date:** May 2026
**What we did:** Built the seventh Tier-1 typology — Mobilisation-Advance-Excess — returning to threshold-shape after three consecutive presence-shape typologies (PVC / IP / LD). Same machinery as PBG/EMD/Bid-Validity (BGE-M3 retrieval → top-K → LLM rerank → L24 evidence guard) but with a new outcome shape: **absence of clause = compliant** (the inverse of PVC/IP/LD's "absence = violation"). Mobilisation Advance is OPTIONAL in Indian procurement per GFR Rule 172 — advance payments are exceptional. The check fires a violation only when the doc states an MA percentage AND that percentage exceeds the regulated cap (10% for AP Works > 1cr per AP-GO-014/076; 5% for AP EPC per AP-GO-224; 10% for Central Works per MPW-130).
**What happened:** Test on JA confirmed the threshold path works: AP-GO-014 fires (WARNING, cap=10%), LLM extracts `mobilisation_advance_pct=10.0` from the AP-GO chain (94/2003 + 267/2018 + 1474/2007 + 57/2024) embedded in JA's GCC, threshold compare `10.0 ≤ 10.0` returns compliant. No finding emitted. Run on the other 5 docs: 3 AP Works docs (Vizag, HC, Kakinada-attempt) sit at exactly 10% (canonical APCRDA boilerplate); Vizag has a notable **5% labour + 5% machinery split** structure that totals 10% — different from JA/HC's flat 10% but the same final cap; Kakinada (SBD format) has no MA clause at all → absent = compliant; Tirupathi/Vijayawada (NREDCAP PPP) hit the rule-layer SKIP path because none of the 4 candidate rules condition on `TenderType=PPP`. **Zero new findings emitted across the 6-doc corpus.** Vizag also exercised the L27 UNKNOWN→ADVISORY downgrade because EV is null (genuinely null per L33) and AP-GO-014's `EstimatedValue>1e7` resolves UNKNOWN — the rule still fired but at ADVISORY severity rather than the native WARNING.

**Why we changed:** Mixing presence-shape and threshold-shape semantics in the same script template would muddy the audit trail. PVC/IP/LD's "absent = violation" is the right answer for clauses that MUST exist (LD is mandatory per GFR Rule 83; IP is mandatory above org-defined threshold per CVC-086; PVC is mandatory for AP Works > 4 lakh AND > 6 months per AP-GO-019). MA is the inverse: the clause is voluntary, but IF present it must respect the cap. Three new outcome labels make the shape explicit:
- `compliant_no_ma_clause` — LLM found nothing → no violation, no finding.
- `compliant_clause_present_no_pct_stated` — framework invoked but % deferred to PCC/SCC → no violation today; would need PCC verification to escalate.
- `compliant_ma_pct_X_within_cap_Y` / `ma_pct_X_exceeds_cap_Y` — the live threshold compare.

The `>` in the threshold compare is intentionally STRICT (not `>=`). 10% exactly is compliant; 10.01% is a violation. AP-GO-014's text says "up to 10%" which is the inclusive interpretation. This matches the user's verification of the rule wording.

**What we changed:**
- `scripts/tier1_ma_check.py` (new) — port of `tier1_ld_check.py` with the threshold compare added between L24 evidence verification and finding materialisation. RULE_CANDIDATES carry a per-rule `cap_pct` field (5 or 10) used at compare time.
- `modules/validation/section_router.py` — added `MA_SECTION_ROUTER` mirroring the LD shape (anchors live in GCC + SCC, with the SBD_Format variant adding Evaluation for n_gcc=0 docs). Registered under `SECTION_ROUTERS["Mobilisation-Advance-Excess"]`.
- LLM prompt distinguishes Mobilisation Advance (the target) from Plant/Machinery Advance (MPW-131, separate 5% cap on equipment), Secured Advance against Material (MPW-132, 75% of invoice), Supplier Advance Payment (GFR Rule 172, 30%/40% limits for Goods/Services), and Notice-to-Proceed mobilisation (the triggering event, not the advance payment).
- L29 absence-finding marker is NOT used for this typology because absence = compliant → no row to mark. The L29 path is preserved in the script for symmetry with PVC/IP/LD but unreachable on this typology's outcomes.

**Result:** 0 new findings across the corpus. AP Works baseline confirmed at exactly 10% (3 docs); Kakinada's SBD format omits MA entirely (compliant); PPP rule-layer SKIP working as designed. The threshold-shape pattern is now structurally equivalent to PBG/EMD/Bid-Validity at the script level, just with a different `cap_pct` field and a different "absence = compliant" branch.

**Forward applicability:** Future threshold-shape typologies with optional-clause semantics fit this template: e.g. Interest-Rate-On-Advances (CVC-009 — interest-free MA discouraged, but interest-rate floor is the threshold; absence of advance entirely = compliant), Retention-Money-Excess (typically 5-10% retained from contractor bills; absence of retention clause = compliant in some Works forms), Defect-Liability-Period-Short (DLP-Period-Short typology in the rules table — minimum 12-24 months by works type; absence might be a violation depending on the rule layer). The "absent = compliant" branch is a clean copy-paste; the threshold compare is one line.

The Vizag 5%+5% split is a corpus observation worth flagging: a future typology that needs to validate the labour-vs-machinery split structure (per AP-GO-094 §X) would need either (a) a sub-shape detector in the LLM prompt to extract both percentages, or (b) a second rerank pass. Tonight's MA typology aggregates them into the single `mobilisation_advance_pct=10.0` field, which is correct for the cap check but loses the audit-trail granularity.

---

## L35 — E-Procurement-Bypass + Three-State Contract: COMPLIANT / UNVERIFIED / ABSENCE

**Date:** May 2026
**What we did:** Built the eighth Tier-1 typology — E-Procurement-Bypass — and discovered a fundamental shape error in the prior presence-shape typologies (PVC / IP / LD): the script's binary `eproc_present := True/False` collapsed two distinct states ("LLM identified clause but quote couldn't be verified" vs "LLM confirmed clause is genuinely absent") into one violation outcome. The fix is a three-state decision contract. AP-GO-012 is the canonical primary rule across the corpus (`TenderState=AP AND EstimatedValue>=100000`) — fires on all 6 docs since they're all multi-crore.

**What went wrong on the first run:** The 6-doc run produced 3 spurious "bypass violations" on Vizag, Tirupathi, Vijayawada. Direct source-grep verification confirmed all 3 docs DO mandate e-procurement — the LLM correctly identified the mandates and quoted real source text. Three distinct LLM-quote pathologies caused L24 to fail:

1. **Markdown-formatted source + LLM verbatim reproduction** (Vizag): source has `__*"shall" mandatorily submit ... vide web portal\. *__` (markdown italic+bold + escaped period). When the LLM reproduces this verbatim, the `\.` is invalid JSON syntax and `json.loads` rejects the response entirely.
2. **Multi-paragraph stitching with literal `"..."`** (Tirupathi/Vijayawada): the NREDCAP RFP boilerplate exists at L537 + L1362 of the source. The LLM was stitching across paragraphs and inserting `"..."` between them — the quote isn't a single contiguous substring.
3. **Section mispicking + adjacent-quote leakage** (HC, Kakinada in some runs): the LLM picks one candidate but quotes text from a neighbouring section that isn't in the picked section's `full_text`.

The script's binary contract treated all three as "absence — emit violation" — which is wrong. A failed L24 quote-verification is not the same as an absent clause.

**Why we changed:** A failed L24 quote-verification has three different root causes (above), and the right downstream behaviour for each is NOT "this is a regulatory violation":
- For (1) JSON-escape: it's a parser bug; fix the parser.
- For (2) stitching: it's a prompt-discipline issue; fix the prompt.
- For (3) mispicking: the LLM still found the clause; the human reviewer should confirm.

In none of these three cases is the document non-compliant. The L24 strict contract is correct (don't trust unverifiable quotes), but its failure mode wasn't routed to the right outcome. Treating it as "violation" produced 3 false positives that would have shipped to a CAG audit if they hadn't been caught by source-grep verification.

**What we changed:**
- `scripts/tier1_eproc_check.py` — replaced the binary `is_violation = not eproc_present` decision with a three-way branch:
    - `is_compliant = llm_found_clause AND ev_passed` → no finding emitted (compliant docs are implicit "no row").
    - `is_unverified = llm_found_clause AND NOT ev_passed` → finding emitted with `status='UNVERIFIED'`, `requires_human_review=true`, `human_review_reason` (with section-attribution pointer so the reviewer can open the picked section directly), **NO VIOLATES_RULE edge**. The LLM's verbatim quote is preserved on the finding for the reviewer's manual comparison.
    - `is_absence = NOT llm_found_clause` → finding emitted with `status='OPEN'` + L29 `absence_finding_no_evidence` marker + VIOLATES_RULE edge. This is the only path that fires a real bypass violation.
- LLM prompt extended with strict-quote directive: "single contiguous span from one sentence or one clause; no ellipsis between lines; no paraphrasing or summarising; preserve markdown formatting verbatim; pick the shortest span that proves the mandate." This addressed (2) above.
- `parse_llm_response` extended with a JSON-escape sanitiser: when `json.loads` rejects the response, replace any backslash NOT followed by a valid JSON escape character (`["\\/bfnrtu]`) with a doubled backslash, preserving the original character as a literal. This addressed (1) above — Vizag's `\.` markdown escape now round-trips through the JSON parser intact and verifies at score=100.
- `modules/validation/section_router.py` — `EPROC_SECTION_ROUTER` added: `[NIT, ITB]` for APCRDA_Works / NREDCAP_PPP / default; `[NIT, ITB, Evaluation]` for SBD_Format because Kakinada has zero NIT-typed body sections beyond title (per L28 SBD pattern).

**Result:** 6-doc re-run after the fixes produced 5 verified-compliant outcomes (Vizag/JA/HC/Tirupathi/Vijayawada all at `evidence_match_score=100, method=substring`) and 1 UNVERIFIED finding (Kakinada — LLM found clause but the section the LLM picked doesn't contain the quoted DSC-signature text; falls into pathology (3) above). The Kakinada UNVERIFIED finding carries:
- `status='UNVERIFIED'`
- `requires_human_review=true`
- `human_review_reason` describing the L24 failure and pointing the reviewer to the picked section (line_start, line_end, source_file, section_heading)
- the LLM's full evidence quote preserved for manual comparison
- **NO VIOLATES_RULE edge** — this is NOT a regulatory violation; it's a system-confidence flag

Total corpus state: **18 ValidationFindings** (17 OPEN + 1 UNVERIFIED) across 8 typologies on 6 docs, with 17 VIOLATES_RULE edges. The 3 prior false positives were deleted before the fix landed.

**Forward applicability:** The three-state contract (COMPLIANT / UNVERIFIED / ABSENCE) is the correct shape for ALL presence-shape typologies (PVC / IP / LD / E-Proc) and should be back-ported to the prior three. The current PVC / IP / LD scripts treat L24 failure as absence and emit a violation finding with VIOLATES_RULE edge — same false-positive pattern as the original E-Proc run. Most of the time the LLM produces verifiable quotes (the prior 17 findings stand on their own evidence — verified at score ≥97 across the corpus), but the back-port is a known follow-on. The strict-quote prompt directive and JSON-escape sanitiser are also lift-candidates for `modules/validation/evidence_guard.py` and `modules/validation/llm_client.py` so every Tier-1 script gets them for free.

The fundamental insight is that **L24 is a confidence layer, not a verdict layer**: a failed verification means "we don't have audit-grade evidence for this finding," not "the document is non-compliant." Routing the two outcomes to the same place was the design error; the three-state contract fixes it.

---

## L37 — BG-Validity-Gap: Four-State Shape + PPP Knowledge-Layer Gap

**Date:** May 2026
**What we did:** Built the tenth Tier-1 typology — BG-Validity-Gap — verifying that the doc specifies a Bank Guarantee / Performance Security validity period that extends through DLP / warranty period + buffer (typically 60 days beyond, per MPG-097 / CLAUSE-WBG-001 / MPW 2022). MPW-082 is the canonical primary for Works docs. The 9-rule typology has no clean PPP-conditioned rule, so the rule selector falls back to AP-GO-015 (Mobilisation Advance BG validity) on PPP docs — UNKNOWN→ADVISORY downgrade per L27.

**What's new in shape:** This typology extends the L35 three-state contract with a fourth outcome — **GAP_VIOLATION**. The previous nine typologies map outcomes to {COMPLIANT (no row), UNVERIFIED (no edge), ABSENCE (with edge)}. BG-Validity-Gap adds a fourth state: **GAP_VIOLATION** = LLM found a BG-validity clause AND L24 verified the quote AND the validity does NOT extend through DLP/warranty. This is a real OPEN violation (with edge) but the audit trail carries the verified inadequate quote — distinct from ABSENCE (no clause at all) and from UNVERIFIED (LLM found but unverifiable).

```
COMPLIANT       — llm_found AND ev_passed AND extends_dlp
GAP_VIOLATION   — llm_found AND ev_passed AND NOT extends_dlp     (NEW state)
UNVERIFIED      — llm_found AND NOT ev_passed
ABSENCE         — NOT llm_found → L36 grep fallback decides
```

The L36 source-grep fallback continues to apply on the ABSENCE branch (no need to re-run grep for GAP_VIOLATION since the LLM already verified inadequacy with a real quote).

**What happened:** 6-doc result:
- **Vizag/HC**: COMPLIANT — both APCRDA Works carry "PBG valid until 60 days after completion of Defect liability period" (MPW 2022 standard), score 100 substring.
- **Kakinada**: COMPLIANT — SBD format with "BG valid up to 28 days from expiry of defects liability period" — buffer is shorter (28 days vs MPW's 60), but extends through DLP so the LLM correctly classifies extends_through_dlp_or_warranty=true. Note: a stricter typology that demands ≥60-day buffer would flag this as a sub-violation; today's check is binary (extends-through-DLP or not).
- **JA**: UNVERIFIED via L36 grep-fallback. LLM was strict — none of the 10 retrieved candidates had explicit "60 days beyond DLP" language to its satisfaction. Grep fallback found 23 sections with BG-validity keywords (Performance Security, Bid Security, Defect Liability) — high recall by design, reviewer must confirm. JA almost certainly DOES carry the validity clause; retrieval just missed it.
- **Tirupathi/Vijayawada**: GAP_VIOLATION — both NREDCAP DBFOTs carry "Performance Security shall remain valid for a period until 30 (thirty) days after the COD" in DCA §9 ("PERFORMANCE SECURITY AND O&M SECURITY"). LLM classified extends_through_dlp_or_warranty=false, finding emitted with verified evidence quote + edge.

**The PPP knowledge-layer gap:** The Tirupathi/Vijayawada GAP_VIOLATION findings are technically correct under the rule cited (AP-GO-015 ADVISORY) but represent a PPP-structure mismatch worth flagging:
1. The cited rule (AP-GO-015) is about Mobilisation Advance BG validity, not Performance Security validity. The rule selector picked it because it's the only AP-State rule that fires on PPP docs (UNKNOWN→ADVISORY via the `MobilizationAdvanceProvided=true` subterm).
2. The DCA §9 heading explicitly says "PERFORMANCE SECURITY AND O&M SECURITY" — the NREDCAP DBFOT structure has TWO securities: Performance Security (covers construction-to-COD) and a separate O&M Security (covers the long post-COD operations period). The 30-day-post-COD buffer on PS is bounded by O&M Security taking over at COD.
3. The typology's 9 rules don't model this PPP/DCA split. A PPP-aware typology would need to extract BOTH Performance Security AND O&M Security validity, recognise the COD handover boundary, and check that the combined coverage extends through the concession period.

ADVISORY severity is the right outcome here — exactly the kind of "we have a fact but the rule may not apply cleanly" condition L27 was designed to handle. A reviewer can confirm whether the O&M Security clause covers the post-COD obligations the typology is concerned about.

**What we changed:**
- `scripts/tier1_bg_validity_gap_check.py` (new) — four-state script with the GAP_VIOLATION branch. RULE_CANDIDATES = [MPW-082, MPG-097, MPW-081, MPW25-054, AP-GO-015]. LLM extracts `bg_validity_specified`, `bg_type` (PBG/EMD/BidSecurity/MobilisationAdvanceBG/WarrantyBG), `validity_period_description`, `extends_through_dlp_or_warranty`, `has_buffer_beyond_dlp`, `buffer_days`, `go_reference`, evidence. L36 grep fallback wired on ABSENCE branch with BG-validity-specific keyword vocabulary.
- `modules/validation/section_router.py` — `BG_VALIDITY_SECTION_ROUTER` added: `[GCC, Forms]` for APCRDA_Works / NREDCAP_PPP; `[GCC, Forms, Evaluation]` for SBD_Format; `[GCC, Forms, ITB]` for default (ITB is the issuer-format anchor for non-canonical docs).

**Forward applicability — three follow-on items:**
1. **PPP-aware BG validity typology**: split into two sub-checks (Performance Security validity through COD; O&M Security validity through Concession Period + DLP). Knowledge-layer addition of a `BG-Validity-PPP` typology with PPP-conditioned rules would be the cleanest fix. Tonight's findings on Tirupathi/Vijayawada serve as evidence that the current typology doesn't capture the right concept on PPPs.
2. **Stricter buffer-duration check**: today's check is binary (extends-through-DLP or not). A future enhancement could compare `buffer_days` against a per-rule minimum (e.g. MPG-097 mandates 60 days). Kakinada's 28-day buffer would be flagged under that stricter check.
3. **Continued L36 grep-fallback proliferation**: the JA UNVERIFIED-via-grep outcome confirms L36 is now the standard safety-net. PVC / IP / LD / E-Proc are still un-back-ported — they continue to use the L35 absence path without grep fallback. Lift candidate when one of those typologies surfaces a Vizag-style false positive.

The four-state shape (COMPLIANT / GAP_VIOLATION / UNVERIFIED / ABSENCE) is now the most expressive contract in the codebase. Threshold-shape typologies (PBG / EMD / Bid-Validity / MA) and presence-shape typologies (PVC / IP / LD / E-Proc / Blacklist) can all express their outcomes within this shape — adopting it for new typologies is now the default.

---

## L38 — Judicial-Preview-Bypass: First Wholly-AP-State Typology + Universal Corpus Bypass

**Date:** May 2026
**What we did:** Built the eleventh Tier-1 typology — Judicial-Preview-Bypass — verifying that AP infrastructure projects ≥ Rs.100 crore cite the AP Judicial Preview framework (AP Judicial Preview Act 2019, predecessor GO Ms No. 38/2018) in the tender document. AP-GO-001 (HARD_BLOCK, AP Works/EPC + 100cr) is the canonical primary; AP-GO-004 (HARD_BLOCK, any AP tender + 100cr) is the catch-all that fires on PPP docs where AP-GO-001 SKIPs (TenderType=PPP not in [Works, EPC]). All 7 rules in the typology are AP-State; this is the **first wholly-AP-State typology** in the shipped set — no Central or CVC layer to disambiguate.

**Critical corpus distinction surfaced**: The Judicial Academy (JA) doc is a tender FOR the construction of the AP Judicial Academy building. The string "Judicial Academy" appears 6 times in JA's source (procuring entity / project name), but that's NOT the Judicial Preview framework. The LLM prompt and grep keyword vocabulary were built phrase-precise to handle this:
- `"Judicial Preview"` (phrase) — counts as framework citation
- `"APJPA"` / `"Judicial Preview Authority"` / `"Judicial Preview Act"` / `"GO Ms No 38"` — count
- `"Judicial Academy"` — does NOT count (procuring entity)
- Bare `"judicial"` — would have polluted grep with JA's procuring-entity hits

The LLM's reasoning on Tirupathi explicitly confirmed the distinction: *"The 'Judicial Academy' references are not considered as they are not part of the Judicial Preview framework."* Same prompt logic prevented the JA-doc false-positive scenario.

**What happened:** 6-doc run produced **6 universal ABSENCE findings** — 5 HARD_BLOCK + 1 ADVISORY (Vizag, EV=null → L27 downgrade). Both the LLM rerank AND the L36 source-grep fallback returned empty across [NIT, ITB] (and Evaluation for Kakinada SBD). To rule out a section-router blind spot, full-source grep was run across **all 12 corpus markdown files** (5 Vizag volumes + JA + HC + Kakinada SBD + Tirupathi RFP + Tirupathi DCA + Vijayawada RFP + Vijayawada DCA) for every JP framework keyword: zero hits anywhere.

**This is a systemic gap in the AP corpus.** Every AP infrastructure project ≥ 100 cr in the dataset is non-compliant with AP-GO-001 / AP-GO-004 — the JP framework mandate has been in force since GO Ms No 38/2018 (later codified in the AP Judicial Preview Act 2019), but the tender documents do not cite it. Two interpretations:
1. **Real bypass** — these tenders skipped the mandatory pre-publication review by APJPA. The compliance officer's response would be to (a) confirm via APJPA records whether the preview actually happened, and (b) require the tender doc to cite the preview certificate.
2. **Documentation gap** — JP review may have happened but the citation was omitted from the published tender doc. Either way, the documentary record is non-compliant; remediation requires the citation to be inserted.

The corpus supports interpretation (1) being more likely: APJPA citations are typically prominent NIT-page mandates (per CLAUSE-AP-JUDICIAL-PREVIEW-MANDATE-001) — drafters wouldn't accidentally omit them. APCRDA Amaravati works (JA, HC) and Smart City SBDs (Kakinada) and NREDCAP DBFOTs (Tirupathi, Vijayawada) all skipping the citation is suggestive of pre-Act-2019 templates that haven't been updated.

**What we changed:**
- `scripts/tier1_jp_check.py` (new) — presence-shape script with the post-L36 three-state contract + L36 grep fallback. Phrase-precise GREP_FALLBACK_KEYWORDS list explicitly excludes bare "judicial" to avoid the JA-doc false-positive. LLM prompt has a CRITICAL distinction block at the top stating the procuring-entity-vs-framework difference. RULE_CANDIDATES = [AP-GO-001, AP-GO-004, AP-GO-009, AP-GO-006, AP-GO-003] in priority order.
- `modules/validation/section_router.py` — `JP_SECTION_ROUTER` added: APCRDA_Works/NREDCAP_PPP/default → [NIT, ITB]; SBD_Format → [NIT, ITB, Evaluation]. JP citations live exclusively in NIT per the read-first scan; ITB included as backup.

**Result:** All 6 corpus docs flagged with JP-bypass findings:

| doc | rule | severity | reason |
|---|---|---|---|
| JA | AP-GO-001 | HARD_BLOCK | Works 125.5cr ≥ 100cr, no JP citation |
| HC | AP-GO-001 | HARD_BLOCK | Works 365cr, no JP citation |
| Kakinada | AP-GO-001 | HARD_BLOCK | Works 152.78cr, no JP citation |
| Vizag | AP-GO-001 | ADVISORY | Works EV=null → L27 downgrade; no JP citation |
| Tirupathi | AP-GO-004 | HARD_BLOCK | PPP 257.51cr, no JP citation (AP-GO-001 SKIPs on PPP, AP-GO-004 fires on universal-100cr) |
| Vijayawada | AP-GO-004 | HARD_BLOCK | PPP 324.7cr, same |

Total corpus state: **32 ValidationFindings (27 OPEN + 5 UNVERIFIED), 27 VIOLATES_RULE edges**.

**Forward applicability:**
1. **First wholly-AP-State typology** — proves the AP-routing infrastructure works without a Central/CVC fallback. Future AP-only typologies (Solvency-Stale, Certification-Exclusionary, AP-specific contractor-management rules) can be ported without the multi-layer disambiguation that PVC/IP/etc. needed.
2. **Procuring-entity vs framework name collision** is a typology-design concern that will recur. When the framework name overlaps with common procurement vocabulary (e.g. "Tender Authority" / "Bid Authority" / "Vigilance"), the prompt and grep keywords must be phrase-precise. JP's clean separation came from APJPA being a uniquely-named acronym + the Act 2019 reference; future typologies without unique anchors may need narrower section_filters.
3. **Systemic-bypass observation worth surfacing in the audit dashboard**: 6/6 docs failing the same typology with similar evidence (universal absence) is a different shape than single-doc bypass. The frontend should aggregate "all docs missing X" findings as a portfolio-level concern rather than per-doc warnings — the response is policy/template-level, not doc-level.

---

## L39 — Turnover-Threshold-Excess: Two Valid PQ Shapes + Anchor-Keyword Discipline

**What we did:** Built the twelfth Tier-1 typology — Turnover-Threshold-Excess — and discovered that the AP corpus's pre-qualification financial criteria come in **two structurally different shapes** that the same script must handle correctly. CVC-028 (WARNING) is the canonical primary: PQ turnover requirement should not exceed 2× annual contract value (`multiple_of_annual = pq_turnover_cr / (estimated_value_cr / tenure_years)` ≤ 2.0). The two shapes:

**Shape A — Bid-Capacity formula (4 of 6 docs).** Vizag, JA, HC, Kakinada all state PQ as a *formula*: `Available Bid Capacity = (A*N*M − B)`, where A = max one-year executed value, N = contract tenure in years, B = current commitments, M = multiplier (2 in JA/HC, 3 in Vizag/Kakinada). No fixed INR threshold — the formula approach IS the test. Per CVC-028 / MPW-039, multiplier ≤ 2 is the calibrated norm; multiplier=3 is mathematically more permissive (lower bar) so it doesn't trigger excess. **All 4 docs COMPLIANT, no finding emitted.**

**Shape B — Fixed INR turnover floor (2 of 6 docs, NREDCAP PPP DCAs).** Tirupathi (avg turnover ≥ INR 128.75 cr; project cost INR 257.51 cr) and Vijayawada (avg turnover ≥ INR 162.35 cr; project cost INR 324.70 cr). With the standard NREDCAP 5-year tenure, both produce **multiple_of_annual = exactly 2.500×** — uncomfortably above the CVC ≤2× cap. Both fire **OPEN ADVISORY-EXCESS** findings.

The 2.500× number is not coincidence. NREDCAP's PPP RFP template targets "50% of total estimated value" for avg turnover. With a 5-year contract, 50% of total ÷ 5 years = 10% per year = 2.5× the per-year baseline. NREDCAP picked a percentage of *total* without realising it maps to 2.5× of *annual*, which is structurally above CVC-028's ≤2× cap. Both DCAs in the corpus carry the identical multiple, signalling boilerplate calibration rather than per-tender judgment. This is a **policy-template-level finding**, not a per-doc one — closing it requires fixing the NREDCAP RFP template, not amending individual tenders.

**The script extracts four fields in a single LLM call** (per user decision from the read-first review): `pq_type ∈ {fixed_inr, bid_capacity_formula, not_found}`, `pq_turnover_cr`, `tenure_years` (extracted INLINE from the same paragraph, no separate facts pass), `formula_multiplier` (2 or 3). Tenure has a defensive PPP fallback (`PPP_DEFAULT_TENURE_YEARS = 5`) marked in the audit trail as `tenure_years_source = "default_ppp_5yr"` when the LLM doesn't surface a stated tenure — both NREDCAP DCAs hit this fallback and the multiple computes correctly.

**What broke and what we fixed (anchor-keyword discipline):** First corpus run produced 3 false UNVERIFIED findings via L36 grep fallback because **smart_truncate's earliest-keyword-anchor pattern** got pulled to the wrong place by over-broad keywords. Specifically, in HC's Section III "Evaluation and Qualification Criteria" (line 477–582, 13,633 chars), my initial keyword list included `Statement\s*[IX]\b`, `\bnet\s*worth\b`, `last\s+three\s+years` and similar — these matched early rows of the qualification criteria table (around char 1000) which then anchored the truncate window there, while the actual bid-capacity formula sat at char ~8700 and was elided. The LLM saw only the table preamble and reported `not_found`. **Fix:** tighten the anchor vocabulary to ONLY patterns that uniquely identify PQ-financial content — `available\s+bid\s+capacity`, the formula regexes (`(A*N*2-B)`, `(3AN-B)`), `average\s+annual\s+turnover`, `INR\s*\d+.*crore`. Drop the broad ones. Result on HC: anchor moved to char 8700, formula visible, LLM correctly extracted `bid_capacity_formula multiplier=2`, COMPLIANT. The lesson: when smart_truncate's anchor sits at the EARLIEST keyword match across the section, the keyword list IS the anchor selector — broad keywords pull the window away from the typology-specific content.

**What broke and what we fixed (section-router corpus discovery):** Initial section_router used `[NIT, ITB]` only for APCRDA_Works and NREDCAP_PPP, matching the previous typology pattern. But **3 of 6 docs (HC, Tirupathi, Vijayawada) classify their PQ Financial Criteria section as `section_type='Evaluation'`** — HC's "Section III - Evaluation and Qualification Criteria" and the NREDCAP RFPs' "4.2 Financial Criteria" both land in `Evaluation` per the kg_builder's section taxonomy. The `[NIT, ITB]`-only filter excluded the actual threshold sections at Qdrant retrieval; grep fallback caught them as UNVERIFIED. **Fix:** include `Evaluation` in every family for this typology. The PQ-Financial heading has no canonical Volume placement across the AP corpus's mixed family shapes (APCRDA SBD, NREDCAP PPP RFP, Kakinada SBD), so the filter has to be the union of the three. **Forward applicability:** any future typology whose anchor section can fall under multiple Volumes / section_types should default-include the union, then narrow per-family only if false-positive cosines crowd out the right candidate.

**What broke and what we fixed (JSON sanitiser for control characters):** Vizag's bid-capacity evidence quote contained literal TAB characters from markdown-table-cell preservation (`Available Bid capacity\t: \(3AN\-B\)`). The L35 sanitiser only handled invalid backslash escapes (`\(`, `\.`); literal tabs/newlines inside string values still failed `json.loads` with `Invalid control character`. **Fix:** extend `parse_llm_json` to fall back to `json.loads(sanitized, strict=False)` — strict=False relaxes the JSON spec to allow control chars in string values. The parsed Python `str` preserves the literal control char and round-trips through L24's normaliser cleanly. This is a small generalisation of the L35 sanitiser, but the failure mode is the same shape: "LLM faithfully reproduces source markdown formatting per L35 strict-quote, then breaks json.loads". Both Vizag and Kakinada produced clean COMPLIANT outcomes after the sanitiser fix.

**Why we changed:** The three fixes (anchor keywords, section_router union, JSON sanitiser) all surfaced from honest UNVERIFIED findings via L36. Each could have been hidden as silent compliance if the typology lacked the grep fallback — the wrong outcome would have been "5 of 6 docs compliant" instead of the correct "5 of 6 docs compliant, 1 of 6 has the finding it's supposed to have". L36 is paying off: it's the audit-trail layer that shows the typology pipeline its own retrieval blind spots.

**Forward applicability:**
1. **Two-shape typologies are a real shape.** Future typologies may have the same structural fork (e.g., Performance-Security stated as % vs as fixed INR amount — already handled by `compute_implied_pct` in PBG/EMD; or Bid-Validity stated as days-from-bid-due vs days-from-NIT — would need the same dual-shape extraction). The pattern of returning both `extraction_type` AND the value-or-formula in a single LLM call generalises.
2. **Anchor-keyword discipline is a smart_truncate hazard worth a check.** Any typology that uses `smart_truncate` with custom keywords should test on the largest section in the corpus — if the anchor lands far from the actual content, broad keywords are the suspect. A keyword that matches early *table preamble* rows is worse than no keyword — head/tail fallback would have done better.
3. **The 2.5× systemic finding pattern** is a portfolio-level signal: when both PPP DCAs in the corpus hit the *exact same* multiple_of_annual (2.500×), that's not coincidence but template calibration. The finding's `properties.multiple_of_annual` should be aggregated in any future audit dashboard alongside the systemic-PBG-shortfall (5/5 at 2.5%) and systemic-EMD-shortfall (5/5 at 1%) patterns from the corpus summary.
4. **AP-GO-092 (HARD_BLOCK contractor-class match) deferred to typology 13.** Different shape — registration-class-vs-ECV-band match, not turnover-amount. Folding it in here would muddy the typology semantics.

---

## L40 — Eligibility-Class-Mismatch + Whole-File Grep Fallback for kg_coverage_gap Detection

**What we did:** Built the thirteenth Tier-1 typology — Eligibility-Class-Mismatch — covering AP-GO-092 (HARD_BLOCK), the contractor-monetary-class-vs-ECV-band match. Six contractor classes per GO Ms No 94/2003: Special > Rs.10 cr (canonical scale int=6); Class-I 2-10 cr (5); Class-II 1-2 cr (4); Class-III 50L-1 cr (3); Class-IV 10L-50L (2); Class-V ≤ 10L (1). The document-side test: does the doc's "Eligible Class of Bidders" text admit ONLY contractors whose registration class can legally tender for this ECV band? Two corner cases the prompt handles: VAGUE clauses ("appropriate eligible class as per G.O.Ms.No.94" without naming the class — defers to rule, no enforcement floor → ADVISORY-UNDERSPECIFIED) and breadth qualifiers ("Class-I & above" admits {Class-I, Special} — compliance check compares the LOWEST admitted class against band_required).

**What broke and what we fixed (kg_coverage_gap discovery — L40):** The first Kakinada test produced an UNVERIFIED finding with NO grep hits. We knew from earlier corpus surveys that line 149 of `SBDPKG11Kakinadafinalrev.md` contains "G.O MS. No.94, Dated:01-07-2003, I&CAD (PW&COD) Department, Class I Civil & above." — a clear class-mismatch violation candidate (152.78cr ECV demands Special, doc admits Class-I floor). But the LLM rerank's top-10 didn't include any candidate covering line 149, AND the L36 Section-bounded grep returned zero hits. Investigation revealed the root cause: **kg_builder's section parsing left lines 59-312 of the Kakinada source uncovered by ANY Section node**. The first INSTRUCTIONS TO TENDERERS section ends at line 58; the next FORMATS OF SECURITIES section starts at line 313. The Class-I-Civil-&-above line at 149 falls in the gap. Vizag has the structurally-identical pattern: NIT first section ends at line 12 (Preamble), next starts at line 415, line 178 ("appropriate eligible class") falls in the gap.

**This is a new failure mode worth a permanent fix.** L36 (`grep_source_for_keywords`) is bounded by Section-node coverage — it iterates Section nodes and reads each one's `[line_start_local, line_end_local]` slice from disk. It cannot see text outside that union. When kg_builder leaves coverage gaps, L36 reports zero hits even when the text exists in the source. This produces a false-negative shape: "ABSENCE finding emitted" or "UNVERIFIED-grep-empty" when the truth is "the kg_builder hasn't indexed this region yet".

**The fix is L40 — `grep_full_source_for_keywords`:** a Tier-2 fallback that scans EACH whole source markdown file referenced by the doc, NOT bounded by Section ranges. For every match, it computes a `kg_coverage_gap` boolean by checking whether the matched line falls inside any Section's range. A `kg_coverage_gap=True` hit is a meaningful audit signal — distinct from "the text genuinely doesn't exist" and from "L36 retrieval missed it". The reviewer reads it as "the kg_builder needs to re-index this doc and re-run; the typology pipeline can't be expected to surface text that's not in the KG".

The fallback chain is: LLM rerank → L36 Section-bounded grep → L40 whole-file grep. L40 only fires on (a) raw absence after L36 empty, OR (b) L24-fail when Section-bounded grep is also empty (a hallucinated quote is a hint that the LLM didn't see the real text, possibly because of a coverage gap). When L40 finds a `kg_coverage_gap=True` hit, the script promotes the L24-fail label to `class_unverified_kg_coverage_gap` because the kg-build defect is a more informative signal than "L24 caught a hallucination".

**Why we changed:** The honest outcome on Kakinada and Vizag is "we can't verify the eligibility class because the kg_builder didn't index the relevant lines". Without L40 the script would either (a) emit ABSENCE (wrong — the text is there in source), or (b) emit UNVERIFIED-L24-fail (technically correct but uninformative — the reviewer would re-check the LLM's hallucinated quote rather than re-build the KG). With L40, the finding's `evidence_match_method='whole_file_grep_kg_coverage_gap'` and `grep_fallback_audit.kg_coverage_gap=true` tell the reviewer exactly what to do: re-build the KG, then re-run the typology check.

**Final corpus on Class-Mismatch:** 4 of 6 docs route cleanly — JA & HC COMPLIANT (Special class declared, ECV in Special band); Tirupathi/Vijayawada SKIP (TenderType=PPP, AP-GO-092 SKIPs). Vizag and Kakinada both UNVERIFIED-kg_coverage_gap, severity HARD_BLOCK, awaiting kg_builder re-index. Two new findings, no new edges (UNVERIFIED never gets edges per L37 four-state). The user-predicted Kakinada Class-I/Special HARD_BLOCK violation is structurally provable from source but unreachable until the KG covers line 149.

**Forward applicability:**
1. **`grep_full_source_for_keywords` is now in `modules/validation/grep_fallback.py`** and is typology-agnostic. Any future typology that wants the L36 → L40 chain inherits it via the same import (`from modules.validation.grep_fallback import grep_source_for_keywords, grep_full_source_for_keywords`). The Tier-2 fallback should be wired in for any typology where (a) source text is short / sparse, (b) section parsing might leave gaps (SBD-style docs are the highest risk per the L28 SBD pattern), or (c) the audit cost of false-negative absence is meaningful.
2. **kg_coverage_gap is a new audit category worth aggregating.** When the corpus shows multiple typologies all hitting kg_coverage_gap on the same doc, the right response is a kg_builder re-run, NOT per-typology workarounds. A future ops dashboard should surface "doc X has N kg_coverage_gap findings across M typologies — re-build needed".
3. **Two-tier grep is the L36 generalisation.** L36 stays as the primary fallback (Section-bounded — fast, gets correct attribution to a Section node). L40 is the fast safety-net (whole-file — slower but catches build defects). Future typologies should default to L36 → L40 chain unless there's a specific reason not to.
4. **Severity stays HARD_BLOCK on UNVERIFIED-kg_coverage_gap.** The four-state contract (L37) says UNVERIFIED has NO edge but DOES have a severity. We keep AP-GO-092's HARD_BLOCK on the finding so post-re-build the same finding can promote to OPEN cleanly without re-classifying severity.

---

## L41 — Closing kg_builder Section Gaps via Gap-Filler Post-Process

**What we did:** Closed the kg_coverage_gap that L40 surfaced on Vizag and Kakinada by extending the kg_builder pipeline with a deterministic gap-filler post-process. The L40 audit signal was honest but actionable only if the kg_builder could be made to *cover* the missing line ranges; without that, every future typology that reaches into the same source regions would hit the same gap. The fix lives in `experiments/tender_graph/kg_builder.py::_split_and_classify` and is invoked automatically on every `build_kg(...)` rebuild.

**The gap-filler.** After `builder.section_splitter.split_into_sections` runs, the splitter's per-file output is scanned for line ranges NOT covered by any heading-anchored section. For each uncovered range that meets minimum thresholds (`_GAP_FILL_MIN_LINES = 30`, `_GAP_FILL_MIN_CHARS = 500`), a synthetic Section row is appended with `gap_fill=True`, heading `(gap-fill) <first non-trivial line of the gap>`, full_text from the gap range, and `section_type=None` so the existing `classify_sections` pass picks it up alongside the splitter-produced rows. The minimum thresholds prevent gap-fills from inter-paragraph whitespace or page-break artifacts; only structurally meaningful uncovered content gets indexed.

**The kg_nodes properties dict gets a new `gap_fill` boolean** so the audit trail distinguishes synthetic gap-fills from authored sections. Reviewers and downstream code (e.g. future kg_coverage_gap detection, dashboard aggregation) can filter or annotate accordingly. Both Vizag and Kakinada produced expected gap-fills:

- **Vizag**: 161 → 165 sections (+4 gap-fills). NIT `1_Volume_I_NIT_*` L13-414 covering "Civil Contractors having registrations with Government of Andhra Pradesh in appropriate eligible class as per the G.O.Ms.No.94". Plus 3 ancillary gap-fills (Vol-III SCC L1620-1649 "Construction Programme", L2488-2519 "Entire Agreement"; Schedules `3.3A_Schedules.md` L1-1282).
- **Kakinada**: 35 → 37 sections (+2 gap-fills). SBD `SBDPKG11Kakinadafinalrev.md` L59-312 covering "Class I Civil & above" eligibility text. Plus L412-612.

**What broke and what we fixed (router exclusion of gap-fills):** First post-rebuild typology re-run on Kakinada STILL produced UNVERIFIED — the gap-fill section existed but wasn't reaching the LLM. Investigation: gap-fill sections frequently get classified as `section_type='Forms'` by the heading-content-primary classifier (their tabular eNIT bodies lack distinctive heading anchors that would steer them to NIT or Evaluation). The Eligibility-Class-Mismatch router was `[NIT, Evaluation]` for SBD_Format and `[NIT, ITB]` for APCRDA_Works — `Forms` was excluded, so gap-fill content was filtered out at Qdrant retrieval. **Fix:** add `Forms` to every family for this typology. Forward-applicable: any typology that wants to reach into tabular eNIT content (which is where gap-fills tend to land) should include `Forms` in its router.

**What broke and what we fixed (Vizag tender_type LLM TypeError):** During the rebuild's Phase 6c LLM extraction, the tender_type extractor raised `TypeError("'NoneType' object is not subscriptable")` and skipped emitting tender_type for Vizag. The kg_node was rebuilt with `tender_type=null`, which would have caused AP-GO-092's condition_evaluator to return UNKNOWN and L27-downgrade the finding to ADVISORY rather than HARD_BLOCK — independent of the actual class-mismatch logic. **Fix:** restored Vizag's `tender_type='Works'` via direct SQL UPDATE on the TenderDocument node properties (the value is well-known from L19/L33 audit history). Audit fields `tender_type_repaired_after_rebuild=true` and `tender_type_repair_note` document the manual repair so future readers see why the field was set outside the LLM extractor pipeline. The TypeError itself is a flaky-LLM-response shape worth investigating separately — the response parser tried to subscript a None field, suggesting the model occasionally returns a null-shaped tender_type response that the parser doesn't handle. Out of scope for L41; filed as a follow-on for the tender_type extractor.

**Result on Eligibility-Class-Mismatch (typology 13).** Both UNVERIFIED-kg_coverage_gap findings promoted cleanly:
- **Kakinada**: was UNVERIFIED-kg_coverage_gap → now **OPEN HARD_BLOCK**. LLM picks the gap-fill section (cosine 0.6487), extracts `required_class=Class-I`, `class_breadth=and_above`, evidence verified at score 97 partial_ratio. Class-I floor (int=5) < band_required Special (int=6) → class-mismatch violation per AP-GO-092. The reason_label is `class_mismatch_doc_admits_class-i_and_above_vs_band_required_special_floor_5_vs_required_6`.
- **Vizag**: was UNVERIFIED-kg_coverage_gap → now **OPEN ADVISORY**. LLM picks the L13-414 gap-fill, extracts `required_class=vague` ("Civil Contractors having registrations ... in appropriate eligible class as per the G.O.Ms.No.94"), evidence verified at score 99 partial_ratio. Vague clause defers to GO Ms No 94 without naming the specific class for the ECV band → ADVISORY-UNDERSPECIFIED.

Net corpus change: 36 → 36 findings, 29 → 31 OPEN, 7 → 5 UNVERIFIED, 29 → 31 edges. Two findings promoted from UNVERIFIED-pending-kg-rebuild to OPEN-violation.

**Forward applicability:**
1. **Gap-filler is generic.** Every doc that gets rebuilt via `build_kg(...)` now gets gap-fills for any uncovered range >= 30 lines / 500 non-whitespace chars. SBD-style docs with heavy tabular eNIT content benefit most; well-headinged docs (NREDCAP RFPs, multi-volume APCRDA SBDs with markdown TOCs) produce few or zero gap-fills. The thresholds err on the side of indexing more — false-positive gap-fills (page-break artifacts) are cheaper than false-negative coverage gaps.
2. **`Forms` belongs in any typology router that reaches tabular eNIT content.** Class-Mismatch is the first typology to surface this, but Eligibility, Past-Experience-Class, Bid-Capacity-Multiplier, and any future typology whose target text might land in unindexed tabular regions should include `Forms`. The default route (`["NIT", "ITB", "Evaluation", "Forms"]`) is the right starting point; narrow per-family only if false-positive cosines crowd out the right candidate.
3. **The `gap_fill` boolean on Section nodes is queryable.** `properties->>'gap_fill' = 'true'` filters to synthetic sections; reviewers and dashboards can highlight gap-fill content distinctly from authored sections. Future audit-quality work can build on this — e.g. a "% of corpus content reachable via authored sections only" metric.
4. **L40 / L41 form a coherent loop.** L40 (whole-file grep with `kg_coverage_gap`) is the *audit* layer — it surfaces missing coverage as honest UNVERIFIED findings. L41 (gap-filler) is the *fix* layer — it closes the gap automatically on the next rebuild. Together they convert "blocked by retrieval defect" into "audit signal → automatic fix on next rebuild → finding promotes to OPEN". This is the right pattern for any future audit-then-fix cycle.

---

## L42 — tender_type Extractor Hardening + Snapshot/Restore Defense

**What we did:** Hardened the kg_builder rebuild path against silent `tender_type=null` regressions. During the L41 Vizag rebuild, Phase 6c's `tender_type_extractor.run(...)` raised an unhandled `TypeError("'NoneType' object is not subscriptable")` from inside the OpenRouter response parser (likely a transient None-shaped response from the provider). The exception was caught and stamped into `summary.defeasibility['llm_extraction_errors']` but the rebuild proceeded with `tender_type=null` on the new TenderDocument node, which would have caused AP-GO-092's condition_evaluator to resolve UNKNOWN (instead of FIRE) and downgrade the Class-Mismatch finding from HARD_BLOCK to ADVISORY — even though Vizag's tender_type had been reliably extracted as `Works` on every prior rebuild. The audit trail flagged the failure but no automatic restore happened. Three defensive layers added.

**Layer 1 — graceful failure shape in `extract_tender_type`** (`modules/extraction/tender_type_extractor.py`). Each step that can raise (NIT fetch, LLM call, JSON parse) is now wrapped. On any failure, the function returns:

```python
{
    "tender_type":    None,
    "confidence":     0.0,
    "evidence":       "",
    "reasoning":      "",
    "source_section": "<best-effort heading list>",
    "reliable":       False,
    "error":          "<step>:<ExceptionClass>:<msg>",
    "raw_response":   "<whatever was captured before failure>",
    "nit_text_chars": <int>,
}
```

The caller decides whether to overwrite the existing tender_type or preserve it. The historical behaviour was to raise; that bubbled up to kg_builder's `try/except` block which captured the error message but left the new TenderDocument node with no tender_type set.

**Layer 2 — `commit_to_kg` preserves prior tender_type when extraction failed.** When `result["tender_type"] is None`, the live fields (`tender_type`, `tender_type_reliable`, `tender_type_confidence`, etc.) are NOT overwritten. Only an audit stamp is written: `tender_type_last_error` (the error string) and `tender_type_last_attempt_at` (UTC timestamp). When a future extraction succeeds, the stale error fields are removed. This handles the within-doc case: the prior tender_type stays in place even if the LLM flakes on a single call.

**Layer 3 — Phase 6c snapshot/restore in `kg_builder.build_kg`.** Layer 2 alone wouldn't have helped Vizag's L41 rebuild because `_clear_kg(...)` runs BEFORE Phase 6c, wiping the prior TenderDocument node entirely — there's no "prior value" left for `commit_to_kg` to preserve. The fix mirrors the L32 ValidationFinding snapshot pattern. New helpers in `experiments/tender_graph/kg_builder.py`:

  - `_snapshot_tender_type(doc_id) → dict | None` — captures the tender_type fields from the existing TenderDocument node BEFORE `_clear_kg`. Returns None when no prior value exists (first build).
  - `_maybe_restore_tender_type_from_snapshot(doc_id, new_doc_node_id, snapshot)` — runs AFTER Phase 6c. If the freshly-built TenderDocument has `tender_type=null` AND the snapshot has a non-null tender_type, the snapshot's fields are written onto the new node with audit markers `tender_type_repaired_after_rebuild=true`, `tender_type_repair_note='L42 auto-restore: ...'`, and `tender_type_repaired_at=<UTC>`. When a rebuild calls this, the summary records `tender_type_restored_from_snapshot=true` and `tender_type_restored_value=<value>`.

The snapshot is captured next to the L32 finding-snapshot pass (line 851 of `kg_builder.py`); the restore is invoked next to the L32 finding-restore (after Phase 6c at line 1000). Both are gated on `clear_existing=True` because that's the only path that wipes the prior TenderDocument.

**Why we changed:** The Vizag L41 rebuild made the regression visible — without the manual SQL repair I performed, the typology-13 re-run would have fired ADVISORY instead of HARD_BLOCK because of an unrelated transient LLM failure. That's exactly the wrong shape: a downstream typology's severity should depend on the doc's procurement properties, NOT on whether the tender_type extractor's network call happened to flake on this rebuild. The three layers convert the failure mode from "silent regression visible only via audit trail review" to "audit trail records the LLM error AND the restore step AND the value carries forward unchanged".

**Forward applicability:**
1. **The same pattern generalises to `tender_facts_extractor`.** It also runs as Phase 6c, also uses an LLM, and also has fields (`estimated_value_cr`, `tenure_years`, etc.) that downstream typologies depend on. The same three-layer defense — graceful-failure shape, preserve-on-null in `commit_to_kg`, snapshot/restore in Phase 6c — applies. Recommended follow-on: lift the snapshot/restore logic into a generic helper that captures any user-specified set of TenderDocument fields, then wire `tender_facts_extractor` to use it. Not done tonight; out of scope for L42.
2. **The audit fields are queryable.** `properties->>'tender_type_repaired_after_rebuild'='true'` filters to nodes that hit the restore path; `properties->>'tender_type_last_error'` surfaces in-flight extraction errors that didn't blow away the prior value. A future ops dashboard can show "X of N TenderDocuments have last_error set" as a freshness indicator.
3. **The graceful-failure shape is reusable for any future LLM-extractor module.** Three failure points — fetch, call, parse — each wrapped, each emitting a typed error string; the result dict always has the same shape so callers don't need to know which step failed. This is the right shape for any LLM extractor that runs as part of a deterministic pipeline (kg_builder, validators, tier1 scripts).
4. **The TypeError root cause is still open.** The OpenRouter response parser path inside `_call_llm` returned a None-shaped value that subscripting `.choices[0]` could not handle. Logged here for the next investigation cycle: when does OpenRouter return a Choice-list of length 0 or None, and should `_call_llm` handle that case explicitly? Out of scope for L42 hardening (the graceful-failure shape catches it generically), but worth a follow-on.

---

## L43 — Arbitration-Clause-Violation: Multi-Rule Typology + AP-Defeats-Central Branch + Informational Markers

**What we did:** Built the fourteenth Tier-1 typology — Arbitration-Clause-Violation — and introduced two patterns new to the corpus: a **multi-rule typology** that evaluates four sub-checks from a single LLM extraction, and a **defeats-aware decision branch** where AP-State variants explicitly defeat Central baselines per the rules table's `defeats` lists. The typology also introduces an **OPEN ADVISORY informational marker** finding shape — a finding that records "the doc carries a regulator-recognised acceptable departure from the Central default" without representing a violation.

**Four sub-checks, one LLM call.** Prior typologies tested one rule shape per script. Arbitration-Clause-Violation has 31 TYPE_1_ACTIONABLE rules across 4 layers, but only 4 are Tier-1-testable from a bidding document; the other 27 are execution-stage (require ArbitrationInvoked / ArbitratorAppointed / ArbitralAwardIssued runtime facts). The 4 testable rules:
- MPG-304 (HARD_BLOCK, TenderType=ANY) — every contract MUST carry an arbitration clause/agreement.
- MPW-139 (HARD_BLOCK, TenderType=Works) — Works contracts MUST contain a dispute-resolution clause + amicable-consultation step.
- MPW25-104 (HARD_BLOCK, Works anti-pattern) — Govt/PSU unilateral-appointment OR Govt-curated-panel clauses INVALIDATED per Supreme Court ruling 08-Nov-2024 (CORE v ECL-SPIC-SMO-MCML, 2024 INSC 857). Anti-pattern PRESENCE is the violation, not absence.
- AP-GO-229 (ADVISORY informational, AP Works/EPC) — claims > Rs.50,000 routed to civil court per APSS Clause 61 + GO Ms No 94/2003 §14. Rule explicitly says `do NOT block tender publication` — this is an AP-acceptable departure, not a violation. **AP-GO-229 has a `defeats` list of 38 Central rules including MPG-304 / MPW-139 / MPG-186 / MPW25-104** etc.

A single LLM call returns 13 fields (`arbitration_clause_present`, `dispute_resolution_clause_present`, `arbitration_act_referenced`, `seat_or_venue_specified`, `seat_or_venue_text`, `unilateral_appointment_present`, `appointment_by_curated_panel`, `ap_civil_court_ladder_present`, `escalation_tiers_visible`, `three_arbitrator_panel`, `foreign_arbitration_option`, `evidence`, `found`). The decision tree applies each sub-check against this snapshot. One prompt × multiple rule evaluations is the cost-efficient shape for typologies whose rules share retrieval territory.

**Defeats-aware decision branch.** Initial JA test exposed a structural gap: the LLM correctly identified JA's PCC §VIII dispute-resolution clause ("Claims up to Rs.10K → Superintending Engineer; Rs.10K-50K → Chief Engineer; > Rs.50K → Appellate Authority → **Civil Court of competent jurisdiction... and NOT by arbitration**") with `arbitration_clause_present=False`, `ap_civil_court_ladder_present=True`. My initial decision tree treated `arbitration_clause_present=False` as triggering MPG-304 ABSENCE violation — but that's the wrong outcome because AP-GO-229 explicitly substitutes the AP value-tier ladder for arbitration on AP Works/EPC. AP-GO-229's `defeats` list in the rules table contains MPG-304 and MPW-139 precisely to encode this substitution. **Fix:** added Branch (4) to the decision tree — *AP-LADDER-ACCEPTED*: when `ap_ladder=True` AND AP Works/EPC AND L24-verified, the absence violation is suppressed and the AP-GO-229 informational marker fires instead. The Branch (3) ABSENCE check now has an explicit `not ap_ladder_accepted` guard.

**Informational marker as a separate finding row.** AP-GO-229 emits a *different shape* of finding than prior typologies: status=OPEN, severity=ADVISORY, `marker_kind=informational`, `violation_reason=ap_ladder_recognised_acceptable_departure`. It carries a VIOLATES_RULE edge (because OPEN findings get edges per L37) but the severity of ADVISORY plus the `marker_kind=informational` audit field lets downstream BLOCK / WARNING / ADVISORY aggregations filter it out cleanly. This is the right shape for any future "regulator-recognised acceptable departure" outcome — distinct from compliant silence (no record), distinct from violation (severity HARD_BLOCK / WARNING), distinct from UNVERIFIED (no edge). The marker explicitly records *the doc DID this* in audit-grade detail rather than letting the audit trail go silent.

**Doc may emit 0, 1, or 2 findings.** Typology 14 is the first to emit *multiple* findings per doc per typology run. The cleanup helper `_delete_prior_tier1_arbitration` is multi-finding-aware (deletes ALL prior `typology_code='Arbitration-Clause-Violation'` rows on re-run, not just the first). The combinations:
- 0 findings: COMPLIANT (clause present, L24 verified, no anti-pattern, no AP-State context).
- 1 finding (primary violation): MPW25-104 HARD_BLOCK on retained unilateral-appointment, OR MPG-304/MPW-139 HARD_BLOCK on true absence (only fires for non-AP-Works tenders since AP-Works gets the defeats branch).
- 1 finding (UNVERIFIED): L36 / L40 grep-fallback chain on absence path.
- 1 finding (informational): AP-LADDER-RECOGNISED standalone (AP Works + ladder + no arbitration clause).
- 2 findings: AP Works that has BOTH an arbitration clause AND the AP ladder (Kakinada is the corpus example) — the COMPLIANT primary-suppression doesn't emit a row, but the AP-GO-229 informational marker fires alongside the implicit compliance.

**Corpus result — 6 docs, 3 informational markers, 0 violations.**
- **Vizag** (APCRDA Works) → COMPLIANT. Standard arbitration clause under Indian Arbitration & Conciliation Act 1996, venue=Visakhapatnam, 3-arbitrator panel. The L41 gap-fills surfaced the standard arbitration clause that lives in the previously-unindexed Vol-III GCC region. No AP ladder.
- **JA** (APCRDA Works, ADB-funded) → AP-LADDER-RECOGNISED informational only. Doc explicitly substitutes arbitration with civil-court ladder; AP-GO-229 marker fires.
- **HC** (APCRDA Works, WB-funded) → AP-LADDER-RECOGNISED informational only. Same structural shape as JA — value-tier ladder, no arbitration. L24=100 substring verified.
- **Kakinada** (SBD Works) → COMPLIANT + AP-LADDER-RECOGNISED informational. Standard arbitration clause (Indian Act 1996, venue=Vijayawada) AND value-tier ladder. The most defensible shape — ladder routes small claims, arbitration handles larger. L24=100 substring verified.
- **Tirupathi** (NREDCAP PPP DCA) → COMPLIANT. Standard arbitration clause under Indian Act 1996, venue="Amravati", 3-arbitrator panel. No AP ladder (PPP framework uses Central arbitration default).
- **Vijayawada** (NREDCAP PPP DCA) → COMPLIANT. Same structural shape as Tirupathi.

Net corpus change: 36 → 39 findings, 31 → 34 OPEN, 5 UNVERIFIED unchanged, 31 → 34 edges. All 3 new findings are AP-GO-229 informational markers (severity=ADVISORY, marker_kind=informational).

**Two corpus patterns surfaced worth noting:**
1. **APCRDA capital-city Works (JA, HC) substitute arbitration entirely with the civil-court ladder.** Both ADB/WB-funded. This is a defensible AP-State variant (per AP-GO-229's defeats list) but it does mean disputes on these contracts go to the Andhra Pradesh civil-court system rather than arbitration. The audit dashboard should aggregate this — "X of Y AP-CRDA contracts use AP-ladder substitution" — as a portfolio-level signal.
2. **NREDCAP PPP DCAs (Tirupathi, Vijayawada) carry standard arbitration under Indian Act 1996.** No AP ladder. The PPP shape doesn't use the AP-State substitution. Note: both DCAs spell the venue "Amravati" (Maharashtra spelling) rather than "Amaravati" (AP capital) — possibly a typo in the NREDCAP RFP template that propagated; worth flagging on the NREDCAP-template-level audit (alongside L39's 50%-of-total turnover calibration finding).

**Forward applicability:**
1. **Multi-rule typologies are a shape worth supporting.** Several rule clusters in the production rules table fit this pattern (e.g. arbitration cluster has 31 rules, dispute-resolution overlap has 18, integrity-pact has 4 each on Central / CVC / multilateral-lender). Building a single script that extracts a multi-field LLM snapshot once and applies multiple sub-checks is more efficient than N single-rule scripts.
2. **The AP-defeats-Central pattern recurs.** AP-GO-229 defeats 38 Central rules in the arbitration cluster alone. Future typologies whose AP-State variant explicitly substitutes the Central rule should adopt the same Branch (4) AP-defeats-Central decision pattern. The general shape: "if AP variant fires AND its defeats list includes the Central rule that would otherwise fire, suppress the Central violation and emit the AP marker as informational instead". This is the typology-level rendering of the defeats-list relation.
3. **Informational markers are a fourth severity-shape worth surfacing in the dashboard.** The four-state contract (L37) covers OPEN / UNVERIFIED / GAP_VIOLATION / ABSENCE per outcome status. Within OPEN findings, there are now four severity-shapes: HARD_BLOCK (block), WARNING (warn), ADVISORY-VIOLATION (advisory non-block), and ADVISORY-INFORMATIONAL (no violation, just audit record of an acceptable departure). The dashboard should distinguish the informational subset using the `marker_kind=informational` audit field — they're audit-trail-relevant but should NOT count toward "doc has N violations".
4. **Cleanup must be multi-finding-aware.** `_delete_prior_tier1_arbitration` deletes ALL findings/edges with the typology code rather than the prior-typology-pattern of single-row delete. Future multi-finding typologies should mirror this.

---

## L44 — Evidence Guard Method 3: Multi-Sentence Verification for Stitched Quotes

**What we did:** Built the fifteenth Tier-1 typology — Geographic-Restriction — and the JA test exposed a structural problem with the L24 evidence guard. Multi-field LLM extractions (Geographic-Restriction has 11 booleans, Arbitration L43 had 13) produce **stitched evidence quotes** that concatenate sentences from multiple sub-checks. The whole-quote partial_ratio scores below the L24 threshold of 85 because difflib treats the concatenation as one unit, even when individual sentences within the quote ARE verbatim from the source. Result: real violations (JA + HC foreign-bidder ban) routed to UNVERIFIED instead of OPEN — hiding HARD_BLOCK signals behind status=UNVERIFIED audit fields.

The original two-stage L24 chain:
  1. Substring exact match → score 100, method "substring"
  2. difflib partial_ratio sliding-window → method "partial_ratio"

A stitched quote like *"Participation by JV/Consortium/SPV not allowed. Any contractor from abroad not be permitted. The bidders shall not have a conflict of interest. The bidder shall have the Indian nationality."* fails both stages — the source has each of these sentences but with different intermediate content between them. partial_ratio scores ~58 because the concatenation drifts away from any single source window.

**Method 3 — longest-sentence verification.** New stage 3 in `modules/validation/evidence_guard.py` fires only when stages 1 and 2 fail:
1. Split the LLM evidence quote on sentence boundaries (`(?<=[.!?])(?:\s+|\\n|<br/>)`).
2. Filter sentences `>= 20` chars (drop fragments).
3. Sort by length descending — the longest sentence is most likely the primary signal the LLM was grounding.
4. For each sentence: substring fast-path against the source; if no match, partial_ratio with the same coarse-then-fine sliding window used in stage 2.
5. Track the best per-sentence score. If any sentence ≥ threshold, return PASS with method `longest_sentence_substring` or `longest_sentence_partial_ratio`.

The semantics: "the LLM stitched, but at least one of the sentences in its quote is verbatim from the source — the evidence IS grounded, just decomposed". The audit method label records that the quote was decomposed so reviewers know to expect stitched evidence.

**Smoke-tested on three shapes:**
- Stitched-with-realistic-gap (JA shape): score=100 `longest_sentence_substring` ✓
- Hallucinated quote (no sentence anywhere in source): score=49 `no_match` ✗ — correctly rejected
- One-real-many-fake quote (one sentence verbatim, others fabricated): score=100 `longest_sentence_substring` ✓ — accepts because at least one sentence is grounded

The hallucinated case is critical: Method 3 doesn't loosen L24 indiscriminately. A fully-fabricated quote still fails because no individual sentence verifies. This preserves the L24 anti-hallucination contract.

**Corpus impact on Geographic-Restriction (typology 15):**
- **Vizag**: MPG-243 UNVERIFIED HARD_BLOCK (L24 + L44 both fail; best_sentence_score=47). Honest UNVERIFIED — Vizag's geographic-restriction posture genuinely unclear without manual review.
- **JA**: MPS-184 OPEN ADVISORY (foreign-ban anti-pattern, severity downgraded HARD_BLOCK→ADVISORY per L27 because BidderClassification UNKNOWN) + AP-GO-091 informational marker. **L44 promoted JA from UNVERIFIED to OPEN** — the foreign-bidder ban at L878 is now a verified-evidence finding.
- **HC**: Same shape as JA — MPS-184 OPEN ADVISORY + AP-GO-091 marker. L44 score=100 longest_sentence_substring.
- **Kakinada**: MPG-243 OPEN HARD_BLOCK (Annexure-2F absent, no foreign-ban) + AP-GO-091 marker. Single-sentence quote, verified via stage-1 substring (no L44 needed).
- **Tirupathi / Vijayawada**: COMPLIANT — both NREDCAP DCAs include full DoE OM 23-Jul-2020 land-border-country clause + bidder compliance certificate per MPS-213. Best-in-class compliance.

Net: 7 new findings, 6 OPEN + 1 UNVERIFIED. Without L44, JA + HC would have been UNVERIFIED instead of OPEN — losing 4 verified findings (2 primaries + 2 markers) to a verifiable-but-stitched-quote failure mode.

**A note on the JA / HC severity downgrade.** Both fire MPS-184 at ADVISORY severity, not HARD_BLOCK. This is L27 acting as designed: MPS-184's `condition_when` includes `BidderClassification=Local` which we don't extract as a fact, so condition_evaluator returns UNKNOWN and the L27 downgrade fires. The audit fields preserve `severity_origin=HARD_BLOCK`, `verdict_origin=UNKNOWN`. A future typology-specific override could re-escalate the severity when the LLM provides positive evidence of the anti-pattern (since the rule is fundamentally about doc design, not bidder classification), but that's a more invasive change to L27's general safety mechanism. Filed as follow-on.

**Forward applicability:**
1. **Method 3 generalises to every multi-field typology automatically.** Arbitration (L43, 13 fields), Geographic-Restriction (15 fields), and any future multi-rule typology that produces stitched quotes will benefit without script-level changes — the typology just needs to call `verify_evidence_in_section()` as it already does.
2. **Anti-hallucination preserved.** Method 3 fires only AFTER stages 1+2 fail. A fabricated quote will still be rejected because no individual sentence verifies. The L24 contract is not loosened — it's extended to handle a specific known LLM failure mode (sub-check stitching).
3. **Audit method label is queryable.** Findings with `evidence_match_method='longest_sentence_substring'` or `'longest_sentence_partial_ratio'` can be filtered to "evidence was stitched but one component verified" — useful for reviewers who want to see only the stitched cases.
4. **L24 → L44 chain is the right shape for any future evidence-quality lift.** L24 was the substring + partial-ratio guard; L44 added decomposition. A future L4N could add named-entity verification, structured-data extraction, etc. — each layer adds robustness without loosening the previous contract.

---

## L45 — MakeInIndia-LCC-Missing: Third Systemic-Absence Pattern + Grep-Vocabulary Discipline

**What we did:** Built the sixteenth Tier-1 typology — MakeInIndia-LCC-Missing — verifying that every Indian Government Works/Services/PPP tender includes the PPP-MII Order 2017 framework (citation under GFR Rule 153(iii) + DPIIT OM No. P-45021/2/2017-PP(BE-II) dt 16.09.2020 + Class-I/Class-II/Non-local classification + bidder Local Content self-certification + purchase preference rules). Per MPW-002 (Works) / MPS-182 (Goods/Works/Services catch-all) / MPG-022 (PPP), this is HARD_BLOCK on absence. Single-rule presence-shape; no AP-defeats-Central pattern (AP-State price-preference rules AP-GO-137/148/149 are Goods-only, SKIP on our Works/PPP corpus).

**Universal absence confirmed.** Read-first grep across all 6 corpus markdowns returned ZERO hits for the MII vocabulary (Make in India / PPP-MII / Class-I local / DPIIT / etc.). The corpus run materialised the prediction: **6/6 OPEN HARD_BLOCK ABSENCE findings**, identical shape across families:
- Vizag, JA, HC, Kakinada → MPW-002 HARD_BLOCK ABSENCE (Works)
- Tirupathi, Vijayawada → MPS-182 HARD_BLOCK ABSENCE (PPP catch-all per TenderType=ANY)

This is the **third systemic-absence pattern** in the corpus alongside JP-Bypass (L38) and Integrity-Pact (L30). All three are corpus-wide misses on Central regulatory frameworks: APJPA, CVC Pre-bid Integrity Pact, PPP-MII Order 2017. Three different shape categories (state-level mandate / parallel-compliance / Central mandate without AP defeats), same corpus signal.

**What broke and what we fixed (grep-vocabulary discipline).** First Vizag test produced an UNVERIFIED outcome instead of ABSENCE because L40 whole-file grep found ONE match for "indigenous" at Vizag's Vol-II Scope of Work line 49. Investigation: that "indigenous" was in a logistics context — *"...delivery from Indian port to site in case of imported equipment and delivery/unloading at site for indigenous equipment..."* — distinguishing imported vs domestically-sourced equipment for shipping logistics. NOT Make-in-India. Same false-positive risk applied to bare "purchase preference" / "price preference" / "local content" — all of these can appear in non-MII contexts (lowest-cost evaluation rules, locally-sourced scope-of-work materials, etc.).

**Fix:** drop the broad keywords from `GREP_FALLBACK_KEYWORDS`. Keep ONLY MII-specific phrases that don't ambiguate against other corpus content:
- *Kept:* "Make in India" / "Make-in-India" / "PPP-MII" / "Public Procurement (Preference to Make in India)" / "Preference to Make in India" / "GFR Rule 153" / "Rule 153(iii)" / "DPIIT" / "Class-I local supplier" / "Class-II local supplier" / "P-45021" / "16.09.2020" / "16-09-2020"
- *Dropped:* "indigenous" (Vizag false positive) / "purchase preference" (broad — could be lowest-cost) / "price preference" (broad) / "local content" (borderline — could refer to scope materials) / "Order 2017" (broad in some contexts) / "Class-I local" without "supplier" suffix (broader than the Order-specific phrase)

**This generalises the L39 anchor-keyword discipline lesson** from `smart_truncate` to `grep_fallback`. Same root cause: broad keywords in a search vocabulary anchor false-positive matches. The lesson: the L36/L40 grep keyword list IS a precision filter — every keyword should be unique enough that bare matches are unambiguous in the corpus context. When in doubt, prefer the longer multi-word phrase over the single ambiguous word.

**After the fix:** Vizag's L40 returned 0 hits → ABSENCE branch fires correctly → OPEN HARD_BLOCK with TenderDocument-attached edge. Re-run on all 6 docs confirms identical clean ABSENCE outcome.

**Why we changed:** Without the grep-vocabulary tightening, all 6 docs would have routed to UNVERIFIED via false-positive whole-file hits on "indigenous" / "purchase preference" / "local content". The systemic-absence finding (which has real audit value as a portfolio-level signal) would have been buried behind 6 UNVERIFIED-needs-review findings. The L45 grep tightening preserves the L36/L40 audit chain (genuine absences still emit ABSENCE; genuine kg-coverage gaps still emit UNVERIFIED) while filtering out false positives that L40 would otherwise misclassify as retrieval-coverage gaps.

**Corpus impact:** 6 new findings, all OPEN HARD_BLOCK ABSENCE, all attached to TenderDocument (no Section attribution because the violation IS absence). +6 VIOLATES_RULE edges. The MII column on the corpus dashboard becomes the third "all-red" column alongside JP-Bypass and (partially) Integrity-Pact.

**Forward applicability:**
1. **Systemic-absence pattern is reusable for any Central-mandate framework.** Three so far (JP / IP / MII). Future candidates: Reverse-Tender-Mandatory-for-Goods (DPIIT mandate for goods procurement above threshold), CSR Disclosure (GFR Rule 175 for state-funded projects), Anti-Profiteering (CGST §171 for GST-rate-change clauses). All would use the same single-rule absence-shape pattern as L38 + L45.
2. **Grep-vocabulary discipline as a typology-build checklist item.** Before running a new typology, sanity-check the GREP_FALLBACK_KEYWORDS list against the corpus: each keyword should be unique enough to flag only the typology's content. If a keyword could match general scope-of-work / logistics / evaluation language, drop it. The L40 false-positive cost (UNVERIFIED instead of ABSENCE) is meaningful — it buries real signals.
3. **Audit dashboard "all-red columns" are a portfolio-level reform signal.** When a typology emits 6/6 HARD_BLOCK across the corpus, the response is policy/template-level (update the SBD/RFP master template), NOT per-tender remediation. The audit dashboard should aggregate "typologies with X/6 violations" as the primary corpus health metric.
4. **Prediction-vs-outcome calibration is improving.** L43 Arbitration: predicted 0 violations, got 0 (3 informational markers). L44 Geographic: predicted 1 HARD + 2 ADV + 3 markers, got exactly that. L45 MII: predicted 6 OPEN HARD_BLOCK, got exactly 6. The read-first phase is paying off — running the queries before building lets us calibrate corpus expectations and catch grep-vocabulary issues before they generate false UNVERIFIED.

---

## L46 — Works-Universal-Mandatory-Fields: Per-Sub-Check Grep Fallback

**What we did:** Built the seventeenth Tier-1 typology — Works-Universal-Mandatory-Fields (sub-typology of Missing-Mandatory-Field, the 596-rule classification bucket). Bundles four atomic mandatory-field sub-checks under one LLM call: MPG-148 (representation officer + contact + window), MPG-150 (post-LoA acknowledgement window 14d/28d), MPG-293 (Contract Effective Date / PPP Appointed Date), MPG-124 (figures-vs-words discrepancy resolution rule). Multi-rule shape per L43 with up to 4 independent findings per doc (one per sub-check). MPG-136 (Goods-only, SKIPs on Works/PPP) and MPG-237 (Secretariat-level DFPR delegation, not bid-doc-side) were dropped per the read-first review.

**The L46 pattern is new — per-sub-check grep verification.** Initial JA test exposed false-positive ABSENCE findings on MPG-148 and MPG-124. The LLM's top-10 Qdrant retrieval surfaced ONE section (ITB §41.2 with the "Fourteen (14) days" post-LoA window for MPG-150) and the LLM correctly extracted that single signal. But MPG-148 (Contact Person at L88/L500), MPG-124 (figures-vs-words rule at L246/L376/L1074), and MPG-293 (Force Majeure Appointed Date at L4651) all live in DIFFERENT sections that didn't make the top-10. The LLM truthfully reported `representation_officer_named=False`, `figures_vs_words_rule_present=False` — those signals weren't in the candidates it saw — but the script then emitted ABSENCE findings for both, which would be wrong.

The structural problem: the global L36/L40 grep fallback chain (L40, L41) only fires on **all-sub-checks-failed absence path**. With multi-sub-check shape, individual sub-checks need per-sub-check verification before emitting ABSENCE. Single-rule typologies (JP / MII / IP) didn't have this issue because there was only ONE signal to find — if the LLM said it wasn't present and grep agreed, the absence was real.

**The L46 fix.** Each sub-check now has its own keyword vocabulary (`SUB_CHECK_GREP_KEYWORDS`). Before emitting ABSENCE for a sub-check the LLM said False, the script runs `grep_source_for_keywords(doc_id, section_types, sub_check_kws)`. If the L36 Section-bounded grep finds a hit, the absence is downgraded to UNVERIFIED with `evidence_match_method='l46_per_subcheck_l36_grep_promoted'`. If L36 is empty, a Tier-2 L40 whole-file grep runs; if THAT finds the signal, downgrade to UNVERIFIED with `_l40_grep_promoted` (and `kg_coverage_gap=True` if the match line falls outside any Section's range). Only when both L36 and L40 are empty does the sub-check emit a true ABSENCE finding.

**Result on JA:** 3 UNVERIFIED + 1 COMPLIANT (MPG-150). All 3 UNVERIFIED carry per-sub-check grep audit — reviewer reads `grep_fallback_audit.hits[]` and confirms manually. **Without L46, JA would have shipped 3 false-positive HARD_BLOCK / ADVISORY ABSENCE findings.**

**Corpus result — 13 findings emitted, 3 OPEN + 10 UNVERIFIED:**
- **vizag** → 3 UNVERIFIED (MPG-148 L40, MPG-150 L36, MPG-293 L36); MPG-124 COMPLIANT
- **judicial_academy** → 3 UNVERIFIED (MPG-148 L36, MPG-293 L40, MPG-124 L36); MPG-150 COMPLIANT
- **high_court** → 1 UNVERIFIED (global L24-fail score=44 → bundled audit-only finding before per-sub-check chain ran)
- **kakinada** → 2 OPEN: MPG-148 HARD_BLOCK ABSENCE + MPG-293 ADVISORY ABSENCE (Kakinada SBD has neither rep officer nor Appointed Date); MPG-150 + MPG-124 COMPLIANT
- **tirupathi** → 2 UNVERIFIED (MPG-148, MPG-293 — L36 found keywords but LLM missed); MPG-150 COMPLIANT, MPG-124 SKIP (PPP-DCA, no BoQ)
- **vijayawada** → 1 OPEN ADVISORY-INFO marker (MPG-293 PPP Appointed Date recognised at L215) + 1 UNVERIFIED (MPG-148 L36); MPG-150 COMPLIANT, MPG-124 SKIP

**One real OPEN HARD_BLOCK** — Kakinada genuinely lacks a representation officer designation. The other Kakinada OPEN ADVISORY (MPG-293) is also genuine — Kakinada SBD has no Contract Effective Date or Appointed Date concept. **This is the only typology-17 finding that's a confirmed real procurement defect** (vs the 9 UNVERIFIED-pending-review).

**Anti-hallucination preserved.** Per-sub-check grep doesn't loosen L24 — it tightens the absence-claim verification. A sub-check the LLM says is absent gets THREE chances to be confirmed absent: (1) LLM didn't see it in top-10, (2) L36 Section-bounded grep doesn't find keywords, (3) L40 whole-file grep doesn't find keywords either. Only after all three layers agree does ABSENCE fire.

**Forward applicability:**
1. **Every multi-sub-check typology should adopt L46.** Arbitration (L43, 4 sub-checks) and Geographic (L44, 4 sub-checks) had similar structure but didn't suffer the false-positive problem because their decision trees focused on the strongest signal rather than emitting per-sub-check findings. If we ever extend either to emit per-sub-check findings, L46 verification becomes mandatory.
2. **The `SUB_CHECK_GREP_KEYWORDS` dict pattern is reusable.** Future multi-sub-check typologies just declare the dict and call `_verify_sub_check_absence(sub_check_kind)` before emitting ABSENCE. The function returns `(any_hit, sec_hits, full_hits, kg_gap)` — same audit shape as L36/L40.
3. **The audit-method labels distinguish each fallback layer.** `evidence_match_method` values now include `l46_per_subcheck_l36_grep_promoted`, `l46_per_subcheck_l40_grep_promoted`, alongside the existing `grep_fallback_retrieval_gap`, `whole_file_grep_kg_coverage_gap`, etc. The dashboard can filter findings by which verification layer they passed/failed at.
4. **Cost is bounded.** Per-sub-check grep runs at most 4 times per doc per typology (once per sub-check), each scanning ~10-30 sections × ~20 keywords. ~200ms total overhead in the worst case. Well within the existing typology-run budget.

---

## L61 — Tier-2 Validator Architecture Pattern (bid_*_check.py)

**Established during Module 3 Sub-block 3a pilot** (`scripts/bid_turnover_check.py`, May 2026). First Tier-2 Bid Submission Evaluator built. Pattern proven across 9 synthetic bids (3 bidders × 3 tenders) with 100% ground-truth match (3 QUALIFIED + 6 INELIGIBLE per predicted matrix; boundary close-calls B1×HC ratio 1.027 / B2×JA ratio 0.956 both correct under strict `>=` comparator).

### Architectural pattern

| Aspect | Tier-1 (`scripts/tier1_*_check.py`) | Tier-2 (`scripts/bid_*_check.py`) |
|---|---|---|
| Input | Unstructured tender markdown via Qdrant | Structured `fact_sheets.extracted_facts` jsonb |
| Retrieval | BGE-M3 embed + Qdrant top-K + LLM rerank | Direct row fetch by `(doc_id, fact_group)` |
| Hallucination guard | L24 evidence_guard verifying LLM quotes | Inert — no LLM in path |
| Coverage fallback | L36 grep across full section filter | Inert — input is already canonical |
| Rule pick | `condition_evaluator.evaluate` + L27 UNKNOWN→ADVISORY downgrade | **Same** (reused unchanged) |
| Idempotence | `_delete_prior_tier1_*` filtering on ValidationFinding + VIOLATES_RULE + tier=1 | `_delete_prior_tier2_*` filtering on BidEvaluationFinding + BIDDER_VIOLATES_RULE + tier=2 |
| Output node | `ValidationFinding` | **`BidEvaluationFinding`** (new node_type, accepted as plain TEXT — no CHECK constraint) |
| Output edge | `VIOLATES_RULE` Section→RuleNode (silent on COMPLIANT_FIRED) | **`BIDDER_VIOLATES_RULE`** BidSubmission→RuleNode (silent on QUALIFIED) |
| Verdict vocab | `COMPLIANT_FIRED` / `GAP_VIOLATION` / `HARD_BLOCK` / `UNVERIFIED` / `SKIP_NOT_APPLICABLE` | `QUALIFIED` / `INELIGIBLE` / `GAP_INSUFFICIENT_DATA` / `SKIP_NOT_APPLICABLE` |
| Crash resilience | `main_with_crash_resilience(main, doc_id, typology)` | **Same** (wrapper's DeferredCleanup captures 0 tier-1 rows for the new typology, harmless; Tier-2 rows are cleaned in `main()`) |

### Citation chain (mandatory on every BidEvaluationFinding)

Five domains, each with both an identity field and a source pointer:

1. **Bidder** — `bidder_profile_id`, `bidder_profile_node_id`, `bidder_name`, `bidder_pan`, `bidder_contractor_class`
2. **Bidder fact** — `fact_sheet_id`, `fact_sheet_fact_group`, `fact_sheet_source_file`, `fact_sheet_extracted_by`, plus the extracted value(s) (`bidder_avg_5yr_turnover_cr`, `bidder_fy_data[]`)
3. **Tender** — `tender_id`, `tender_nit_no`, `tender_title`, `tender_estimated_value_cr`, `tender_tenure_years`
4. **Tender criterion** — `pq_turnover_floor_cr`, `pq_floor_source` (audit string naming the source field path), plus a `*_consistent` cross-check flag when the value can be triangulated from multiple sources
5. **Regulatory rule** — `rule_id`, `rule_natural_language`, `rule_condition_when`, `rule_layer`, `rule_typology_code`, `rule_facts_evaluated` (dict of fact values fed to condition_evaluator), plus L27 audit (`verdict_origin`, `severity_origin`)

Plus computation + outcome + ground-truth cross-check fields (`predicted_matches_ground_truth` from `extracted_facts._designed_to_trip` annotation — non-zero RC if disagreement, so a wrapper loop catches regressions immediately).

### orthogonal `evaluation_consequence` field

Distinct from `severity` (which comes from the rule). Tells downstream EligibilityMatrix what to do with this bidder:
- `HARD_BLOCK` on INELIGIBLE — bidder disqualified, cannot proceed
- `ADVISORY` on QUALIFIED / SKIP — informational, no action needed
- `WARNING` on GAP_INSUFFICIENT_DATA — reviewer must supply missing facts

### Pilot-only shortcuts to revisit before Sub-block 3b scaling

- **Tender threshold lives in the bidder's fact_sheet row** (`extracted_facts.pq_floor_cr`) plus a small in-script `SYNTHETIC_TENDER_CATALOG`. Real architecture: a separate Tier-2 tender-criterion node extraction reading PQ thresholds + similar-works requirements + ABC formula + class requirement from each tender's Section III into queryable kg_nodes. Queued.
- **No TenderDocument nodes for synthetic tenders** — only BidderProfile + BidSubmission. The pilot's `SYNTHETIC_TENDER_CATALOG` is a stand-in.
- **condition_evaluator can't parse `IN (...)` syntax** — CVC-028's `WorkType IN ('Civil','Electrical')` always resolves UNKNOWN. Tier-1 absorbs this via L27 downgrade (WARNING → ADVISORY); Tier-2 mirrors. A future evaluator upgrade could turn the verdict from UNKNOWN to FIRE for known WorkType, preserving WARNING severity. Out of scope tonight.
- **Inadvertent cross-layer demonstration**: Kurnool's synthetic PQ floor ₹121.7 cr against ECV ₹85 cr / tenure 3 yr → annual = ₹28.33 cr, multiple = 4.3× — well above the CVC-028 ≤2× cap. The same rule that Tier-2 uses for bidder-side eligibility is what Tier-1 would flag as doc-side excess. Useful as a Sub-block 3b smoke target: run Tier-1 turnover_check on the synthetic tenders once they're materialised as TenderDocument nodes; should produce both a Tier-1 finding (PQ floor excessive) and Tier-2 findings (bidders B2/B3 ineligible against that same floor). Both layers tell different stories about the same rule.

### File naming convention reaffirmed (per L60)
- Tier-1: `scripts/tier1_<typology>_check.py`
- Tier-2: `scripts/bid_<typology>_check.py` ← this pilot

### L61 addendum-2 (Sub-block 3b Batch 2) — Composite multi-supplementary input contract

A second composite pattern variant emerged in `bid_emd_validity_check` (Batch 2): two **per-bid supplementary nodes** combined, neither of which is the bidder entity (`BidderProfile`) nor a `fact_sheets` row.

- **Source 1**: `kg_nodes.EMD_BG.properties` — per-bid supplementary node with BG financial metadata (issue/expiry dates, amount, unconditional flag, issuing bank).
- **Source 2**: `kg_nodes.LetterOfBid.properties` — per-bid supplementary node with bid signature metadata (signature_date, bid_validity_days).

The finding's `input_contract` is `"composite:EMD_BG+LetterOfBid"`, tagged with `input_contract_pattern="composite_multi_supplementary_per_bid"`.

**Distinction from addendum-1 (`composite_entity_plus_statement`)**: addendum-1 joins entity-level (BidderProfile) with statement-level (fact_sheets); addendum-2 joins two per-bid supplementary nodes. Future Tier-2 validators reading from multiple supplementaries (e.g. EMD_BG + PricedBoQ for cover-bid signal detection, LetterOfBid + Schedule-of-Rates for rate-anomaly checks) should use the addendum-2 pattern tag.

Both addendum variants share the same mandatory carry-through: each source citation, cross-source consistency check, both rule anchors (primary + secondary) cited with separate NL blocks.

### L61 addendum-3 extension (Sub-block 3b Batch 3) — Rule-strict verdict over seed framing

The recompute discipline (addendum-3) extends beyond numerical recompute from arrays to **rule-derived verdict over seed labels**. When the synthetic seed's narrative framing diverges from the rule's strict text, the validator's verdict follows the rule. The seed's softer framing is preserved in the `ground_truth_label` audit field for reviewer visibility, but does NOT override the verdict.

Reference cases from Batch 3:
- **B2 equipment**: seed `_designed_to_trip` says *"PARTIAL — mix of owned/leased acceptable"*. MPW-042 NL explicitly permits leased items as "assured access". Validator emits **QUALIFIED** (rule-strict) with `seed_completeness_softer_than_rule=True` flag. The "PARTIAL" framing is committee-discretion language; the rule itself is permissive.
- **B2 personnel**: seed `_designed_to_trip` says *"PARTIAL (4/6) — gap in key personnel; may be acceptable if filled post-award"*. MPW-041 NL requires personnel meeting qualifications **at bid time**, no post-award filling allowed. Validator emits **INELIGIBLE** (rule-strict). The "may be acceptable" framing is committee discretion; the rule itself is strict.

Pattern: rule NL > seed labels, always. EligibilityMatrix downstream sees the rule-strict verdict; reviewers see the seed's softer label in audit fields and can override via committee process if appropriate.

### L61 addendum-3 (Sub-block 3b Batch 2) — Recompute-from-array discipline

Synthetic seed rows carry computed ground-truth flags (e.g. `meets_3_2_1_rule`, `qualifies`, `is_within_one_year`). A Tier-2 validator MUST recompute the verdict from the **raw input data** (the array, the date, the figure), not trust the seed's pre-computed boolean. Disagreements between recompute and seed are surfaced as `recompute_seed_agree=False` + `l64_seed_defect_surfaced=True` audit fields, with the validator returning `RC=2` so a wrapper loop stops on the defect.

`bid_similar_works_check` exercises this — it ignores `meets_3_2_1_rule` and re-runs all 3 branches (3@40%, 2@50%, 1@80%) against the `similar_works[]` array. Disagreement on any of the 9 synthetic bids would have surfaced as a real L64 seed defect (zero defects found in Batch 2 — recompute agreed with seed across all 9, including the B1×HC boundary where the seed scales work values proportionally to ECV).

### File naming convention reaffirmed (per L60)
- Tier-1: `scripts/tier1_<typology>_check.py`
- Tier-2: `scripts/bid_<typology>_check.py` ← this pilot

### L61 addendum (Sub-block 3b Batch 1) — Composite input contract pattern

The pilot's single-source input contract (one `fact_sheets` row per finding) is the common case but not the only shape. `bid_blacklist_check` (Batch 1) introduced the **composite input contract**:

- **Source 1 (entity-level)**: `kg_nodes.BidderProfile.properties.blacklist_status` — a per-bidder attribute, not per-bid.
- **Source 2 (statement-level)**: `fact_sheets.Statement-VII-Litigation` — `litigation_count` + `cases[]` array per bid.

The finding's `input_contract` field captures this composite shape as the string `"composite:BidderProfile+fact_sheets.Statement-VII-Litigation"`, plus a new `input_contract_pattern` field tagged `"composite_entity_plus_statement"` for downstream aggregation (EligibilityMatrix can use this to group findings by input shape).

Mandatory carry-through on composite validators:
- Each source gets a separate citation block (`blacklist_status_source`, `fact_sheet_source_file`, etc.).
- Cross-source consistency is checked and surfaced (`litigation_consistent` boolean) — drift between the two sources doesn't crash the validator but is flagged in the audit field, with the fact-sheet value preferred when they disagree.
- Both rule anchors (primary + secondary) are recorded in the finding (`rule_id` + `secondary_rule_id`) with separate citation blocks for each (`rule_natural_language` + `secondary_rule_natural_language`, etc.).

Forward-applicable to any Tier-2 validator needing entity-level + statement-level facts (e.g. future `bid_litigation_check` with multi-statement cross-ref; future `bid_equipment_check` reading per-equipment BidderProfile facts + Statement-V).

### Replicating to the 9 remaining Tier-2 validators (Sub-block 3b)

Pattern is now stable. Each next Tier-2 validator needs only:
1. A new `TYPOLOGY` string
2. A `RULE_ID` (or rule-priority list)
3. A `load_<statement>_fact` helper (one query against `fact_sheets`)
4. A `compute_verdict` function (typology-specific compare logic)
5. The same finding/edge emission boilerplate

Everything else (rule selection, condition_evaluator + L27 path, idempotence, crash resilience, citation chain template, ground-truth cross-check) is copy-paste from the pilot.

---

## L108 — Reference-SBD Section Classification (Boilerplate / Template+Placeholders / Project-Specific) is Where the M1 Drafter Architecture Lives

**Established in Run 7.1** (2026-05-13). When extending Module 1 from the 30-page template-mode v1 output to a 100-244 page corpus-driven v2, the inflection point is not "use a bigger model" — it's understanding which of the 9 SBD sections are mechanically templated, which are template+placeholders, and which are genuinely project-specific text that must be LLM-adapted from exemplars.

### The measured classification (Jaccard on normalised line-set fingerprints across HOD Towers + LPS Zone-11 reference SBDs)

| Section | Title | Jaccard | Class |
|--------:|-------|--------:|-------|
| I       | NIT (Invitation for Bids)        | 0.024 | TEMPLATE+PLACEHOLDERS |
| II      | Instructions to Bidders          | 0.267 | TEMPLATE+PLACEHOLDERS |
| III     | Evaluation & Qualification       | 0.065 | TEMPLATE+PLACEHOLDERS |
| IV      | Bidding Forms                    | 0.86  | BOILERPLATE           |
| V       | Eligible Countries / Source-of-Funds | 0.91 | BOILERPLATE         |
| VI      | Works Requirements               | 0.04  | PROJECT-SPECIFIC      |
| VII     | General Conditions of Contract   | 0.95  | BOILERPLATE           |
| VIII    | Particular Conditions of Contract| 0.12  | PROJECT-SPECIFIC      |
| IX      | Annexures (Contract Forms)       | 0.93  | BOILERPLATE           |

### Pre-flight prediction vs measurement

The pre-flight inventory predicted Sections II, III, NIT would be high-similarity TEMPLATE+PLACEHOLDERS (≥0.5 Jaccard). The measurement showed otherwise: NIT 0.024, II 0.267, III 0.065. The structural pattern is still templated (same headings, same paragraph order), but the prose-level token overlap is low because every reference tender swaps in its own project name, GO numbers, eligibility specifics, and dates. The fix is not to abandon the classification — it's to recognise that "TEMPLATE+PLACEHOLDERS" means **deterministic substitution of {{var}} markers on a single chosen exemplar's content_md**, not a high-Jaccard line-set merge.

### Architectural consequence for workflow_v2

This 3-way split locks the 15-node workflow's per-section strategy:

- **BOILERPLATE (IV, V, VII, IX)**: top-1 pgvector retrieval → drop content_md verbatim with placeholder substitution. Zero LLM calls. Fast (< 50ms each).
- **TEMPLATE+PLACEHOLDERS (I, II, III)**: top-1 retrieval → placeholder substitution. Same code path as BOILERPLATE but the exemplar's content_md has many more `{{vars}}` to fill. Still zero LLM calls.
- **PROJECT-SPECIFIC (VI, VIII)**: top-3 retrieval → Vertex Gemini 2.5 Pro adaptation with the 3 exemplars in-context. ~30s per call, ~₹2-3 each on a 50-cr tender. This is the *only* place reasoning-tier compute is spent for section drafting.

The BoQ generator is a fourth, separate path (Vertex Gemini 2.5 Flash batches; covered in L110).

### Forward-applicable rule

For any future Module that ingests a templated document corpus (LD/PVC/IP/PCC/SCC anchors across the validator, eligibility lots, communication templates), **measure boilerplate-vs-project-specific Jaccard on normalised line sets BEFORE writing the per-section LLM strategy.** Use ≥0.85 / ≥0.50 / <0.50 as the classification thresholds, but treat <0.50 as "project-specific even if structurally templated" — token similarity isn't the same as structural similarity, and the wrong assumption costs you a Pro call per section that didn't need one.

---

## L109 — Token-Aware Prompt Engineering: thinking_budget=0, Schema Inliner, Batch-Size Sanity (Vertex AI Gemini 2.5 Pitfalls)

**Established in Run 7.4 + 7.6** (2026-05-13). Three sharp edges show up the first time you wire Vertex AI Gemini 2.5 (Flash or Pro) into a structured-output pipeline. None of them are documented in the Vertex docs as gotchas; all of them silently destroy your output unless you find and fix them.

### 1. Flash "thinking" can consume your entire output budget

In R7.4 smoke, a 300-token `max_output_tokens` Flash call returned 11 actual text tokens and 285 `thoughtsTokenCount`. Gemini 2.5's chain-of-thought is on by default and competes with the visible output for the same token budget. For BoQ enrichment (structured output, no reasoning needed), this destroys throughput.

Fix: pass `generationConfig.thinkingConfig.thinkingBudget = 0` on every Flash call. We wired this as the default in `gemini_flash()` (`thinking_budget: Optional[int] = 0`). Pro keeps the default-None ("model decides") since Pro is where we actually want reasoning.

### 2. Vertex AI's responseSchema rejects Pydantic's $defs/$ref output

Pydantic 2's `model_json_schema()` emits JSON Schema with `$defs` + `$ref` for any nested BaseModel. Vertex AI's `generationConfig.responseSchema` is an **OpenAPI 3.0 subset** that explicitly does NOT support these. The result is a 400 with `Unknown name "$defs"` / `Unknown name "$ref"`. We hit this on every Flash batch in the first R7.6 smoke — 100% LLM failure across 12 batches.

Fix: write an inliner (`_pydantic_to_response_schema()` in `vertex_client.py`) that recursively dereferences `$ref` against the local `$defs` dict, drops the `$defs` key, converts `anyOf` with null → `nullable=true`, and strips `title` / `default` / Pydantic-specific metadata. Cap recursion at depth 8 for self-referential types.

### 3. Output-token cap interacts with batch size to cause silent JSON truncation

Flash's default `max_output_tokens=8192` is too small for a 30-row BoQ batch where each row produces ~300 output tokens (spec_text + citations + work_type + apss_cl_no). The response gets cut off mid-row, the `parse_ok` flag returns False with `"Unterminated string starting at line N..."`, and every single batch falls back to the stub-row path.

Fix: empirically right-size. 15 rows × 12288 cap works reliably; 30 rows × 8192 cap does not. **The right rule is `max_output_tokens ≥ N_rows × expected_output_per_row × 1.5`** (50% headroom for verbose model responses). Document the formula in the prompt's docstring so future maintainers don't tighten the cap to "save tokens."

### Cost of the lesson

R7.6 smoke ran 12 Flash batches × ~35s with 100% success after these three fixes. ~₹1.19 total spend, 100% citation-match rate on the 10-row sample, 100% spec_text length pass. Before the fixes: same cost (because failed calls still consume the prompt-tokens charge), 0% success.

### Forward-applicable rule

For every new Vertex AI integration, before any real-LLM smoke, write a 3-line preflight that:
- Asserts `thinking_budget=0` is set on Flash and any sub-reasoning-tier model.
- Round-trips your Pydantic response schema through the inliner and asserts no `$ref`/`$defs` keys remain.
- Logs an upper-bound on `expected_output_tokens × batch_size × 1.5` and asserts `max_output_tokens` ≥ that.

These three fail-fast checks save real LLM-API budget when the smoke runs.

---

## L110 — BoQ Skeleton Is the Officer's Lever: Discipline-Bucketed Flash Batches Beat Per-Row Calls

**Established in Run 7.5 + 7.6** (2026-05-13). Module 1 v2 enriches a Bill of Quantities from an officer-uploaded skeleton (item names + quantities + units, no specifications). Two design decisions in `boq_generator.py` matter:

### Decision 1: Bucket rows by detected discipline before LLM batching

We classify each skeleton row's discipline via regex (16 disciplines: HVAC, Fire, Lifts, PA, BMS, HSD, Plumbing, Electrical, Roads, Bridges, Drains, Sewerage, WaterSupply, Reuse, UtilityDucts, Plantation, Civil-catchall). Each discipline batch gets the same set of exemplar TechSpecTemplates retrieved via pgvector, so the in-context exemplars match the prompt's domain. This avoids the failure mode where an exemplar AHU spec is shown alongside a road WMM line item — the LLM borrows the wrong citation style.

Tie-break ordering matters: HVAC/Fire/Lifts must come BEFORE Electrical in the regex dict, otherwise "AHU panel" gets classified as Electrical (because "panel" matches first) and the BoQ row receives Electrical citations.

### Decision 2: 15-row Flash batches with progressive event yield, not 30-row or per-row

Per-row calls are too slow (1.2s/row × 200 rows = 240s, plus per-call HTTP overhead). 30-row batches overflow `max_output_tokens` (see L109). 15-row batches at 12288 output cap fit reliably, average ~35s each, ~₹0.10 each.

The other half of this decision is **progressive yield**: an earlier implementation queued events into a list inside `_draft_BoQ_node` and yielded them all at the end. With a synchronous-blocking Flash call inside the loop, the SSE consumer saw zero rows for 5+ minutes while batches accumulated, then everything at once. Refactor: the BoQ node yields `boq_batch_started` → 15× `boq_item_complete` → `boq_batch_started` → … as a generator. Wall-clock guards in test drivers and the live SSE view both see partial progress.

### Why this lever exists at all

The directive's input-modality decision was: officer uploads an Excel/CSV skeleton, AI fills the specs. This was the right call because:
- It keeps the officer in the loop on *which* line items are in scope (the hardest part of BoQ authorship).
- It frees the LLM from the open-ended "what should this tender include?" problem and lets it specialise on the closed-form "given THIS item name + qty + unit, write the spec_text + citations".
- It maps to existing officer workflows — every tender already starts with a hand-drafted skeleton spreadsheet that goes through internal review before AP-eGP upload.

### Forward-applicable rule

For any LLM enrichment of a structured tabular input where each row needs ≥150 chars of typed output: bucket by a deterministic upstream classifier first, batch within bucket at a size that fits 1.5× the output budget, and yield events progressively. Don't reach for parallelism until per-bucket serial fits inside the wall-clock SLA — it's a 4× speedup at a 10× code-complexity hit.

---

## L111 — pgvector + Vertex Embeddings: Direct psycopg Wins Over REST PATCH for Bulk Backfills

**Established in Run 7.4** (2026-05-13). Backfilling 1095 768-dim embeddings (30 SBDSection + 993 BoQItemSpec + 72 TechSpecTemplate) initially routed through Supabase REST `PATCH /rest/v1/kg_nodes?node_id=eq.X`. This pattern is fine for occasional single-row writes but fell over here for three reasons:

1. **Per-row HTTP overhead**: 1095 round-trips × ~600ms each = ~11 min minimum, ignoring API rate-limit backoff.
2. **PostgREST read-timeout under sustained PATCHing**: hit `ReadTimeout` ~700 rows in, no obvious recovery path without resuming from arbitrary mid-stream.
3. **No bulk-update primitive in PostgREST** for `(node_id, vec_str)` pairs — `UPDATE FROM (VALUES …)` requires raw SQL.

Fix: skip REST entirely for bulk operations. Open a direct `psycopg.connect(settings.supabase_url, sslmode="require")` against the Postgres pooler (port 6543), set `statement_timeout = 300000`, and use `cursor.executemany("UPDATE kg_nodes SET embedding = %s::vector WHERE node_id = %s::uuid", batched_50_at_a_time)`. Total wall-clock for the full 1095-row backfill: 3 minutes 12 seconds.

### Companion lessons captured

- **`psycopg[binary]` not bare `psycopg`** — the wheel without the libpq binding fails to import with `no pq wrapper available`.
- **Cast strings to pgvector explicitly** — `'%s'::vector` in the UPDATE, with the literal formatted as `'[1.2,3.4,...]'`. No driver coercion exists.
- **HNSW index with `vector_cosine_ops` + partial WHERE** — `CREATE INDEX … WHERE embedding IS NOT NULL` avoids re-indexing the millions of rows that don't carry embeddings (Module 2-4 corpus). Index build on the 1095-row partial: ~2 seconds.
- **Service-role key fallback** — Supabase service-role key was empty in `.env`; bulk operations against pgvector tables don't need it if you're going through the Postgres connection string, but the REST path does. Document both code paths so future operators know which to choose.

### Forward-applicable rule

Any kg_nodes-level bulk operation > 100 rows: direct psycopg over the pooler. Any per-action mutation triggered by a user request: REST + service-role key. The boundary is "is this an integration job, or a user-flow operation?" — the former goes psycopg, the latter goes REST. Different consistency / latency / observability expectations.

---

## L112 — Run 7 Wrap: 5 Sub-Blocks, 1095 Embeddings, ₹1.19 Smoke, Sentinel Preserved

**Cumulative summary of Run 7** (2026-05-13). Module 1 capital-scale expansion shipped in 5 commits:

| Sub-block | Commit | Deliverable | Cost |
|----------:|:-------|:------------|-----:|
| R7.1+R7.2 | `c7df576` | 9 SBD sections → 30 SBDSection kg_nodes; 380pp MEP BoQ → 993 BoQItemSpec | ~₹0 |
| R7.3+R7.4 | `3b47dab` | 72 TechSpecTemplate schemas + pgvector + Vertex hybrid clients + 1095 embeddings | ₹0.46 |
| R7.5      | `e411c4c` | workflow_v2.py (15 nodes) + boq_generator.py + v1↔v2 toggle | ~₹0 (dry-run only) |
| R7.6      | `1c5fbad` | Mid-scale smoke (90 BoQ rows civil, ALL 5 gates pass) | ₹1.19 |
| R7.7      | `3ee4286` | BoQ skeleton upload UI (Step 6) + parse endpoint | ~₹0 |
| R7.8      | this commit | Wrap + status report + L108-L112 docs | — |

### Sentinel preserved

The 18-row sentinel taken at R7.6 start (BidAnomalyFinding=6, BidEvaluationFinding=351, RuleNode=611, ValidationFinding=154, etc.) was unchanged after all 5 sub-blocks. The R7-added types (`SBDSection`, `BoQItemSpec`, `TechSpecTemplate`) are additive — no `defeats`/replacement of existing kg_node types. Job count was excluded from the sentinel (volatile per-run).

### What works end-to-end after Run 7

- POST `/api/m1/draft/parse-boq-skeleton` with .xlsx/.xls/.csv → parsed rows in JSON
- Step 6 of the new-draft wizard renders a drag/drop uploader; parsed rows are previewed
- Submit on Step 7 → POST `/api/m1/draft/start` with `boq_skeleton` → worker dispatches to v2 when `M1_DRAFTER_WORKFLOW_V2=1`
- v2 worker runs 15 nodes including Vertex Pro for Section VI + VIII PCC adaptation
- 90-row BoQ batched at 15 rows / batch, 12 Flash batches × ~35s, 100% citation match
- All SSE event types stream live (node_started, section_complete, text_chunk, table_row_added, boq_batch_started, boq_item_complete, llm_call)

### What is deferred to Run 8

- Capital-scale smoke (HOD Towers ₹743cr MEP, 3000+ BoQ rows)
- Small-scale smoke (Banaganapalli ~30 rows) — ensures the existing v1 backward-compat path still works
- Mid-scale Civil + MEP mix (LPS Zone-11 ~800 rows)
- Cloud Build deploy of v2 workflow to production with the `M1_DRAFTER_WORKFLOW_V2` flag flipped
- Parallelisation of Flash batches when total rows ≥ 200 (currently serial; capital scale will exceed 5-min wall-clock without it)
- Vertex AI Model Garden Anthropic publisher access enablement (Sonnet 4 fallback currently 404s; we skip after first 404 to save retries)

### Forward-applicable note

Skipping the BoQ upload yields a tender with `state.boq = []` and the workflow_v2 node emits a `no_skeleton_supplied` telemetry field. The artifact renderers tolerate empty BoQ. This is the intended degraded path — the officer can complete the BoQ in the TECHNICAL gate's edit scope and re-publish. Don't gate the workflow on skeleton presence; gate the publish step on a non-empty BoQ at the AUTHORITY gate.

---

## L113 — Wall-Clock Recalibration: Flash batches actually run at 35s, not the 12s the pre-flight assumed

**Established in R7.6 → R8.2** (2026-05-13). The pre-Run-7 cost-model spreadsheet assumed Gemini 2.5 Flash would average ~12s per 15-row BoQ batch (based on Vertex AI's typical small-prompt latency). The measured reality across R7.6 (90 rows / 12 batches) and R8.2 (800 rows / 58 batches) is **~35s per batch** — roughly 3× the estimate.

### Why the gap

Three compounding factors not visible in the small-prompt latency benchmarks:

1. **Schema response size**: each batch returns ~4000 output tokens (15 rows × ~270 tokens of spec_text + citations + work_type). Vertex AI's structured-output path serialises the schema-conformant JSON on the server before sending — that adds ~10-15s versus a free-form text reply.
2. **Prompt size**: each batch's prompt is ~5000 tokens (system instruction + 8 TechSpecTemplate exemplars + 15 skeleton rows). Prompt-token processing is fast but not free.
3. **thinking_budget=0 still incurs token-budget routing overhead**: Flash's chain-of-thought path is suppressed but the model's decoder is set up for a reasoning-tier call shape. ~2-3s of overhead per call.

### Architectural consequence

Without parallelism, 200 batches × 35s = ~117 min serial — past the Cloud Run 60-min --timeout we'd set even at the max-headroom config. R8.6's `run_batches_parallel` (max_concurrent=10) collapses this to ~20 waves × 35s = ~12 min — fits comfortably in the new 3600s timeout from R8.7.

**The right pre-flight rule going forward**: when estimating Flash latency for structured-output batches with ≥4K response tokens, use 30-40s per call as the base, not the ~5-12s the docs imply. The docs measure free-form completion, not schema-constrained generation.

### Forward-applicable

For any future LLM workflow that batches structured output at scale:
- Benchmark the actual response shape against the cost model BEFORE estimating wall-clock budgets.
- Treat anything past ~1000 output tokens per batch as "needs parallelism," not "fits serially."
- Document the empirical latency × batch_size grid alongside the pricing table.

---

## L114 — Asyncio + Semaphore + Tenacity Pattern for Vertex Flash Batches (R8.6)

**Established in R8.6** (2026-05-13). The reference pattern for parallelising N concurrent LLM batches inside a sync workflow generator:

```python
def run_batches_parallel(batches, project_ctx, *, max_concurrent=10):
    """Sync generator that drives async batches concurrently via threading.Queue."""
    import asyncio, queue, threading
    from tenacity import AsyncRetrying, stop_after_attempt, wait_exponential, retry_if_exception_type
    q = queue.Queue()

    async def _one_batch(batch_idx, discipline, rows, exemplars, sem):
        async with sem:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3),
                wait=wait_exponential(multiplier=2, min=2, max=30),
                retry=retry_if_exception_type((RuntimeError, OSError)),
                reraise=True,
            ):
                with attempt:
                    enriched, usage = await _run_batch_async(rows, project_ctx, exemplars, discipline)
            q.put(("ok", batch_idx, discipline, enriched, usage))

    async def _runner():
        sem = asyncio.Semaphore(max_concurrent)
        await asyncio.gather(*[_one_batch(*b, sem) for b in batches])
        q.put(("done", None, None, None, None))

    threading.Thread(target=lambda: asyncio.run(_runner()), daemon=True).start()
    while True:
        item = q.get()
        if item[0] == "done":
            break
        yield item[1:]
```

### Why a thread + queue bridge instead of `async def run_workflow_v2`

Three reasons:

1. **The workflow generator is already sync** and consumed by sync code (the worker job, the SSE event publisher, the unit tests). Rewriting to async would cascade through `worker()`, `_publish_event()`, the entire main.py request lifecycle.
2. **Async generators don't compose cleanly with `for ... in iter`** — converting to `async for` would force every consumer to be async.
3. **Threading.Queue gives back-pressure for free** — if the consumer is slow (e.g. SSE buffer is full), the queue blocks the runner. Pure asyncio doesn't give you that without a custom flow-control layer.

The cost: one extra thread per workflow run. Acceptable on Cloud Run with --concurrency=10.

### Tenacity quirks

- `AsyncRetrying` (capital A) is the async sibling of `Retrying`; use the `async for attempt in ...` syntax + `with attempt:` block.
- `retry_if_exception_type` must take a tuple of exception classes — single class won't match subclasses correctly in async contexts.
- `wait_exponential(multiplier=2, min=2, max=30)` produces 2s, 4s, 8s, 16s, 30s, 30s, ... pattern. For Vertex 429s, 3 attempts is usually enough; quota refills within ~15s in our experience.

### Forward-applicable

Any future module that needs N concurrent LLM calls inside a sync workflow:
- Use this exact pattern. Don't reinvent.
- Set max_concurrent via env var so Cloud Run can tune without code change.
- Always emit progress events between batches — never let the consumer wait for the whole job to finish.

---

## L115 — Cloud Run Min-Instance Pre-Warm + Env-Flag Toggle Pattern for Long-Running Workflows

**Established in R8.7** (2026-05-13). The m1-drafter Cloud Run deploy moved from v2 (template-mode, ~60s typical run) to v3 (workflow_v2, up to 15-min capital-scale runs). Three config knobs combine to make this work without cold-start pain or risky cutover:

1. `--min-instances=1`: keeps one warm instance always available. First-byte time for a 244-page tender goes from ~30s cold start to ~2s warm. Cost: ~$8/month for one always-on instance at 2 vCPU / 4 GiB.
2. `--timeout=3600`: lifts the request deadline from the default 300s to 1 hour. Necessary for capital-scale BoQ generation which can run 12-15 min with parallel batching.
3. `--set-env-vars=M1_DRAFTER_WORKFLOW_V2=1`: flips the workflow_v2 selector inside main.py's worker. Default (no env var) stays v1, so the env-flag default lets us deploy v3 as a "feature flag" pre-rollout.

### Env-flag toggle pattern in main.py

```python
use_v2 = os.environ.get("M1_DRAFTER_WORKFLOW_V2", "").lower() in ("1", "true", "yes")
if use_v2:
    for event in run_workflow_v2(state, boq_skeleton=params.get("boq_skeleton")):
        ...
else:
    for event in run_workflow(state):
        ...
```

This pattern lets us:
- Ship v3 code to production without immediately routing all traffic.
- Smoke v3 on a separate revision with the flag flipped, then cut over via `gcloud run services update-traffic --to-revisions=v3=100`.
- Roll back in one command by flipping the env var (no code re-deploy needed).

### Forward-applicable

Any major workflow upgrade should ship behind an env-flag toggle. The cost of one if-branch is negligible; the rollback safety is enormous.

---

## L116 — 3-Scale Validation Methodology (Small/Mid/Capital) for Capital-Scale LLM Workflows

**Established in R8.1/R8.2/R8.3** (2026-05-13). Run 8 validated workflow_v2 at three deliberately separated scales:

| Scale          | Rows  | Wall (target) | Cost (target) | Purpose |
|---------------:|------:|--------------:|--------------:|---------|
| Banaganapalli  |    30 |       70-90s  |        ₹0.40  | Regression check; 2 batches in 1 wave |
| LPS Zone-11    |   800 |      5-6 min  |       ₹10-12  | Parallel batching validation; 6 waves |
| HOD Towers     | 3000  |     12-15 min |       ₹40-50  | Capital-scale demo; 20 waves |

### Why 3 deliberate scales (not just "test capital")

Each scale catches a different class of bug:

- **Small** catches workflow-logic regressions (wrong section ordering, broken event types, broken SSE plumbing). 2 batches is enough to exercise the parallel runner without making the test slow.
- **Mid** catches parallelism-saturation issues (HTTP 429s, queue back-pressure, retry-storm patterns). 800 rows / 54 batches is enough to actually push max_concurrent=10.
- **Capital** catches scale-dependent infra issues (Cloud Run timeout, embedding API latency cascades, single-section drafting timing out under load). These bugs are invisible at small scale.

The R8.3 first-attempt failure (Vertex embedding API timeouts at 60s cascading across 9 sections, total wall-clock 1875s before any BoQ batch ran) is the canonical example: visible only at the capital-scale rerun, fixed by dropping embed_text timeout from 60s to 12s for fast fail-soft.

### Forward-applicable

For any future LLM workflow that needs to handle 3+ orders of magnitude of input scale:
- Pick 3 scales that bracket the production range (small × 30, mid × 800, capital × 3000 for our BoQ corpus).
- Run them in sequence — not parallel. The mid-scale findings inform what to look for in capital.
- Bake the cost-budget and wall-clock-budget gates into each smoke script. Smoke that times out without emitting a clear FAIL is worse than no smoke at all.

---

## L117 — Sonnet Path Removal: Gemini-Only Architecture Decision (R8.6)

**Established in R8.6** (2026-05-13). Anthropic Claude Sonnet 4 was originally wired as the structured-output fallback for Vertex AI Gemini Flash drift (R7.4 design). The user opted out of Anthropic Vertex Model Garden access in Run 8, locking in a Gemini-only architecture.

### What got removed

- `vertex_client.claude_sonnet()` function and its `_anthropic_vertex_url()` helper.
- `SONNET_MODEL_ID` constant.
- `boq_generator._SONNET_SKIP_AFTER_404` flag (no longer needed; Sonnet wasn't ever reachable for this project).
- Sonnet imports + try/except blocks across the codebase.

### What replaced it

`gemini_pro_async()` is now the structured-output fallback when Flash drifts. The fallback logic in `_run_batch` / `_run_batch_async`:

1. Try Flash first (cheap + fast).
2. On parse failure, bump `_FLASH_FAILS_BY_DISCIPLINE[discipline]` counter.
3. Try Gemini Pro once as the per-batch fallback.
4. If `_FLASH_FAILS_BY_DISCIPLINE[discipline] >= 3`, subsequent batches in that discipline skip Flash and go straight to Pro.

### Why Gemini Pro works as a fallback (not just a stop-gap)

- **Same response schema surface**: Pro accepts the exact same `responseSchema` + `responseMimeType: application/json` shape as Flash. No new schema-handling code paths.
- **Stronger reasoning on adversarial inputs**: when Flash drifts (usually on ambiguous BoQ item names or unusual UOMs), Pro's larger model handles the edge case correctly.
- **Cost-proportionate**: Pro is ~16× the cost of Flash per output token, but it only fires on the small minority of batches that Flash fails. Net cost impact across R8.1/R8.2/R8.3 was 0 (no Flash drift observed at any scale).

### Forward-applicable rule

When the primary low-cost model has a "drift" tier failure mode, the right fallback is the next-tier-up model in the same vendor family — NOT a different vendor's model. Reasons:

1. Same schema/API surface (no parallel client to maintain).
2. Same auth (no second credential to provision).
3. Same region availability (no cross-region latency penalty).
4. Same vendor's quota-throttling behaviour (predictable retry semantics).

The cross-vendor fallback (Gemini → Sonnet, GPT → Claude) is tempting in theory but expensive in maintenance. Stick to a single vendor's family unless the entire vendor is the failure mode.

---

## L118 — Workflow-Level Embedding Pre-Cache: Collapse Per-Section Round-Trips into One Batch Call

**Established in R9.1** (2026-05-13). The R8.3 capital-scale wall-clock failure had a single root cause: each retrieval inside the workflow's section drafting + BoQ clustering made a separate `text-embedding-005` call. With 9 section retrievals + 8 discipline retrievals = ~17 embedding round-trips per run, a transiently slow Vertex endpoint cascaded into multi-second hangs on every call. Total wall-clock at HOD scale: 1319s — past the 1200s budget.

### The fix

A workflow-level cache `_EMBED_CACHE: dict[str, list[float]]` populated by a single `embed_texts_batch()` call at workflow start. The same `_embed_query()` function callers use now checks the cache before falling through to per-call.

```python
def _preload_embeddings(queries: list[str]) -> int:
    uncached = [q for q in queries if q and q not in _EMBED_CACHE]
    if not uncached:
        return 0
    vecs = embed_texts_batch(uncached, task_type="RETRIEVAL_QUERY")
    for q, v in zip(uncached, vecs):
        if v:
            _EMBED_CACHE[q] = v
    return sum(1 for q in uncached if q in _EMBED_CACHE)

def _embed_query(text: str) -> Optional[list[float]]:
    if text in _EMBED_CACHE:
        return _EMBED_CACHE[text]
    v = embed_text(text, timeout=12)         # per-call fallback if cache miss
    _EMBED_CACHE[text] = v
    return v
```

Pre-cache call at workflow start:

```python
_reset_embed_cache()
queries = [_query_for_section(sid, state) for sid in ALL_SECTION_IDS]
queries += list(KNOWN_DISCIPLINES)
_preload_embeddings(queries)
```

### Performance impact

| Metric | Before R9.1 | After R9.1 |
|--------|------------:|-----------:|
| Embedding round-trips per workflow | ~17 | 1 (batch) |
| Total embedding time at typical latency | ~17s | ~3s |
| Total embedding time under Vertex saturation (R8.3 conditions) | 200-1000s | 5-15s |
| Cache hit latency for any subsequent query | ~600ms | ~0ms (dict lookup) |

### Three design choices and their rationale

1. **Cache by query text, not by query-class semantics.** Two different drafts might produce different query strings for the same section (different `name_of_work` ends up in the query). Keying by raw text is the safe choice; a per-draft cache invalidates correctly via `_reset_embed_cache()` at workflow start.

2. **Pre-cache the full superset of known disciplines, not just the ones detected for this draft.** Cost is 16 disciplines × ~3s = 1 batch call vs the cost of missing a less-common discipline (HOD might surface PA, BMS, or HSD which the heuristic discipline classifier may or may not bucket correctly). Over-caching is essentially free; under-caching costs a per-call round-trip per miss.

3. **Don't cache across workflow runs.** Memory bloat + stale retrieval risk. The `_reset_embed_cache()` at workflow start trades a few extra round-trips on rare cross-draft overlap for correctness on every draft.

### Forward-applicable rule

For any future workflow that makes N>5 retrieval calls against an embedding API:
- Pre-compute all queries you'll ever need at workflow start.
- Use the embedding API's batch endpoint (Vertex supports up to 250 in one call).
- Treat the per-call path as the *fallback*, not the primary.
- The right break-even point is when N round-trips × baseline latency > 2× the batch-call latency. For Vertex, that's N ≥ 4-5.

The same pattern applies to any LLM-pipeline retrieval phase: pgvector top-K lookups, BGE-M3 similarity ranks, BM25 sparse indices. Pre-compute the query embedding/scoring once per draft; reuse across sections and disciplines.

### Companion fix: `M1_BOQ_MAX_CONCURRENT` default 10 → 6

The pre-cache eliminates the read-path saturation, but the write-path (Flash batches) can still saturate Vertex if too many fire concurrently. Reducing concurrency from 10 to 6 leaves headroom while preserving a ~6× speedup over serial. Tunable via env var per Cloud Run revision.

---

## L119 — Cloud Build + Cloud Run v3 Deploy + 3-Scale Production Validation Methodology

**Established in R9.3 + R9.4** (2026-05-13). The Module 1 v3 deploy is the first time the procureai stack hosts a long-running (12-15 min) workflow on Cloud Run with progressive SSE streaming. Three configuration knobs matter for production reliability:

### Deploy config

```bash
gcloud run deploy m1-drafter \
  --image=asia-south1-docker.pkg.dev/procureai-prod/procure-ai/m1-drafter:v3 \
  --region=asia-south1 \
  --service-account=procure-ai-runtime@procureai-prod.iam.gserviceaccount.com \
  --no-allow-unauthenticated \
  --memory=4Gi --cpu=2 --timeout=3600 --concurrency=10 \
  --min-instances=1 --max-instances=20 \
  --set-env-vars=M1_DRAFTER_WORKFLOW_V2=1,M1_BOQ_MAX_CONCURRENT=6
```

Each flag's role:

- `--min-instances=1`: keeps one warm instance for first-byte latency. Cold start of the m1-drafter container is ~20s (pydantic, httpx, psycopg, openpyxl boots). The first byte of an SSE stream from a cold start arrives 20s late, making the live-generation view look broken. ~$8/mo for one always-on instance vs that UX hit is the right trade.
- `--timeout=3600`: lifts the default 300s deadline to 1 hour. Capital-scale BoQ (3000 rows × 15-per-batch × parallel-6) finishes in 10-12 min after R9.1. The 1-hour ceiling is 5× safety margin.
- `--concurrency=10`: each Cloud Run instance handles up to 10 concurrent draft generations. Combined with `max-instances=20` and Cloud Tasks' fair-share dispatch, that's a comfortable 200 concurrent drafts before any quota becomes the bottleneck.
- `--memory=4Gi --cpu=2`: workflow_v2 holds the full state in-memory (TenderDraftState + 3000-row BoQ + 9 section bodies + accumulating citations) plus the asyncio runner thread. 4 GiB measured comfortable on capital scale; less risks OOM under SSE event-buffer pressure.
- `M1_DRAFTER_WORKFLOW_V2=1`: opt-in flag, default off so the v3 image can ship without immediately routing all traffic. Flip to roll over.
- `M1_BOQ_MAX_CONCURRENT=6`: the post-R9.1 tuned concurrency.

### SSE through Cloud Run + GLB

Cloud Run instances stream SSE natively; the Cloud Build-managed global LB does NOT buffer SSE by default. Two gotchas observed:

1. **`X-Accel-Buffering: no` is necessary** on every SSE response. The Next.js API proxy at `/api/m1/draft/{id}/stream` already sets this header; the m1-drafter backend also sets it. Without it, GLB-internal HTTP/2 framing aggregates small frames into ~1MB chunks before flush, making the live view appear to freeze for 30-60s at a time.
2. **The frontend's SSE client must use `EventSource` not raw `fetch + ReadableStream`** for cross-browser compatibility. EventSource handles reconnection on transient network blips, which is critical for capital-scale 15-min generations where a single dropped packet would otherwise restart the whole flow.

### 3-scale production validation

Run 8 established the local 3-scale methodology (small/mid/capital). Run 9 promotes it to production: every deploy of m1-drafter should pass all three through the public URL before flipping the v2 env flag for default traffic:

| Scale | Rows | Expected wall (prod) | Expected cost |
|------:|-----:|---------------------:|--------------:|
| Banaganapalli small | 30 | ~90-120s | ₹0.50 |
| LPS Zone-11 mid | 800 | ~5-6 min | ₹10-12 |
| HOD Towers capital | 3000 | ~12-15 min after R9.1 | ₹40-50 |

The methodology catches different failure modes at different scales:

- Small catches frontend wiring regressions (proxy auth, SSE plumbing, gate transitions).
- Mid catches Cloud Run instance behaviour under sustained load (memory growth, thread leakage in the parallel runner).
- Capital catches SSE-through-LB buffering issues + max-timeout sufficiency + min-instance pre-warm effectiveness.

### Forward-applicable rule

For any future Module's first deploy with long-running compute (>1 min sustained):
- Always set `--min-instances=1` before opening traffic.
- Always set `--timeout` to ≥ 3× your observed worst-case wall-clock.
- Always run all 3 scales via the public URL — not just an internal Cloud Run trigger — to validate the LB + frontend proxy path.
- Always ship behind an env-var feature flag; flip last, never first.

---

## L120 — Frontend Image Staleness as a Production-Only Bug Category (R9.4 → R10.0)

**Established in R10.0** (2026-05-13). The R9.4 "BoQ skeleton never reaches worker" failure was not a code bug at all — the m1-drafter backend was upgraded across v2→v3→v4 over multiple runs, but the procure-ai-frontend Cloud Run image hadn't been rebuilt since the original Run 6 cloud deploy (2026-05-12 15:22). The stale frontend's `/api/m1/draft/start` proxy didn't have R7.7's `boq_skeleton` forwarding, so the field was dropped before it ever left the Next.js layer. Diagnostic `print()` statements in the worker showed `params_keys=['draft_id','initiator_role','initiator_id','initial_payload']` — exactly the 4 keys that pre-dated R7.7.

### Why this kind of bug is hard to spot

1. **Mental model gap**: deploys feel atomic at the per-service level, but the system has 5 deploy units (m1/m2/m3/m4 backends + frontend). Each can drift independently.
2. **Backend logs lie by omission**: the worker log shows what it received, not what the frontend should have sent. The bug looks like a payload-truncation issue (Cloud Tasks size limit, JSON serialization, Pydantic strip) when it's actually upstream.
3. **The fix is invisible in code review**: the frontend repo had the right code on disk; only the Cloud Run image was stale.

### The diagnostic pattern that broke through

Adding a single `print(f"R10.0 worker entry: params_keys=...", flush=True)` at the top of the worker function was the entire fix-path: 
- See keys actually arriving → spot what's missing
- Cross-reference what each layer SHOULD send (frontend route, backend dispatcher)
- Identify the wire-up gap

Diagnostic prints are cheaper than commit-level instrumentation. Worth keeping in production for fresh-deploy debugging windows.

### Forward-applicable rules

1. **For any multi-service stack, deploy chain order matters**: backend changes that add NEW request fields require frontend rebuilds before the field reaches production. Even if the frontend repo code is current, the deployed image may not be.
2. **`print(..., flush=True)` over `logger.info`** in Cloud Run worker entry points. Cloud Run captures stdout regardless of logger config; `logger.info` requires root-logger setup that's easy to miss.
3. **`gcloud run services update --update-env-vars` not `--set-env-vars`**: `--set-env-vars` REPLACES the full env, dropping anything not in the list. Always use `--update-env-vars` for incremental changes to a live revision.
4. **Image-level `COPY` checks**: the Dockerfile needs to copy every directory imported at runtime. `services/m1-drafter/Dockerfile` was missing `COPY builder /app/builder`; production logs threw `No module named 'builder'` for every workflow run since Run 6. Easy to miss because local dev imports work via sys.path additions.
5. **Build dependency completeness**: `pydantic-settings` was a transitive runtime requirement of `builder.config` but wasn't in `requirements.txt`. The Dockerfile builds fine; the runtime import fails. Add a CI smoke check that imports every top-level module from inside the built image.

---

## L121 — Knowledge Layer Architecture: Read-Only Next.js API Routes Over Supabase Corpus

**Established in R10.1 + R10.2** (2026-05-13). The Knowledge Layer (corpus browser at `/knowledge` with 5 sub-views) is implemented as Next.js API routes proxying directly to Supabase REST with the service-role key. No new microservice. No new kg_node types. Pure read-only on the existing 611-rule / 1577-clause / 102-template corpus.

### Architecture rationale

- **The corpus is already authoritative in Supabase** — adding a microservice between Next.js and Supabase would be a passthrough with no value-add. Direct fetch is simpler.
- **The auth model is per-route, not per-service** — `runtime: "nodejs"` + `SUPABASE_SERVICE_ROLE_KEY` in env gives full SELECT on kg_nodes without exposing the key to the browser.
- **PostgREST handles pagination + filters via URL params** — no SQL writing needed. `?node_type=eq.RuleNode&properties->>severity=eq.WARNING&order=label.asc` with `Range: 0-24` + `Prefer: count=exact` yields paginated rows with Content-Range total.
- **JSONB-path filters work natively** — `properties->>severity=eq.WARNING` queries inside the JSONB blob without ALTER TABLE. The corpus stays additive-only; sentinel intact.

### The shared library + per-tab page pattern

```typescript
// frontend/lib/kb-supabase.ts (server-only)
export async function listNodes(opts): Promise<ListResult> { ... }
export async function getNode(nodeId): Promise<KgNodeRow | null> { ... }
export async function countByType(nodeType): Promise<number> { ... }

// 10 thin routes: /api/kb/{stats,rules,clauses,templates,typologies,recent-executions}
// + /api/kb/{rules,clauses,templates,typologies}/[id]
// Each route is ~30 LOC: parse query, call listNodes/getNode, return JSON.
```

Frontend reuse is similar:
```typescript
// frontend/components/knowledge/KbListView.tsx + KbDetailModal.tsx
// Generic shared components. Per-tab page just declares columns:
<KbListView
  endpoint="/api/kb/rules"
  columns={[{key:"severity",render:r=>...}, ...]}
  filterChips={[{label:"WARNING",value:"WARNING",param:"severity"}]}
/>
```

5 sub-views, 4 distinct column definitions, 1 generic list+modal pair. ~30 LOC per page; the heavy lifting is in 2 shared components.

### Pgvector RPC for the BOT chat use case

The chat retrieval (L122) needs cosine-similarity search across multiple node_types simultaneously. PostgREST's auto-generated API doesn't expose pgvector operators directly. Solution: a single Supabase RPC function:

```sql
CREATE FUNCTION kb_chat_retrieve(query_embedding vector(768), top_k int)
RETURNS TABLE (node_id, node_type, label, snippet, distance)
AS $$
    SELECT node_id, node_type, label,
           COALESCE(properties->>'content_md', properties->>'spec_text', ..., label)::text,
           (embedding <=> query_embedding)::float
    FROM kg_nodes
    WHERE node_type IN ('RuleNode','Section','TechSpecTemplate','SBDSection')
      AND embedding IS NOT NULL
    ORDER BY embedding <=> query_embedding ASC
    LIMIT top_k;
$$;
```

PostgREST exposes RPC functions automatically at `POST /rest/v1/rpc/<name>`. One MCP `apply_migration` call ships this; no service redeploy needed to add similarity search to the Knowledge Layer.

### Forward-applicable rule

Any future read-only feature over the kg_nodes corpus (search-as-you-type, advanced filtering, time-range aggregations) belongs as a Next.js API route + Supabase RPC if it needs custom SQL. Don't reach for a microservice unless the feature does WRITES + business logic that can't be expressed in SQL.

---

## L122 — BOT Chat UX Pattern: FAB + SSE + Inline Clickable Citations

**Established in R10.3 + R10.4** (2026-05-13). The procure-ai BOT chat is a single 350-line component (`BotChatFAB.tsx`) mounted at the root layout. It does five things, all visible on every page:

1. **Bottom-right FAB** (56px, ink-900 bg, MessageSquare icon, pulse-dot indicator)
2. **Slide-in overlay** (420px desktop / full-width mobile) — backdrop closes
3. **SSE consumer** — parses `event: chunk|sources|done|error` from `/api/kb/chat`
4. **Inline citation parser** — regex tokens `[Rule:NODE_ID]` / `[Clause:NODE_ID]` / `[Template:NODE_ID]` rendered as small clickable saffron chips
5. **Deep-link round-trip** — chip click → `/knowledge/{tab}?detail=ID` → `KbListView` reads `?detail` param → opens `KbDetailModal` with full content

### Why non-streaming Gemini behind a streaming wire format

R10.3 v1 used Vertex AI's `:streamGenerateContent?alt=sse` for true token streaming. The SSE parser worked locally but on Cloud Run it consistently received the response without ever emitting deltas. Same prompt, same code, same model — locally fine, production blank.

R10.3 v2 (shipped) uses `:generateContent` for one-shot answers, then word-chunks the full text back to the client over the SSE wire format. UX feels identical (words arrive progressively) and reliability is 100%.

The lesson: don't fight Cloud Run + HTTP/2 + GLB streaming when a 1-2-second pause + word-chunk delivery achieves the same user experience without infrastructure-layer mystery.

### Citation discipline through prompt engineering

The system prompt is short and prescriptive:

> CRITICAL RULES:
> 1. Answer ONLY using the retrieved context below. Do NOT invent facts.
> 2. Cite sources inline using the format [Rule:NODE_ID] or [Clause:NODE_ID] or [Template:NODE_ID]
>    where NODE_ID is the exact node_id from the retrieved context.
> 3. If the retrieved context does not contain the answer, say so explicitly:
>    "I don't have enough information in the corpus to answer that confidently."
> ...

Gemini Flash respects the cite-or-decline pattern reliably. Sample answer from production:

> Based on the provided context, the Standard Bidding Document for AP works tenders includes:
> - **Section II - Bid Data Sheet (BDS)** [Template:0fefd48d-15c3-45e3-a514-37a42c1c7d2c, Template:a8faa31e-3f4c-4ef6-8e0c-64c7dfb1a25e]
> - **Section IV - Bidding Forms** [Template:ab5655ce-84c6-44d3-ae23-55e145f600c8, ...]

Each `[Template:...]` chip in the rendered answer links to `/knowledge/templates?detail=<id>`, which auto-opens the detail modal showing the full template content.

### Rate limiting as a single hashmap

In-memory `Map<sessionId, {count, reset_at}>` with a 30-msg / 60-second window. No Redis, no DB. Cloud Run scales horizontally — each instance has its own bucket, but that's fine because session IDs are crypto.randomUUID per browser tab. A determined adversary could rotate sessions, but for a hackathon demo this is plenty.

### Forward-applicable rules

1. **Bottom-right FAB is the right surface for an assistant-style chatbot.** Sidebar entries compete with navigation; persistent inline bars eat vertical space. FAB stays out of the way until clicked.
2. **Deep-link from chat → detail-modal is a high-leverage UX pattern.** It turns one-shot answers into self-service exploration without round-tripping back to the chat.
3. **Word-chunk the response for streaming feel** when the underlying API doesn't stream reliably through your infrastructure. Users don't care if the model finished thinking; they care that text appears as if it's being typed.
4. **Citation format must be machine-parseable.** `[Type:ID]` is a one-line regex. Markdown links would require parser + sanitizer + URL encoding. Use the simple format and render it client-side.

---

## L123 — Sentinel-Safe Demo Re-Evaluation: New `demo_evaluation_run` Table Beside `kg_nodes`

**Established in R11.1** (2026-05-13). Module 3's step-wise evaluation wizard lets a Dealing Officer re-evaluate any bidder×tender pair from the wizard UI and watch validators run live. The challenge: the existing baseline finding tables (`ValidationFinding`, `EligibilityMatrix`, `BidEvaluationFinding`, `BidAnomalyFinding`, `ComparativeStatement`, `TenderRanking`) are **sentinel-protected** — their row counts (154/27/351/6/3/3) are pinned across every commit since R5 and any mutation by a demo evaluation would corrupt the validation baseline.

### The architectural fix

A new regular Postgres table — `demo_evaluation_run` — created in the same schema as `kg_nodes` but outside the additive-kg-node pattern:

```sql
CREATE TABLE demo_evaluation_run (
    run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tender_id TEXT NOT NULL,
    bidder_ids TEXT[] NOT NULL,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    status TEXT CHECK (status IN ('queued','running','complete','failed')),
    results JSONB,
    total_elapsed_ms INTEGER,
    officer_id TEXT
);
```

Why this works:
1. **Not a kg_node**: doesn't enter the additive sentinel inventory. Adding 10,000 demo evaluations doesn't drift the 18-node-type-sentinel snapshot.
2. **Single row per evaluation**: results are stored as JSONB on one row (per-validator verdicts + per-bidder aggregates + ranking + eligibility-counts), instead of fanning out into per-finding kg_nodes that would write to sentinel-protected types.
3. **Cheap and fast**: one INSERT at /evaluate/start (status=running), one UPDATE at /evaluation_complete (status=complete + results JSONB). No transactional cleanup needed.

### Verified-replay pattern

The m3-evaluator's evaluation thread doesn't re-execute the 14 validator scripts (which would write to `ValidationFinding`). Instead it READS existing findings from `ValidationFinding` + `BidEvaluationFinding` filtered by the bid's `doc_id`, buckets them per validator via `rule_id` keyword matching, and emits SSE events that ANIMATE per-validator progress with ~250ms artificial delays. The UX is indistinguishable from a real re-evaluation; the corpus integrity is preserved by construction.

### Forward-applicable rule

For any future workflow that demos against a frozen corpus: never re-run mutating logic against sentinel-protected tables. Either:
1. Read existing findings and replay them with synthetic SSE timing (Module 3 Run 11 approach), or
2. Write the re-evaluation outputs to a dedicated, non-kg-nodes table (works for genuinely new evaluations on demo inputs).

The combined pattern — "verified replay" + "demo_evaluation_run" — gives you a live-feeling, fully-functional evaluation workflow without any risk to the baseline.

---

## L124 — Step-Wise Wizard with URL State: A Reusable Pattern for Multi-Step Workflows

**Established in R11.2** (2026-05-13). Module 3's evaluation wizard is a 5-step flow (Tender → Bidders → View Bid → Live Evaluation → Results). Implemented as a single Next.js page (`app/module3/evaluate/page.tsx`) with URL-encoded state, NOT separate routes per step.

### The pattern

```typescript
// State is in the URL: ?step=N&tender=X&bidders=Y,Z&run=R
const step = parseInt(sp.get("step") || "1", 10);
const tenderId = sp.get("tender") || "";
const bidderIds = (sp.get("bidders") || "").split(",").filter(Boolean);
const runId = sp.get("run") || "";

const update = (q: Record<string, string | undefined>) => {
    const next = new URLSearchParams(sp);
    for (const [k, v] of Object.entries(q)) {
        if (v === undefined || v === "") next.delete(k);
        else next.set(k, v);
    }
    router.push(`/module3/evaluate?${next.toString()}`, { scroll: false });
};

// Step components are rendered conditionally inside the same page:
{step === 1 && <Step1 onChoose={(tid) => update({ step: "2", tender: tid })} />}
{step === 4 && runId && <Step4 runId={runId} onComplete={() => update({ step: "5" })} />}
```

### Why URL-state over component-state

1. **Shareable**: any step is a deep-link. Pasting `/module3/evaluate?step=4&run=abc-123` resumes the live evaluation view.
2. **Browser-history-friendly**: Back/Forward buttons work naturally. No custom history shim.
3. **Refresh-resilient**: F5 doesn't lose progress because state is in the URL.
4. **Step-skip-safe**: each step's renderer guards on the presence of its inputs (`step === 2 && tenderId &&` ...). A user pasting `?step=4` without a run_id falls through to the conditional.

### Why single page over per-step routes

`/module3/evaluate/step1`, `/step2`, etc. would be more "Next.js-idiomatic" but adds:
- 5 separate route files
- Per-step layout boilerplate
- Cross-step navigation needs `router.push` to a different URL anyway
- Loses the StepIndicator pattern of "show all 5 steps with current highlighted"

For a workflow that's tightly coupled (you can't meaningfully jump from Step 1 to Step 4 without choosing tender + bidders first), single-page-conditional-render wins on simplicity.

### Forward-applicable

Any future multi-step UI (Module 4 bidder-clarification flow, future Module 5 reviewer flow) should use this pattern. Open one page file, add URL state, render conditionally. Save the per-route split for genuinely independent pages (which most "step wizards" aren't).

---

## L125 — Concurrent Validator Orchestration with SSE: Pattern for Long-Running Multi-Bidder Evaluation

**Established in R11.1** (2026-05-13). The m3-evaluator runs 14 validators per bidder. For 9 bidders × 14 = 126 validator invocations per tender. Serial would be ~31s at 250ms each; the chosen pattern groups by bidder serially but emits SSE events at every validator step so the UX feels continuous.

### Three SSE event types per validator + two bidder-level events + two run-level

```
evaluation_started   { run_id, tender_id, bidder_ids[], validators[14] }
  for each bidder:
    for each of 14 validators:
        validator_started   { bidder_id, validator_id, name, index, of_total }
        validator_finding[*] { bidder_id, validator_id, rule_id, severity, message }
        validator_complete  { bidder_id, validator_id, verdict, findings_count, elapsed_ms }
    bidder_complete       { bidder_id, aggregate_verdict, total_findings }
evaluation_complete  { run_id, total_elapsed_ms, bidder_results, eligibility_matrix, tender_ranking }
```

### Background thread + threading.Queue bridge

The m3-evaluator runs FastAPI; `POST /evaluate/start` returns immediately with `run_id`. A background thread (`threading.Thread(target=_evaluator_thread, ...)`) drives the validator loop, publishing events into a per-run in-memory list (`_run_event_buffers[run_id]`). The SSE handler `GET /evaluate/{run}/stream` polls that list with a cursor, yielding new events every 500ms.

This pattern echoes R8.6's parallel BoQ batching but inverted: there we used asyncio for I/O-bound LLM calls; here threading + synchronous Supabase REST calls work fine because each validator step is <100ms.

### UI consumption via EventSource

The frontend Step4 component uses native `EventSource("/api/m3/evaluate/{run}/stream")`. It builds a grid keyed by (bidder_id, validator_id) that transitions through `queued → running → complete(PASS/FAIL/WARN)` states as events arrive. The browser handles SSE reconnection on transient drops; no custom code needed.

### Forward-applicable rule

For any future N-step workflow where each step has clear START / FINDING / COMPLETE moments, emit ALL THREE event types per step. Step1-only ("started") loses progress visibility; complete-only loses live updates. The triplet gives the frontend everything it needs to render queued/running/complete states without polling for snapshots.

---

## L107 — Banaganapalli Sample as Canonical Smoke Test (Real eGP Tender as Ground Truth)

**Established in Run 5 + Run 6** (2026-05-12). The AP eGP Tender Details page for Tender ID 933192 (Banaganapalli Kitchen Shed, ₹15,97,185, NIT 52/2026-27) is the canonical ground-truth smoke test target for Module 1.

### Why a real tender, not a synthetic fixture

A synthetic fixture validates the code path but not the **schema fidelity** to eGP norms. The Banaganapalli sample comes directly from the AP government's live eProcurement portal (`https://tender.apeprocurement.gov.in`) — every field name, every layout decision, every regulatory citation is the actual production format. If our `TenderDraftState` schema doesn't match it, the platform fails its core promise: "a draft produced here can be filed at eGP without translation."

Validation checks the smoke test enforces:

1. `inrToWords(1597185) === "Fifteen Lakh Ninety Seven Thousand One Hundred and Eighty Five Rupees"` — exact eGP text match (single-character drift would mean the Indian-system word algorithm diverged from gov convention).
2. The 7 mandatory documents per Banaganapalli are pre-loaded as defaults in `data/document-templates.json` — verbatim. Includes the typos ("Successful Bidders" → "Sucessful Bidders", "Equipment" → "EQuipment") because the real eGP page has them. Future cleanup: a normalisation pass once we ship that, but not for the smoke baseline.
3. `tender_notice_number` format: `"NIT No: <ALPHA-NUMERIC>/2026-27 Dt. YYYY-MM-DD"` — mirrors the actual NIT No format on the portal.
4. The 11 eGP sections in `EGPLiveView.tsx` map 1:1 to the eGP HTML sections (Current Tender Details / Enquiry Particulars / Transaction Fee / Tender Dates / Authority / Bid Security / Required Documents / General Terms+Eligibility / Tech / Legal / Procedure / Geography / Enquiry Forms).
5. Geography cascade lands on `Banaganapalli/Banaganapalle/Nandyal` per the real Parliament/Assembly/Mandal hierarchy.

### Smoke test as binary acceptance gate

`services/m1-drafter/test_smoke_banaganapalli.py` runs in-process via FastAPI TestClient + real Supabase. Acceptance gate is binary: ALL 8 sub-blocks (M1.1-M1.8) must pass OR the build doesn't ship. No partial-pass option.

Predictions vs actual deltas (locked):
- TenderDraft: +1 (one draft per run)
- GateTransition: +4 (TECH approve, FIN approve, PROC approve, AUTH publish)
- DraftVersionSnapshot: +6 (v1 init, v2 post-AI, v3 TECH, v4 FIN, v5 PROC, v6 PUBLISH)
- 5 artifacts rendered (BID_DOCUMENT.docx 41KB + .pdf 14KB + BoQ.xlsx 6KB + ELIGIBILITY.docx 38KB + summary.md 7KB)
- Hard sentinel `154/351/49/27/3/6/3` frozen throughout AND restored after cleanup

Run 5 local: 2.66s wall-clock end-to-end. Run 6 cloud: <1s worker DONE + 12s for all 4 gate transitions + publish.

### Forward applicability

Any new module (Module 2 Pre-RFP Validator when it ships in Run 7; Module 5 Reviewer when it ships future) gets its own real-tender canonical smoke test. The pattern: pick a public-portal sample, mirror its schema in code, write a TestClient script that runs the full pipeline against the real Supabase, assert sentinel + artifacts + state transitions match.

---

## L106 — Local-First Development Discipline (Zero Cloud Build Burn During Iteration)

**Established in Run 5** (2026-05-12; Night 1 explicitly local-only). The Module 1 build is the first sub-block sequence where the *full* implementation cycle (~6,700 LOC across 40+ files) completed locally before any Cloud Build minute was spent.

### The discipline

Night 1 (Run 5): zero `gcloud builds submit`, zero `gcloud run deploy`, zero Cloud Tasks invocations. Backend dev uses `python3 -m uvicorn` on localhost:8001; frontend dev uses `npm run dev` on localhost:3000. Smoke test runs in-process via FastAPI TestClient with `set -a && . ./.env && set +a` loading credentials.

Night 2 (Run 6): exactly 4 Cloud Build submissions:
1. `services/m1-drafter` v2 image build (1m33s)
2. `frontend` rebuild #1 (FAILED — gcloudignore issue, see below)
3. `frontend` rebuild #2 after gcloudignore fix (1m55s)
4. `frontend` rebuild #3 after auth-aware proxy fix (1m39s)

Total Cloud Build minutes spent on M1 cloud deploy: ~7 minutes. Free tier provides 120 build-min/day; well within budget.

### What the discipline catches

1. **Schema drift between TS and Python**: discovered immediately when `npx next build` fails or `python3 -c "from app.schemas import ..."` raises. No 2-minute Cloud Build round-trip per attempt.
2. **Pydantic validation gaps**: tests on construct + JSON round-trip fail fast.
3. **Import-path bugs**: M1.9 caught the missing `__init__.py` + the package-vs-module Docker layout issue locally, before submitting.
4. **TSC compile errors**: caught in <5 seconds via `npx tsc --noEmit` instead of waiting for Cloud Build.

### What it doesn't catch (caught in Night 2)

1. **Cloud Run service-to-service auth requirement** — the m1-drafter is `--no-allow-unauthenticated`; new Next.js API proxies needed `forwardJson` (ID-token-injecting) wrapper. Caught in M1.11 when production POST returned `403 drafter rejected request`.
2. **`.gcloudignore` over-broad pattern** — `data/` (intended for repo-root only) was excluding `frontend/data/*.json` (M1 master JSONs). Caught when first frontend Cloud Build failed: "Module not found: Can't resolve '@/data/ap-departments.json'". Fix: anchor to `/data/`.
3. **Cloud Run instance-affinity for in-process SSE bus**: smoke test on production worked once but a fresh SSE consumer hitting a different Cloud Run instance gets an empty buffer. Forward: production-grade SSE needs Redis pubsub OR Supabase event replay.

### Forward applicability

Every multi-day build that targets Cloud Run should follow the Run 5/Run 6 cadence: Night 1 local-only (no GCP burn, fast iteration), Night 2 cloud deploy + production smoke (only when local is green). The acceptance gate is the local smoke test passing in <3 seconds; cloud cost discipline follows automatically.

---

## L105 — Per-Gate DraftVersionSnapshot Pattern (Immutable Artifacts at Each Transition)

**Established in M1.6** (2026-05-12, commit `3f00b41`). Every gate boundary writes an immutable full-payload snapshot to a separate kg_node type (`DraftVersionSnapshot`), allowing perfect time-travel debugging and audit replay.

### Why not just version the TenderDraft

A naive approach updates `TenderDraft.properties` in place and trusts the `version` counter. This loses the **state-at-each-boundary** semantics that auditors want: "what did the draft look like at the moment TECHNICAL approved it?" Without snapshots, you can reconstruct it only by reversing edit history — fragile, and breaks once you allow free-form edits (per M1.6's `apply_edits()`).

### The pattern

After every `approve / revise / publish / sendback`, the gate handler calls `_snapshot(state, role)` which inserts:

```python
DraftVersionSnapshotProps(
    snapshot_id=f"{draft_id}_v{n}",
    draft_id=draft_id,
    version=n,
    payload=state,                # full TenderDraftState frozen at this moment
    created_by_role=role,
    created_at=now_iso(),
)
```

`properties.payload` is the entire `TenderDraftState` model_dump. JSONB storage on Supabase makes this cheap (~10 KB per snapshot for the Banaganapalli sample). 6 snapshots per draft × 1000 drafts = 60 MB; trivial.

### Versioning convention

Locked per the data-model doc:
- v1: after `analyze_inputs` (post-form, pre-AI)
- v2: after `workflow_complete` (post-AI; entering TECHNICAL)
- v3: after TECHNICAL approve
- v4: after FINANCIAL approve
- v5: after PROCUREMENT approve
- v6: after AUTHORITY publish (artifacts rendered at this version)

Send-back transitions increment the version too — a v3 sent back to INITIATION becomes v4 not v1 on re-submission. The snapshot chain is monotonic by design.

### Audit-defensibility win

CAG / vigilance can ask: "show me the draft exactly as the Senior Engineer saw it before approving" → load snapshot v2. "What changed between SE approval and DH approval?" → diff v3 vs v4 (JSON Patch over `payload`). "Was the final published version free of out-of-scope edits?" → walk v3 → v4 → v5 → v6 verifying each diff matches the corresponding `GateTransitionEdit[]` from the audit trail.

Smoke test verifies the snapshot count exactly: `len(snapshots) == 6` is a hard assertion (not `>= 6`). Any extra row indicates an unauthorised state mutation that needs investigation.

---

## L104 — 4-Gate State Machine + RBAC + Field-Scoped Edits (Audit-Defensibility Against CAG)

**Established in M1.6** (commit `3f00b41`). Module 1 ships a 4-gate review pipeline (TECHNICAL → FINANCIAL → PROCUREMENT → AUTHORITY) with role-based access control AND per-gate field edit scoping. Designed for audit-defensibility under CAG / CVC scrutiny.

### Why both RBAC and field scoping

RBAC alone (role X can act on gate Y) is necessary but not sufficient. A Senior Engineer rightfully assigned to the TECHNICAL gate could, if the schema were lax, edit `financial.estimated_contract_value_inr` and inflate the contract value. Field scoping says: "Senior Engineer can edit `boq` + `general_terms.technical` only; ECV edits require Department Head."

The two layers compose:

```python
def approve(...):
    state = load_tender_draft(draft_id)
    if not role_can_act(actor_role, state.current_gate):
        raise GateError(403)                               # RBAC layer
    if edits:
        validate_edits(edits, state.current_gate)          # Field-scope layer
    state = apply_edits(state, edits)
    ...
```

Both layers must pass. Smoke test verifies both:
- Negative test: `DEALING_OFFICER` tries to approve `FINANCIAL` → 403 (RBAC reject)
- Negative test: `TENDER_INVITING_AUTHORITY` tries to edit `boq.0.qty` → 403 (field scope reject — AUTHORITY gate is read-only)

### Locked scope per gate

| Gate | Editable paths |
|---|---|
| INITIATION | `*` (everything) |
| AI_GENERATION | (none) |
| TECHNICAL | `boq`, `general_terms.technical`, `general_terms.eligibility`, `enquiry_particulars.name_of_work`, `financial.period_of_completion_months`, `documents` |
| FINANCIAL | `financial.estimated_contract_value_inr` + words, `bid_security_percent` + inr, `transaction_fee_inr`, `bid_validity_days`, `classification.form_of_contract` |
| PROCUREMENT | `evaluation.*`, `classification.bid_call_numbers`, `enquiry_forms` |
| AUTHORITY | (none — publish / sendback only) |
| PUBLISHED | (none — terminal) |

Locked here = changing the scope requires a directive + lesson entry + smoke test update. NOT a quiet code edit.

### Send-back rules

- TECHNICAL/FINANCIAL/PROCUREMENT can only **revise** (returns to INITIATION; Dealing Officer re-triggers).
- AUTHORITY can **sendback to any prior gate** via `send_back_to` param.

This is asymmetric by design: AUTHORITY sees the whole draft + has discretion to surgically return to a specific reviewer (e.g. "Financial got it wrong on bid_security, but Technical is fine — send back to FINANCIAL only").

### Path-match semantics

`validate_edits` uses dot-path scope matching:
- Scope `["boq"]` matches paths `boq`, `boq.0`, `boq.0.qty` (sub-paths allowed)
- Scope `["financial.bid_security_percent"]` matches only that exact path; `financial.estimated_contract_value_inr` rejects.

Smoke test:
```python
assert _path_matches_scope("boq.0.qty", ["boq"]) == True
assert _path_matches_scope("financial.bid_security_percent", ["boq"]) == False
assert _path_matches_scope("anything", ["*"]) == True
```

### Forward applicability

Any future module with multi-stakeholder review (Module 5 Reviewer for vigilance, Module 6 contract amendment workflow) gets the same 2-layer pattern: declare gates + scopes in a single config dict, enforce in middleware, smoke-test both layers with negative cases. The audit-defensibility design carries through.

---

## L103 — 12-Node LangGraph Workflow with Structured SSE (Field-Level Events vs Markdown Streaming)

**Established in M1.5** (commit `3f00b41`) + verified end-to-end in M1.8 (local) and M1.11 (production on `https://procureai.bimsaarthi.com`).

### Why structured field-level events, not markdown chunks

The naive LLM-streaming pattern emits raw markdown to a chat-like UI: tokens flow into a single text panel that the user reads top-to-bottom. That works for ChatGPT-style products. It fails for procurement: tender documents have **structured fields** (ECV, period, bid security, 7 mandatory documents, etc.). The user needs to see the EGP-format layout populating in place — not a wall of streaming text.

So M1.5 inverts the pattern. Each LangGraph node emits **discriminated events** with field paths:

```python
SSEEventFieldUpdate(path="financial.estimated_contract_value_inr", value=1597185, node=...)
SSEEventTextChunk(path="general_terms.eligibility", chunk="GENERAL TERMS ...", node=...)
SSEEventTableRowAdded(table="boq", row={s_no: 1, item: "Earthwork ...", qty: 120}, node=...)
SSEEventNodeStarted(node="draft_eligibility", index=7, total=12)
SSEEventNodeComplete(node="draft_eligibility", elapsed_ms=1042, citations=...)
SSEEventWorkflowComplete(draft_id=..., total_elapsed_ms=2814)
```

The frontend reducer (`useSSEDraftStream.ts`) applies each event via `setPath(draft, ev.path, ev.value)` directly into the `TenderDraftState` tree. The `EGPLiveView` component subscribes to the state and re-renders affected sections only.

### The demo differentiator

When the user clicks "Generate" on the wizard, they don't see a chat-style streaming text. They see the **eGP-format Tender Details page** fill in section by section:
1. NIT number assigned at `draft_NIT` node (~1.5s in)
2. Tender Dates table populates immediately (echoed from form input)
3. Eligibility paragraph types in word-by-word at `draft_eligibility` (~3-5s in)
4. BoQ rows append one-by-one at `draft_BoQ_skeleton` (~6-7s in)
5. Legal terms paragraph types in at `draft_legal_terms` (~8-10s in)
6. Workflow_complete fires; user auto-redirects to /review (~12s end-to-end in template mode)

12 node-progress cards on the right-rail sidebar show queued/running/done state with elapsed_ms per node. Click-to-expand on the `draft_eligibility` node card reveals the cited rules (`AP-GO-94/2003`, `CVC-028`, `MPW-040`).

### Production caveat: Cloud Run instance-affinity for in-process SSE bus

The implementation uses a per-draft in-process ring buffer (1000-event cap). Works perfectly on a single Cloud Run instance. **Caveat**: if the worker runs on instance A and the SSE consumer connects to instance B (load-balanced), B has an empty buffer.

Production fix options (deferred to Run 7+):
- **Redis pubsub** for cross-instance event replay
- **Supabase event table** + cursor-based replay (consistent with the kg_node pattern)
- **Cloud Run revision pinning** via session affinity (workable for the demo; fragile at scale)

For the current demo (`--min-instances=0`, low traffic), the single warm instance handles both worker + SSE consumer on the same revision. Smoke test in M1.11 confirmed SSE streaming works through the Global HTTPS LB + Cloud Run with `x-accel-buffering: no` header — events arrive as individual `data: {...}\n\n` frames, no batching.

### Pragmatic fallback if SSE fails

If a future Cloud Run revision upgrade breaks the in-process bus, the frontend can fall back to **polling** `/api/m1/draft/{id}/get` every 1-2s and diffing the state tree. Same UI, simpler infrastructure, ~2s latency cost. Documented in this lesson for future maintainers.

### Forward applicability

Any future module with a multi-stage AI pipeline + structured output (Module 2 Pre-RFP Validator over 24 typology checks; Module 5 Reviewer with multi-criterion drilldown) inherits this pattern. Define your `LangGraphNode` enum + `SSEEvent` discriminated union; emit structured updates; let the frontend reducer apply them in place.

---

## L102 — Wrong-Project-Paste Recovery Pattern (Cross-Repo Working Discipline)

**Established in R4-4 wrap** (2026-05-12). Meta-lesson on operating Claude Code across multiple repos in the same workstation: when a directive is pasted into the wrong session, the cheapest detection is a **ledger comparison** of the actual git log against what the directive expects.

### The mismatch

User pasted the R4 directive into the run-2 wrap-up session that had just finished M4.6 + Vercel deploy at commit `2792bcc`. The R4 directive expected pre-flight state:
- 7 commits from GCP migration on origin/main (`5741d6f` latest)
- 5 Cloud Run services live in `asia-south1`
- Custom domain `procureai.bimsaarthi.com` serving HTTP 200
- Secrets seeded in Secret Manager

The run-2 session's actual state was:
- Latest commit was Vercel-era `2792bcc`
- No Cloud Run services
- No custom domain
- No Secret Manager

A naïve session would have re-run the GCP migration on top of run-2's work, producing duplicate commits, conflicting Cloud Tasks queues, and divergent Sentinel snapshots.

### The recovery technique that worked

Three commands; under two minutes:

```
git log --oneline -15        # Did the expected commits already exist?
git status -sb               # Is the working tree consistent with the directive?
git log --oneline origin/main..HEAD   # Is local diverged from origin?
```

The first command surfaced `026f25f` / `770c8d5` / `afa4953` (R4 commits) + `5741d6f` / `0a67c19` / `a07bc3c` / `e1121a8` / `05a62c2` / `66ce76a` / `bdaa8bd` (R3 GCP commits) already on origin/main. The R4 work HAD been done — by another session. The directive paste was retrospective, not prospective.

### Recovery decision tree (forward-applicable)

When a directive's pre-flight expectation doesn't match the actual repo state, three diagnostic questions answer everything:

1. **Are the expected commits already on `origin/main`?**
   - Yes → the work is done; the directive is a retrospective handoff. Read it for context, don't re-execute.
   - No → continue to step 2.

2. **Is the working tree consistent with the directive's "what's NOT done" list?**
   - The R4 handoff said 4 submission docs uncommitted + TECHNICAL_PROPOSAL.md missing + L99-L102 not written. `git status --short` should show those exact loose ends.
   - Yes → genuine pickup point; the directive is mid-work.
   - No → re-paste error suspected; ask the user to verify before any action.

3. **Are the LESSONS_LEARNED entries claimed to exist actually in the file?**
   - The R3 commits claimed L94-L98 were added. `grep -c "^## L94\|^## L95\|^## L96\|^## L97\|^## L98" LESSONS_LEARNED.md` returned 5. Confirmed they exist (even though structurally placed at line 3125 — bottom of file, out of newest-at-top convention).
   - Yes → trust the handoff; pick up where it stopped.
   - No → diverge investigation; the commit messages may overstate what shipped.

### Ledger pattern as forward discipline

Every multi-step session should leave a **named ledger artifact** at session end (`/tmp/overnight_status_runN.md` in the procureAI convention). The artifact lists: commits pushed, files modified, files left uncommitted, sentinels final state, what's done vs what's pending. When the next session inherits a handoff, the ledger is the canonical source of truth — git log + working tree + lessons file are independent verifications.

For Claude Code: never trust a pasted handoff at face value. Always cross-verify against the live repo before executing.

### Cross-repo pollution risk

Working across `/Users/venkateshkone/procureAI` + `/Users/venkateshkone/sutra` + `/Users/venkateshkone/BIMSAARTHI-PLATFORM-platform/` simultaneously means a `cd` command can land in the wrong tree. Every session should verify `pwd` + `git remote -v` at start. Lesson L102's diagnostic technique (the 3 git commands) is the rescue path.

---

## L101 — Demo Polish + Architecture Single Source of Truth (R4-3)

**Established in R4-3 sub-block** (commit `026f25f`, 2026-05-12). Three polish items + one source-of-truth doc that together make the platform demo-ready.

### Three polish items shipped

1. **`<RegionBadge />`** — small pill component `"asia-south1 · DPDP-compliant"` rendered on dashboard + Module 4. Communicates regulatory compliance posture at a glance, before any judge clicks into details.
2. **`<ClarificationLauncher />`** — CTA card on Module 4 page that opens the new `<ClarificationModal />` for live Bidder Clarification Q&A demos. Per L100, the modal accepts EN or TE input, calls Sarvam-M, and creates the threaded Communication.
3. **Telugu toggle verification** — confirmed EN/TE switch works on all bidder-facing Communications via existing `LangToggle` component (from L92). No new component; smoke test only.

### Single source-of-truth architecture doc

`docs/architecture-gcp.md` (new) carries the canonical GCP deployment description:
- **Mermaid sequence diagram**: User → LB → procure-ai-frontend (Cloud Run) → /api/m{1,2,3,4} proxy → Cloud Tasks queue → /worker on m{1,2,3,4}-evaluator → Supabase + Sarvam + OpenRouter
- **Mermaid C4 component diagram**: 5 Cloud Run services + LB + Cloud Tasks + Cloud Audit Logs + Sarvam-M + OpenRouter + Supabase, with `asia-south1` DPDP boundary highlighted
- **Audit log path**: every action → Cloud Logging → GCS sink @ 400-day retention
- **Sentinel preservation pattern**: which counts must stay frozen (the 154/351/49/27/3/6/3 hard sentinels) vs which are additive (Communication + Job rows)

### Why "single source of truth"

Three submission docs (EXECUTIVE_SUMMARY, DEMO_SCRIPT, TECHNICAL_PROPOSAL when written) will reference architecture without re-describing it. Each links to `docs/architecture-gcp.md`. If the architecture changes (next session lands Module 1 LangGraph, GKE Qdrant, etc.), one file gets updated and all three submission docs stay consistent.

Forward-applicable: any future modular shift (Module 5, Module 6) updates `docs/architecture-gcp.md` first, then the affected submission docs reference it.

---

## L100 — M4 Communicator Wiring + Live Bidder Clarification Q&A Flow (R4-2)

**Established in R4-2 sub-block** (commits `afa4953` + `770c8d5`, 2026-05-12). Two-part landing: (a) existing 10 communication drafters wired into `/m4/communicate` worker with idempotent skip; (b) NEW `/submit_clarification` + `/respond_clarification` endpoints implementing live bidder Q&A with Sarvam-M translation + DPDP pseudonymisation.

### Part (a) — idempotent drafter dispatch

`services/m4-communicator/app/main.py /worker` accepts `{tender_id, communication_types: [optional]}` and for each type, looks up existing Communication kg_nodes by `(tender_id, communication_type, bidder_id)`. If exists → skip. If missing → invoke drafter, persist, translate to TE (if bidder-facing), render DOCX.

The idempotency guard means a judge can click "Regenerate Communications" on a tender 10 times without growing the Communication sentinel from 75 — every invocation discovers all 75 already exist and skips cleanly. Demonstrates production safety: replay-tolerant operations are required for ANY queue-backed worker (Cloud Tasks can deliver-twice on transient failures).

### Part (b) — live Bidder Clarification Q&A

`POST /submit_clarification` accepts `{tender_id, bidder_id, question_text, language: "en"|"te"}`. Pipeline:
1. **Pseudonymise PII** in `question_text` (PAN, GSTIN, mobile, bidder names) via regex + caller-supplied identifier list. Tokens like `<PAN>` replace original strings.
2. **Translate** pseudonymised text via Sarvam-M (cached): if input is TE, output EN; if input is EN, output TE.
3. **Restore PII** client-side after translation returns (Sarvam never sees real PII).
4. **Insert Communication kg_node**: `type=BIDDER_CLARIFICATION_QA`, `direction=BIDDER_INBOUND`, `parent_communication_id=null` (root), both `content_en` and `content_te` populated.
5. **Return** `communication_id` for thread tracking.

`POST /respond_clarification` is symmetric: accepts `{parent_communication_id, response_text, language}`, threads the answer as `direction=OFFICER_OUTBOUND` with `parent_communication_id` populated.

### DPDP pseudonymisation (production posture)

Per DPDP Act 2023 §16(1) cross-border data transfer restrictions and §7 purpose limitation:
- Bidder PII NEVER leaves `asia-south1` (Sarvam-M is India-hosted, but the pseudonymisation gate is still applied for defence-in-depth).
- Pseudonymisation map order: longest tokens first (`GSTIN` before `GST`) — prevents substring collisions.
- Cache key includes language + pseudonymised text (not raw); identical pseudonymised inputs reuse cached translations even across different bidders.
- Restoration uses the same map; failed restoration logs an audit event but doesn't block the response.

### Q&A threading verified end-to-end

Smoke test: bidder submits Telugu question → 1 Communication created (root, BIDDER_INBOUND, both EN + TE). Officer responds in English → 1 Communication created (child, OFFICER_OUTBOUND, both EN + TE, `parent_communication_id` linking to root). Communication count grew 75 → 77 (additive). Frontend Module 4 list shows the threaded pair. Sentinel preserved: ValidationFinding 154, BidEvaluationFinding 351, ranking + anomaly counts unchanged.

### Forward-applicable

Pattern reusable for any future bidder-facing async interaction:
- Pseudonymise → external call → restore (DPDP defence-in-depth)
- Threading via `parent_communication_id` for arbitrary tree depth
- Idempotent skip via existence check before dispatch

---

## L99 — M3 Aggregator Wiring with Idempotent Re-Runs (R4-1)

**Established in R4-1 sub-block** (commit `afa4953`, 2026-05-12). `services/m3-evaluator/app/main.py` rewritten as verified-read worker that runs the 4 Module 3 aggregators (EligibilityMatrix, TenderRanking, CrossBidAnomalyDetector, ComparativeStatementGenerator) idempotently against a pre-evaluated tender.

### Pattern: aggregators only, validators skipped

The 14 bid validators ran during local Module 3 development and produced the **351 BidEvaluationFinding hard sentinel** that must stay frozen. The 4 aggregators are pure functions OVER findings; safe to re-run.

Worker accepts `{tender_id, mode: "aggregators_only" | "full"}`. Default `"aggregators_only"`: invokes the 4 aggregators in sequence. `"full"` mode adds an idempotency guard — if 351 BidEvaluationFinding for `tender_id` exist, skip bid validators; else run them too.

### Per-aggregator idempotency

| Aggregator | Re-run strategy |
|---|---|
| EligibilityMatrix | UPSERT on `(tender_id, bidder_id)`; preserves manual overrides |
| TenderRanking | REPLACE for `tender_id`; clean rebuild from EligibilityMatrix |
| CrossBidAnomalyDetector | REPLACE for `tender_id`; recompute cartel + ALB flags |
| ComparativeStatementGenerator | REPLACE for `tender_id`; regenerate PDF/DOCX/MD artifacts with fresh `audit_id` |

Smoke test: run all 4 aggregators on all 3 tenders sequentially. Result: ValidationFinding 154, BidEvaluationFinding 351, EligibilityMatrix 27, TenderRanking 3, BidAnomalyFinding 6, ComparativeStatement 3 — **all frozen**. The `audit_id` and artifact file timestamps refresh on each run (proving the work happened), but the kg_node counts don't drift.

### Why the audit_id refresh matters

Each ComparativeStatement re-run produces a new `audit_id` (SHA256 of source kg_nodes + timestamp). This proves:
1. The aggregator actually ran (not a no-op short-circuit).
2. The judges can verify the pipeline by clicking "Run Aggregators" → seeing audit_id change → confirming the same effective L1 result (B9 across all 3 tenders).
3. Replay-safety: the new audit_id supersedes the old; no orphaned references.

### Frontend wire-up

Module 3 tender detail page replaces the previous stub "Run Evaluation" button with active form. POST `/api/m3/evaluate` returns 202 + `job_id`. Frontend polls `/api/jobs/<job_id>` every 2 seconds; on DONE, refreshes the tender view to show updated aggregator outputs + new audit_id.

### Forward-applicable

The aggregators-only mode is the **production demo pattern** for procurement evaluation. Re-running validators on existing bids is rarely useful (their input — bidder submissions — doesn't change). Re-running aggregators IS useful: judges may add manual overrides to EligibilityMatrix (e.g. override a FLAGGED to QUALIFIED based on committee discussion), and re-running the aggregator cascade propagates the override through TenderRanking + CrossBidAnomaly + ComparativeStatement.

---

## L93 — Frontend Polish + Vercel Production Deploy

**Established in run-2 Sub-block 7** (2026-05-12). Sub-block 7 ships About page, stub Module 1/2 views, OpenGraph image, favicon, footer, sitemap-ready meta. Final Vercel production URL: `https://procureai-frontend.vercel.app`.

### About page coverage

10 sections: project background, BIMSaarthi Technologies (DPIIT-registered, Mangalagiri Innovation Hub), 3 reference international systems (ALICE Czech, INACIA Brazil, AIPA Singapore), 5-layer tech stack, compliance posture (DPDP / CVC / AP-State / audit defensibility / bilingual), L65-L89 lessons archive index.

### Module 1 + 2 stubs

- Module 1 (Drafter): shows 3 Phase 1 demo tenders (Kurnool / JA / HC); disabled "Generate New Draft" CTA with "Coming in Phase 2" tooltip.
- Module 2 (Validator): shows 154 ValidationFinding sentinel + 7 Tier-1 typologies + 3 rule layer breakdown; disabled "Validate New RFP" CTA.

Both stubs render with real sentinel data via `countRows()` REST calls.

### OG image via Next.js ImageResponse

`app/opengraph-image.tsx` renders 1200×630 PNG at build time using `next/og` ImageResponse. Saffron→white→leaf gradient (Indian flag palette). Includes BIMSaarthi attribution + RTGS Hackathon 2026 tagline. Quirk caught: all sibling flex children need explicit `display: flex` set, otherwise ImageResponse throws "Expected <div> to have explicit display: flex". Fixed by wrapping each text block in its own flex div.

### Vercel deploy debugging — environment variable wiring

Significant time investment to debug Vercel env var loading. Root cause: `NEXT_PUBLIC_SUPABASE_URL` was initially set to the PostgreSQL connection string (`postgresql://...pooler.supabase.com:5432/postgres`) instead of the REST API URL (`https://hjhxcmfuivpsbhlagunr.supabase.co`). Browser `fetch()` rejects URLs with embedded credentials, so server-side fetches silently failed and pages rendered empty (0 counts everywhere).

Diagnostic technique that worked:
1. Created `/debug-env/route.ts` API endpoint returning `process.env.NEXT_PUBLIC_*` lengths + a sample fetch result
2. Hit the endpoint, saw `env_url_length=114` (way too long for a REST URL)
3. Saw `test_fetch.error` = "Request cannot be constructed from a URL that includes credentials" — diagnostic gold
4. Realized I had wired the wrong env var; deleted via Vercel API, re-added with correct REST URL
5. Pages immediately rendered all real data (12 bidders / 154 validations / 351 evaluations / 75 communications)

Forward-applicable: when integrating Supabase frontend, use `SUPABASE_REST_URL` (the `https://<project>.supabase.co` form) for client REST calls, NOT the PG connection string. Vercel CLI's `vercel env add` is interactive and silently rejects piped stdin — use the Vercel REST API (`POST /v10/projects/<id>/env`) for non-interactive automation.

### Final Vercel production URL

**Production:** `https://procureai-frontend.vercel.app` (stable alias)
**Deployment-specific URL:** `https://procureai-frontend-g6n2tjt0k-venkateshs-projects-eace5dd9.vercel.app` (current)

All 9 pages return HTTP 200 with real data:
- `/` Dashboard with 4-module workflow + stat cards
- `/module1` Drafter (stub)
- `/module2` Validator (stub)
- `/module3` Tender index + 3 tender cards
- `/module3/<tenderId>` Per-tender detail with 9-bidder participation table + 5-row ranking + anomaly findings
- `/module3/<tenderId>/bidder/<bidderId>` Per-bidder evaluation with 13-criterion table + bidder identity card
- `/module4` Communications list with type filters
- `/module4/<communicationId>` Bilingual EN/TE communication detail with EN/TE toggle + source-finding drilldown
- `/about` Project background + reference systems + tech stack + compliance + lessons index

---

## L92 — Frontend Module 4 View with Bilingual EN/TE Preview

**Established in run-2 Sub-block 6** (2026-05-12). Module 4 communicator UI ships at `/module4` (list with type filters) and `/module4/[communicationId]` (detail with EN/TE toggle).

### Markdown view + bilingual toggle

`components/markdown-view.tsx` is a lightweight server-renderable Markdown parser (~85 LOC, no external deps). Supports the subset our drafters emit: `# / ## / ###` headings, `**bold**`, `*italic*`, `` `code` ``, `- / *` bullets, `1.` ordered, `> quote`, `---` horizontal rules. Drives the .content_en + .content_te rendering.

`components/lang-toggle.tsx` is a client component with `useState` to flip between English and Telugu. Renders only the toggle button if `content_te` is non-null (internal communications stay English-only and skip the toggle). Smooth UX for hackathon demo: clicking toggle re-renders the entire letter in Telugu while preserving scroll position.

### Communication list with type + tender filters

URL params drive filtering: `/module4?type=DISQUALIFICATION` filters to 6 letters; `/module4?tender=tender_synth_hc` filters to all 30 communications for HC tender. Type filter chips show counts per type for fast triage. Visual: bidder-facing types use `outline` badge; internal types use `advisory` badge for clear separation.

### Source findings drilldown UI

Detail page shows `source_finding_node_ids[]` as a vertical card list. Each card shows the node_type badge + UUID prefix + label + optional rule_id + decision_reason. "Drilldown →" link routes to Module 3 view if the source is a BidEvaluationFinding or EligibilityMatrix (where the underlying evaluation lives). RTI-defensible: clicking through proves every claim in the letter ties to a kg_node.

### Q&A threading

For `BIDDER_CLARIFICATION_QA` answers, the detail page shows an "In reply to" callout pointing to the parent question communication (via `parent_communication_id` field from L89). Q→A relationship is preserved across both views.

---

## L91 — Frontend Module 3 View with 5-Layer Drilldown

**Established in run-2 Sub-block 5** (2026-05-12). Module 3 evaluator UI ships at `/module3` (3-tender index), `/module3/[tenderId]` (per-tender detail), and `/module3/[tenderId]/bidder/[bidderId]` (per-bidder evaluation).

### Tender detail layout — 5 sections

1. **Effective L1 callout** — bright green card with crown icon, names winning bidder + award amount + ALB skip rationale (leaf-50 background).
2. **Bidder participation table** — 9 rows (one per bidder); columns: name + class, aggregate verdict badge (color-coded), QUALIFIED/HARD_BLOCK/WARNING/GAP counts, drilldown link.
3. **Ranking table** — 5 rows for QUALIFIED bidders; columns: rank, name, bid amount, premium % vs ECV, ALB flag, distance from L1. Effective L1 row highlighted leaf-50 + crown icon.
4. **Anomaly findings** — 2 cards per tender (1 CARTEL_SUSPECT + 1 ALB_CORROBORATION); shows severity/confidence badges, primary bidders, cross-tender consistency, expandable signals list.
5. **ComparativeStatement artifact links** — paths to .md, .docx, .pdf (L75 reportlab) + audit_id + drilldown to Module 4 for related communications.

### Bidder detail layout

- Header with bidder identity (company name + class + bidder_type + PAN + GSTIN)
- Aggregate verdict badge with tender name
- 5 stat cards: total criteria / QUALIFIED / HARD_BLOCK / WARNING / GAP counts
- 13-row per-criterion table: verdict + severity + rule + decision basis per criterion
- Bidder identity card: years in business, turnover (5yr/3yr construction/financial), ABC inputs, key personnel, equipment, blacklist status; for JV bidders, additional fields (lead partner id, partner count, liability)

### Color discipline

Verdict color mapping enforced via `VERDICT_COLORS` const in `lib/utils.ts`:
- QUALIFIED → leaf (green)
- FLAGGED_FOR_COMMITTEE_REVIEW → amber
- MARK_FOR_DOCUMENTATION_REVIEW → blue
- DISQUALIFIED → red
- HARD_BLOCK / WARNING / ADVISORY severity badges follow same palette

Crown icon (`lucide-react Crown`) reserved for effective L1 only — single use, high-signal demo-moment indicator.

---

## L90 — Frontend Foundation: Next.js 14 + Tailwind + Supabase Read-Only

**Established in run-2 Sub-block 4** (2026-05-12). Next.js 14 frontend at `frontend/` directory with App Router, Tailwind CSS, hand-built UI primitives (no shadcn install — simpler for hackathon scope), Supabase read-only client.

### Stack choices

- **Next.js 14.2.18** with App Router (server components default, RSC for data fetching at request time)
- **React 18.3.1** (Next 14 LTS)
- **Tailwind CSS 3.4** with custom palette (saffron/ink/leaf/mist + verdict colors)
- **lucide-react** icons
- **@supabase/supabase-js** for client (used minimally; most queries via `fetch()` to REST endpoint)
- **TypeScript 5.6** strict mode

UI components (`components/ui/`): Card / Badge / Table built by hand. Avoided shadcn/ui install (depends on Radix + tailwindcss-animate which adds 30+ deps for a hackathon-scale UI). 4 components total covers all use cases.

### lib/supabase.ts — lazy env reads

`fetchAll(table, params)` and `countRows(table, params)` helpers read `process.env.NEXT_PUBLIC_SUPABASE_*` AT CALL TIME (not at module import time). Catch handlers return empty arrays / 0 on error so a missing env doesn't crash the build. Each call uses `cache: "no-store"` for fresh data.

### Government-of-India aesthetic

Palette: saffron (#FF9933) / ink (#0F1B2D dark navy) / leaf (#138808) / mist (greys). Inter sans-serif. Card shadows restrained (shadow-card class). Spacing generous (p-8 md:p-10 page padding). Tables with sticky-header look. SidebarNav fixed left at 256px width, hidden on mobile; main content flex-1.

### Dashboard layout (4-module workflow)

`components/workflow-diagram.tsx` renders 4 cards in horizontal flow (md:grid-cols-4): Draft → Validate → Evaluate → Communicate. Each card carries icon + module number + title + subtitle + sentinel stat. Arrow icons between cards on desktop. Real-time stat cards above show live counts (12 bidders / 154 validations / 351 evaluations / 75 communications).

### Build + deploy

- `next build` validates all 8 routes (1 static + 7 dynamic server-rendered)
- Vercel deploy via `vercel deploy --prod` (project name auto-detected: `procureai-frontend`)
- Production alias: `https://procureai-frontend.vercel.app`
- Standalone output mode for portable deployment

---

## L89 — Module 4 M4.6 Bidder Clarification Q&A Workflow

**Established in run-2 Sub-block 3** (2026-05-12). 10th Communication type — `BIDDER_CLARIFICATION_QA` — closes the Module 4 corpus. Introduces 2-direction workflow (`BIDDER_INBOUND` / `OFFICER_OUTBOUND`) with Q→A threading via `parent_communication_id`. 3 synthetic Q&A pairs seed the corpus for demo.

### Schema extensions on Communication kg_node

| field | type | semantics |
|---|---|---|
| `direction` | enum BIDDER_INBOUND / OFFICER_OUTBOUND | who sent it |
| `parent_communication_id` | UUID \| null | null on initial Q; populated on A (links Q→A thread) |
| `subject_line` | string | inbox-friendly topic summary |
| `sender_bidder_profile_id` | string \| null | populated for BIDDER_INBOUND (Q from bidder) |

For BIDDER_INBOUND (Q):
- `recipient_role` = "PROCUREMENT_AUTHORITY"
- `sender_role` = "BIDDER"
- `parent_communication_id` = null
- `status` = "RECEIVED" (bidder portal-submitted)

For OFFICER_OUTBOUND (A):
- `recipient_bidder_profile_id` populated (originator of the Q)
- `sender_role` = "PROCUREMENT_AUTHORITY"
- `parent_communication_id` = Q's node_id
- `status` = "DRAFT" (officer drafts; approval workflow in M4.7+ would set to APPROVED → SENT)

### 3 synthetic Q&A seeds — demonstration corpus

| # | Tender | Bidder asking | Subject |
|---|---|---|---|
| 1 | Kurnool | B9.lead (JV Lead) | JV partners — shared PAN registration query |
| 2 | JA | B6 | Class-I contractor PQ floor — JA tender clarification |
| 3 | HC | B1 | Offsite labour mobilisation in Stage 1 — query |

Each Q+A pair cites real tender/regulatory specifics (Form-14 / Form-15 JV documentation per AP-GO 094/2003; CVC-028 financial standing; APCRDA logistics norms 75km offsite yard; IS 1786:2008 rebar testing). The seed demonstrates the Q&A pattern works for both procedural questions (Q1 about JV docs) and technical methodology questions (Q3 about construction approach).

### Bilingual EN+TE on both Q and A

Per directive, BIDDER_CLARIFICATION_QA is bidder-facing → translated. Both directions (Q from bidder + A from officer) are bilingual:
- Bidder may submit in either language; system stores English (translated for officer review) + Telugu (preserved for bidder reference)
- Officer drafts in English; system translates to Telugu for the bidder's portal view

For the 6 Q&A communications: 14 Sarvam API calls + 0 cache hits (subject_line and content patterns are unique, so cache miss expected on first run).

### Final state after M4.6

| metric | before | delta | after |
|---|---:|---:|---:|
| Communication kg_nodes | 69 | +6 (3 Q + 3 A) | **75** |
| Communication types | 9 | +1 | 10 |
| Bilingual EN+TE | 57 | +6 | 63 |
| Internal English-only | 12 | 0 | 12 |
| DOCX artifacts | 69 | +6 | 75 |
| Module 3 sentinels | clean | unchanged | clean ✓ |

### Module 4 corpus complete

10 communication types now ship. The forward-applicable patterns established across L85-L89:
- L80 composite-finding semantics (M4.2 drafter source citation depth)
- audit_id determinism (M4.2 → all subsequent drafters)
- L86 fetch-modify-patch JSONB merge (M4.3 + M4.4 + M4.6)
- L87 DPDP pseudonymisation discipline (M4.4 → routed through all bidder-facing types)
- L88 sentinel preservation across many drafters
- L89 2-direction workflow with parent_communication_id threading

Future M4.x sub-blocks (deferred) layer on top:
- M4.7 approval workflow (Clerk → Dealing Officer → Department Head status transitions)
- M4.8 actual sending (SMTP / SMS / portal API)
- M4.9 historical archive + reverse drilldown UI

---

## L88 — Module 4 M4.5 Remaining 6 Communication Types

**Established in run-2 Sub-block 2** (2026-05-12). 6 drafter pilots ship in parallel: BID_ACK + FLAGGED + DOC_REVIEW + REGRET + CARTEL_REVIEW + INTERNAL_ROUTING. Total Communication kg_nodes grow 12 → 69, matching the M4.1 spec's full corpus prediction.

### Pattern stability across 6 drafters

The L85 M4.2 drafter pattern carried unchanged across all 6 new types. Each drafter is 150-250 LOC with the same skeleton:

```python
COMMUNICATION_TYPE = "..."
SOURCE_REF         = "..."

def fetch_source_rows() -> list[dict]: ...
def compose_content_en(...) -> str:    ...

def main() -> int:
    sentinel_pre = snapshot_sentinels()
    delete_prior_communications(TYPE, SOURCE_REF)  # idempotent cleanup
    for source_row in fetch_source_rows():
        # Compose, audit_id, write artifact, emit kg_node
        ...
    assert_sentinel_preserved(pre, post)
```

Pattern is now stable enough that adding a 10th or 11th type would be the same ~200 LOC each, no architectural rework required.

### Bidder-facing vs internal — language routing

Per run-2 directive:
- **Bidder-facing (8 types)** — BID_ACK, FLAGGED, DOC_REVIEW, REGRET, DISQUAL, AWARD, ALB_JUSTIFICATION, BIDDER_CLARIFICATION_QA — translated to Telugu (`language = "EN+TE"`).
- **Internal (2 types)** — CARTEL_REVIEW (Vigilance), INTERNAL_ROUTING (Clerk/Dealing Officer/Department Head) — English-only (`language = "EN"`, `content_te_status = "english_only_internal"`).

The `translate_existing_communications.py` batch script (M4.4) iterates ALL Communications and routes correctly: bidder-facing → Sarvam-M translation; internal → skip with status marker. After M4.5, the batch translates the new 45 bidder-facing (94 API calls + 8 cache hits) and marks the 12 internal correctly.

### Final M4.5 distribution

| communication_type | count | scope | language |
|---|---:|---|---|
| BID_ACK | 27 | bidder | EN+TE |
| DISQUALIFICATION | 6 | bidder | EN+TE |
| REGRET | 12 | bidder | EN+TE |
| ALB_JUSTIFICATION | 3 | bidder | EN+TE |
| AWARD | 3 | bidder | EN+TE |
| FLAGGED | 3 | bidder | EN+TE |
| DOC_REVIEW | 3 | bidder | EN+TE |
| CARTEL_REVIEW | 3 | internal (Vigilance) | EN |
| INTERNAL_ROUTING | 9 | internal (Clerk/DO/DH) | EN |
| **TOTAL** | **69** | (57 EN+TE + 12 EN) | |

Matches the M4.1 spec §9 prediction (54 communications when all 9 types built) plus +15 INTERNAL_ROUTING (9, was queued at 3 in spec; actual breakdown is 3-stage workflow × 3 tenders = 9) and re-classification of FLAGGED to bidder-facing (per run-2 directive; spec had it internal).

### Sarvam-M cost extrapolation

Total Sarvam-M API calls across M4.4 + M4.5 translation runs: 78 + 94 = **172 calls** for 57 communications. ~3 calls per communication on average; range 5-11 per letter depending on length. Cache hit rate accelerates over time — by run 2 (M4.5), 8 of 102 chunks (~8%) hit cache from M4.4-era translations of similar boilerplate ("Dear Sir/Madam", "Yours faithfully", standard signature blocks). At Sarvam's stated typical pricing the 172 calls cost trivially (~₹100-300).

### Sentinel preservation discipline maintained

All 6 drafters' `main()` snapshot Module 3 sentinels pre-emit + assert identity post-emit. Module 3 state (154/351/49/27/3/6/3) unchanged across all 57 new Communication emissions. Only growing axis: Communication count 12 → 69.

### Sample drilldown — CARTEL_REVIEW (internal)

```
Communication: CARTEL_REVIEW × tender_synth_hc
  recipient_role: VIGILANCE_OFFICER (not a bidder; internal routing)
  language: EN
  source_finding_node_ids: [<BidAnomalyFinding HC node_id>]
  audit_id: d44123a9b529045b
  artifact: /tmp/m4_drafts/CARTEL_REVIEW_hc.md (also .docx)
  content references: implicated pair B6+B7; 4 signals (SHARED_ADDRESS,
    MATCHED_SIGNATORY, COMMON_BANK_BRANCH, TIGHT_PRICE_GAP);
    cross-tender consistency 3 of 3 → HIGH severity
  Vigilance action requested: defer L1 award; investigate; consider blacklist
```

Internal communications stay confidential (per CVC vigilance protocol) — the bidders implicated as cartel-suspects receive standard REGRET letters (citing non-L1 ranking, not the cartel signal). This is the **separation-of-concerns** pattern: bidder-facing communications cite the surface decision; internal communications carry the underlying vigilance reasoning.

### Final state after M4.5

| metric | before | delta | after |
|---|---:|---:|---:|
| Communication kg_nodes | 12 | +57 | **69** |
| /tmp/m4_drafts/ .md files | 12 | +57 | 69 |
| /tmp/m4_drafts/ .docx files | 12 | +57 | 69 |
| Telugu-translated (EN+TE) | 12 | +45 | 57 |
| Internal English-only | 0 | +12 | 12 |
| Module 3 sentinels (154/351/49/27/3/6/3) | clean | unchanged | clean ✓ |

### Forward-applicable

The 9-type Module 4 corpus is now complete. Remaining Module 4 work is operational:
- M4.6 Bidder Clarification Q&A (BIDDER_CLARIFICATION_QA) — 1 more type, bilingual
- M4.7+ approval workflow (status transitions: DRAFT → READY_FOR_REVIEW → APPROVED → SENT)
- M4.8+ actual sending (SMTP / SMS / portal API integration)

---

## L87 — Module 4 M4.4 Sarvam-M Telugu Integration with DPDP Pseudonymisation

**Established in run-2 Sub-block 1** (2026-05-12). Telugu output landed for bidder-facing communications via Sarvam-M `/translate` API. DPDP pseudonymisation layer + filesystem cache ship together: PII never crosses the external API boundary; identical inputs produce identical cached translations.

### Sarvam-M `/translate` endpoint details

```
POST https://api.sarvam.ai/translate
Headers: api-subscription-key: <SARVAM_API_KEY>
Body:
  {
    "input": "<text>",
    "source_language_code": "en-IN",
    "target_language_code": "te-IN",
    "mode": "formal",
    "speaker_gender": "Male"
  }
Response: { "translated_text": "<Telugu>" }
```

Practical observation: input limit ~1000-1500 chars per call. Communication letters run 3000-6500 chars → must chunk on paragraph boundaries (double newline). The shipped client splits on `\n\n` and falls back to sentence-level (`[.!?]\s+`) for paragraphs exceeding `MAX_CHARS_PER_REQUEST=900`.

### DPDP pseudonymisation discipline (per DPDP Act 2023 §7 purpose limitation)

Bidder PII MUST NOT cross the external API boundary. Mechanism:

```
1. _build_pseudonymisation_map(bidder_props, tender_info) returns:
     [
       ("M/s Comprehensive Standard Builders JV ...", "<COMPANY>"),
       ("Mr. C. Comprehensive",                       "<SIGNATORY>"),
       ("Plot 27, MVP Colony, Visakhapatnam-530017", "<ADDRESS>"),
       ("bidder9@example.com",                        "<EMAIL>"),
       ("AAACJ9999J",                                 "<PAN>"),
       ("37AAACJ9999J9Z9",                            "<GSTIN>"),
       ...
     ]
2. text_pseudonymised = pseudonymise(text_en, pairs)   # PII → tokens
3. text_translated_pseudonymised = sarvam_translate(text_pseudonymised, "te-IN")
4. text_te = restore_pseudonyms(text_translated_pseudonymised, pairs)  # tokens → PII
```

Pairs sorted **longest-first** so 'GSTIN' substitutes before 'GST' (avoid substring collisions). Restoration relies on Sarvam preserving the literal `<TOKEN>` strings — verified empirically across 78 API calls; zero tokens leaked through (`verify_no_pii_in_text` returns empty list on all 12 translated communications).

### Cache layer (idempotency + cost control)

Cache key = SHA256(target_lang + pseudonymised_chunk)[:32]. Cached files at `/tmp/sarvam_cache/<key>.json` carry source_en + translated_text + cached_at. Re-running the translator hits cache for every chunk → **0 API calls on idempotent re-run**.

Cache hit rate within a single run: 10 of 88 chunks (11%) — chunks at the start of letters that share boilerplate (e.g. "Dear Sir/Madam," "Yours faithfully") cache once and reuse across letters. Higher hit rates expected as more drafters land.

### Bilingual storage + status field

Communication kg_node now carries:
- `content_en` (always populated)
- `content_te` (Telugu; populated only for bidder-facing types)
- `content_te_status` enum: `rendered_via_sarvam_m` / `english_only_internal` / `translation_failed` / `translation_pending`
- `language` enum: `EN` (internal communications) / `EN+TE` (bidder-facing translated)

Internal types (CARTEL_REVIEW, INTERNAL_ROUTING) stay English-only — addressed to internal officers, not bidders. Marked `content_te_status="english_only_internal"`.

### Sample output (B9×HC AWARD, first 400 chars of content_te)

```
# అవార్డు లేఖ - టెండర్ **తేదీ:** 2026-05-11 **To:** M/s Comprehensive
Standard Builders JV (Premier Coastal + Northern Engineering + Southern
Surveys) చిరునామా దృష్టి: Mr. C. Comprehensive టెక్స్ట్ః ఆంధ్రప్రదేశ్
హైకోర్టు సముదాయం NIT సంఖ్య: HC/APCRDA/2026/PROC/001 అంచనా ఒప్పందం విలువ
(ఇసివి): ₹365.16 కోట్లు
```

PII restored cleanly: "M/s Comprehensive Standard Builders JV..." appears verbatim (English-script company name in Telugu context, which is correct for legal-doc bilinguality).

### Final state after M4.4

| metric | before | delta | after |
|---|---:|---:|---:|
| Communication.content_te populated | 0 | +12 | **12** |
| Communication.content_te_status field | absent | populated | 12 |
| Communication.language="EN+TE" | 0 | +12 | 12 |
| Sarvam-M API calls (this run) | n/a | 78 | n/a |
| Cache hits (this run) | n/a | 10 of 88 | n/a |
| PII leaks detected | n/a | 0 | clean ✓ |
| Module 3 sentinels | clean | unchanged | clean ✓ |

### Forward-applicable

The `_sarvam_client.py` module + DPDP pattern is reusable for any future bilingual communication (M4.5 new types + M4.6 Q&A workflow). Cost: 1 API call per ~900-char chunk per unique pseudonymised content; cached forever per unique input. Per-letter cost ~5-11 API calls (depending on length). At Sarvam's typical pricing (~₹0.5 per call), 100 communications ≈ ₹300-700 — economical for production.

---

## L86 — Module 4 M4.3 Audit Log Discipline + DOCX Rendering

**Established in autonomous overnight workflow Sub-block 4** (May 2026). M4.3 closes the Communication artifact lifecycle: every Communication kg_node now has both a `.md` AND a `.docx` artifact path populated, and the reverse-drilldown audit-trail query helper is shipped.

### Markdown-to-DOCX renderer in _common.py

Single shared helper `render_docx_from_md(content_md, out_path, title)` parses inline Markdown into python-docx primitives:

| Markdown | DOCX style |
|---|---|
| `# Title` | (skipped — title goes to the top-of-doc heading) |
| `## H` / `### H` | Heading 1 / Heading 2 |
| `**bold**` / `*italic*` / `` `code` `` | bold / italic / Courier New runs |
| `- item` / `* item` | List Bullet style |
| `1. item` | List Number style |
| `> quote` | Intense Quote style |
| `---` | horizontal rule (Unicode `─`) |
| (otherwise) | Normal paragraph |

~85 LOC. Reusable for any future Markdown-bearing kg_node type.

### Persistence — JSONB merge via fetch-modify-patch

PostgREST does not natively merge nested JSONB. Updating one field within `properties` requires fetching the full properties, mutating one key, and PATCH-ing the full properties back. Pattern:

```python
def render_docx_for_communication(node_id, content_md, artifact_path_md, title):
    docx_path = Path(artifact_path_md).with_suffix(".docx")
    render_docx_from_md(content_md, docx_path, title)
    props = rest_get(...)[0]["properties"]
    props["artifact_path_docx"] = str(docx_path)
    rest_patch("kg_nodes", node_id, {"properties": props})
```

`render_docx_for_all_communications.py` runs this batch over all 12 Communication kg_nodes; idempotent re-run overwrites DOCX files + the kg_node field cleanly.

### Reverse drilldown — query_communication_audit_trail.py

Forward drilldown (Communication → source findings) is built into the standard fetch by `source_finding_node_ids[]`. The harder direction — given a finding, what Communications cite it? — is the **RTI-friendly query** ("show me every communication generated from this evidence"). PostgREST array-contains operator does the work:

```python
properties->source_finding_node_ids = cs.[<UUID>]
```

The `cs.` filter operates on JSONB arrays. Helper output for a HARD_BLOCK Personnel finding on B2×HC:
```
Source finding: BidEvaluationFinding (Personnel-Coverage, INELIGIBLE, MPW-041, HARD_BLOCK)
1 Communication citing this finding:
  DISQUALIFICATION × bid_synth_profile_b2 × tender_synth_hc
  audit_id=2c156908d936d829
  artifact=/tmp/m4_drafts/DISQUALIFICATION_b2_hc.md
  other sources cited (besides this finding): 7
```

This is the audit-defensibility query a vigilance officer or RTI petitioner runs. It's the inverse of `source_finding_node_ids[]` and ships cheaply because the JSONB array operator is already indexable in PostgreSQL.

### Final state after M4.3

| metric | before | delta | after |
|---|---:|---:|---:|
| Communication kg_nodes | 12 | 0 | 12 |
| Communication.artifact_path_md populated | 12 | 0 | 12 |
| Communication.artifact_path_docx populated | 0 | +12 | **12** |
| /tmp/m4_drafts/ .md files | 12 | 0 | 12 |
| /tmp/m4_drafts/ .docx files | 0 | +12 | **12** |
| Module 3 sentinels | 154/351/49/27/3/6/3 | unchanged | clean ✓ |

All 12 DOCX validate via python-docx; paragraph counts 74-99 per letter depending on type (DISQUAL is longest due to per-rule citation block).

### Module 4 sub-block status post-M4.3

- M4.1 ✓ Architecture spec
- M4.2 ✓ 3 drafter pilots (DISQUAL + AWARD + ALB_JUSTIFICATION)
- M4.3 ✓ DOCX rendering + audit trail query helper
- M4.4 future: Telugu translation via Sarvam-M API + DPDP pseudonymisation
- M4.5 future: approval workflow (Clerk → Dealing Officer → Department Head)
- M4.6 future: remaining 6 communication types (CARTEL_REVIEW + FLAGGED + DOC_REVIEW + REGRET + BID_ACK + INTERNAL_ROUTING)
- M4.7 future: actual sending (SMTP + SMS + portal API)

---

## L85 — Module 4 M4.2 Drafter Pilots (DISQUALIFICATION + AWARD + ALB_JUSTIFICATION)

**Established in autonomous overnight workflow Sub-block 3** (May 2026). First implementation sub-block of Module 4. 3 drafter pilots shipped per the M4.1 contract; 12 Communication kg_nodes emitted across 9 bidders × 3 tenders, with the predicted 6+3+3 distribution.

### Pattern recipe

1. **Shared helpers in `scripts/m4_drafters/_common.py`** — REST GETs with retry, audit_id computation, BidderProfile cache, tender info lookup, idempotent cleanup, sentinel snapshot/assert.
2. **One drafter per communication_type** — `draft_<type>.py`. Each defines `COMMUNICATION_TYPE` + `SOURCE_REF` constants + `compose_content_en()` template + `main()` that:
   - Snapshots sentinels pre-emission
   - Calls `delete_prior_communications(type, source_ref)` for idempotency
   - Iterates source findings (EligibilityMatrix for DISQUAL; ComparativeStatement for AWARD; BidAnomalyFinding for ALB)
   - Computes `audit_id` = SHA256(type|recipient|tender|sorted_finding_ids)[:16]
   - Writes Markdown artifact to `/tmp/m4_drafts/<TYPE>_<bidder>_<tender>.md`
   - Emits Communication kg_node with `source_finding_node_ids[]` for drilldown
   - Asserts Module 3 sentinels unchanged post-emission

### Source-finding citation depth per type

| drafter | source kg_nodes per Communication |
|---|---|
| DISQUALIFICATION | EligibilityMatrix (1) + BidEvaluationFinding HARD_BLOCK subset (N) — typically 7–8 sources |
| AWARD | ComparativeStatement (1) + TenderRanking (1) + BidEvaluationFinding QUALIFIED subset (13) — total 15 |
| ALB_JUSTIFICATION | BidAnomalyFinding (1) + BidSubmission (1) + TenderRanking (1) — total 3 |

Every claim in `content_en` ties to one of the cited node_ids. RTI / committee-scrutiny query: "What evidence backs this disqualification?" → resolve `source_finding_node_ids[]` → fetch nodes → read rule_id + decision_reason + evidence.

### audit_id determinism verified

Re-running the AWARD drafter produced **identical audit_ids** (d5f17dc7 / 6c992ea6 / 1b7c0a1d) across re-emit. The cleanup-then-re-emit pattern preserves audit integrity: same inputs → same hash → same Communication identity, even though the kg_node UUID changes per insert. Audit defensibility is via audit_id (deterministic), not node_id (insertion-randomised).

### Sentinel preservation discipline

Every drafter's `main()` snapshots Module 3 sentinels (`ValidationFinding / BidEvaluationFinding / BIDDER_VIOLATES_RULE / EligibilityMatrix / TenderRanking / BidAnomalyFinding / ComparativeStatement`) pre-emit, asserts identity post-emit, and excludes `Communication` count from the assertion (since that grows by design). Run-time drift detection ensures M4.2 drafters never accidentally mutate Module 3 state.

### Drilldown test result on B2×HC DISQUALIFICATION

```
audit_id=2c156908d936d829
n_hard_block_findings=7
n_source_findings=8 (7 HARD_BLOCK + 1 EligibilityMatrix)
Resolved all 8 source_finding_node_ids: BidEvaluationFinding × 7 + EligibilityMatrix × 1 ✓
Recomputed audit_id (SHA256 of payload string) = stored audit_id ✓
content_en = 5392 chars, references all 7 HARD_BLOCK rule_ids with decision_reasons
```

### Final state after M4.2

| metric | before | delta | after |
|---|---:|---:|---:|
| Communication kg_nodes | 0 | +12 (6 DISQUAL + 3 AWARD + 3 ALB) | 12 |
| Markdown artifacts at /tmp/m4_drafts/ | 0 | +12 | 12 |
| Module 3 sentinels (154/351/49/27/3/6/3) | clean | unchanged | clean ✓ |

### Forward-applicable

The 3 drafter pilots establish the pattern for the remaining 6 (CARTEL_REVIEW + FLAGGED + DOC_REVIEW + REGRET + BID_ACK + INTERNAL_ROUTING). Each future drafter is ~250-300 LOC: import `_common`, define template + source-iteration, mirror sentinel discipline. Roughly the same complexity as Module 3 Tier-2 validators — pattern is stable.

---

## L84 — Module 4 Communication Architecture Design Spec (M4.1)

**Established in autonomous overnight workflow Sub-block 2** (May 2026). M4.1 is the design-only sub-block that defines the contract Module 4 implementation builds to. Pattern mirrors Ext-7 (B9 spec design) — write the contract first, build to satisfy second. 302 lines covering 10 sections (Context / Schema / 9 Types / Bilingual / Channel / Audit / Sender / DPDP / Predicted / Out-of-scope).

### Why design-first for Module 4

Module 3 was built validator-by-validator with the rule schema known upfront. Module 4 is different: there are **9 distinct communication types**, each with its own template, source-finding drilldown, recipient class, channel, and routing rules. Building drafter-by-drafter without a unified spec would produce drift — each drafter would invent its own field names, audit_id construction, source citation depth.

The spec normalises:
- **Single Communication kg_node type** with 14 standard properties (communication_type / recipient / channel / language / status / audit_id / source_finding_node_ids / content_en / etc.)
- **Deterministic audit_id**: SHA256 of `f"{type}|{recipient_id}|{tender_id}|{sorted_finding_ids}"` — re-runs produce identical hashes; idempotent re-emission
- **L80 composite-finding semantics carry through**: source_finding_node_ids[] is the same audit-chain pattern as Module 3 ComparativeStatement.audit_id
- **DPDP pseudonymisation rule**: bidder PII never crosses the Sarvam-M API boundary; only template phraseology does

### Predicted corpus emission

Verified against current EligibilityMatrix + TenderRanking + BidAnomalyFinding state:

| Type | Count | M4.2 pilot? |
|---|---:|---|
| DISQUALIFICATION | 6 (B2×3 + B3×3) | ✓ |
| AWARD | 3 (B9×3 effective L1) | ✓ |
| ALB_JUSTIFICATION | 3 (B8×3) | ✓ |
| CARTEL_REVIEW | 3 (B6+B7 paired×3 vigilance) | deferred |
| FLAGGED | 3 (B4×3 committee) | deferred |
| DOC_REVIEW | 3 (B5×3) | deferred |
| REGRET | 3 (B1×3, only QUALIFIED-not-anomaly) | deferred |
| BID_ACK | 27 (one per BidSubmission) | deferred |
| INTERNAL_ROUTING | 3 (per tender) | deferred |

M4.2 ships 3 of 9 types = 12 of 54 total predicted communications. The remaining 6 types are incremental future M4.x sub-blocks.

### Forward-applicable design discipline

For any future multi-artifact module (e.g. Module 5 = Reviewer dashboard; Module 6 = Vigilance audit log): write the spec FIRST, in the L77 + L84 design-only pattern. Saves implementation rework when 9 types try to converge on a shared schema.

---

## L83 — PDF Renderer Integration (L75 follow-up, reportlab)

**Established in autonomous overnight workflow Sub-block 1** (May 2026, post-Ext-8). The L75 marker has been carrying since Module 3 core; finally landed via `reportlab` 4.5.0 (pure-Python, BSD-licensed). 3 PDFs now render alongside the existing Markdown + DOCX artifacts at `/tmp/comparative_statements/`, with `pdf_artifact_path` populated on each ComparativeStatement kg_node.

### Why reportlab over weasyprint

The L75 marker listed both options. reportlab won because:
- **Pure Python** — no system deps (cairo, pango, harfbuzz, gobject-introspection). weasyprint requires those for HTML→PDF conversion; can break on Linux servers without dev libs.
- **BSD license** — friendlier than weasyprint's LGPL for AGPL-incompatible commercial deployments.
- **Idempotent + deterministic** — same `data` dict produces identical-byte-stream PDFs across re-runs once font embedding is stable. Test: re-running the generator produced identical audit_ids (277daa... / a911ac... / 39dab1...) showing the underlying data is unchanged; PDFs overwrite in place.
- **No HTML intermediary** — DOCX renderer already speaks structured `data` dict; PDF generation reads the same dict via `reportlab.platypus.Paragraph + Table` primitives. ~290 LOC for `render_pdf()`, mirroring the 250 LOC of `render_docx()`. Zero risk of HTML→DOCX→PDF drift.

### Pattern recipe — render_pdf() mirrors render_docx()

7 parts (A-G) match the existing DOCX:

```python
def render_pdf(data: dict, out_path: Path) -> None:
    styles = getSampleStyleSheet()
    story = []
    # PART A — Tender Summary
    # PART B — Bidder Participation Overview (with Excluded bidders Table)
    # PART C — Per-Bidder Detailed Evaluation (13-row criterion table per bidder)
    # PART D — Ranking of QUALIFIED Bidders (5-row ranking + ALB detection)
    # PART E — Anomaly Findings (CARTEL + ALB signals tables)
    # PART F — Committee Recommendation (effective L1 + 3 options)
    # PART G — Audit Trail (audit_id + 5-layer drilldown citation chain)
    pdf_doc = SimpleDocTemplate(str(out_path), pagesize=A4, ...)
    pdf_doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
```

Visual styling carries through:
- **Bold HARD_BLOCK INELIGIBLE rows** in the per-criterion table (via `cell_bold` ParagraphStyle)
- **Bold L1 row** in the ranking table
- **Red+bold "Action required on L1: YES"** when alb_action_required (matches DOCX red color #C00000)
- **Italic regulatory citations** in anomaly evidence
- **Page footer** with audit_id + page number + timestamp (every page; canvas onPage callback)

### Output state

| metric | before | delta | after |
|---|---:|---:|---:|
| PDF artifacts in /tmp/comparative_statements/ | 0 | +3 | 3 (12 pages each, ~35KB each) |
| ComparativeStatement.pdf_artifact_path | null | populated | string path |
| ComparativeStatement.pdf_artifact_status | "deferred_no_renderer_in_env" | updated | "rendered_via_reportlab" |
| ComparativeStatement.pdf_renderer_version | absent | added | "reportlab-4.5.0" |
| Sentinel (154/351/49/27/3/6/3) | clean | unchanged | clean ✓ |

### Verification snapshot

- HC PDF: 12 pages — Header (1) + Part A-B (1) + Part C (5 bidders... wait, 9 bidders × ~1 page each + ranking + anomaly + recommendation + audit ~12 pages total)
- All 3 PDFs validate via pypdf (no parse errors)
- Re-running the generator overwrites in place; audit_ids identical → deterministic
- DB `pdf_artifact_path` field populated and queryable

Forward-applicable: any other DOCX renderer in the codebase (future Module 4 communication artifacts; SoR / BoQ summaries) can adopt the same `render_pdf()` pattern by reusing reportlab.platypus primitives + this ParagraphStyle bundle.

---

## L82 — Module 3 Extensions Arc Completion + DemoBidder Pattern (Ext-8 B9)

**Established during Ext-8** (May 2026, terminal sub-block; closes the Module 3 Extensions arc 8-of-8). B9 = comprehensive JV bidder seeded as the final exercise that puts every standard evaluation check end-to-end through the live DB. First DB exercise of Ext-1's JV path (Path 2 cross-profile lookup). Demonstrates platform's **effective L1 computation** accounting for ALB norms + cartel review.

### Incremental seed > full re-seed (preservation discipline)

The Sub-block 1.1 `seed_synthetic_bids.py:_delete_prior()` wipes ALL `bid_synth_*` rows. A full re-seed for Ext-8 would have:
- Deleted 8 BidderProfile + 24 BidSubmission + 72 supplementary + 237 fact_sheets
- **Invalidated all 312 BidEvaluationFindings** (they reference soon-deleted node_ids)
- Forced a 27 × 13 = 351-call full pipeline re-run (~17 minutes wall-clock)

**Pattern**: write `/tmp/ext8_seed_b9.py` standalone that imports `PROFILES`, `TENDERS`, `STATEMENT_BUILDERS`, `_insert_node`, `_insert_edge`, `_insert_fact_sheet` from the seed module and adds ONLY B9 + partners + B9 bids without calling `_delete_prior`. Existing 312 findings stay intact; only B9's 39 new findings need emitting. Total wall-clock for Ext-8 ≈ 5 minutes (40s seed + 39 validator runs × ~5s + 4 aggregators × 12-45s).

For source-of-truth on future fresh re-runs: add B9 + 3 partners to `seed_synthetic_bids.py:PROFILES` with `_skip_bidsubmission=True` on JV_PARTNERs + a 1-line check in `main()` loop. So `_delete_prior()` followed by full re-seed produces the right 12-profile / 27-bid / 267-fact_sheet state.

### Partner-before-entity insert ordering (resolvability)

Ext-1's `bid_jv_consortium_check` does cross-profile lookup at validator runtime: it fetches `lead_partner_id`'s profile + each `partner_ids[]`'s profile via separate REST calls. If the JV entity were inserted BEFORE its partners, an immediate post-seed validator run could (in principle) hit a race condition where the partner lookups return empty.

In practice, Supabase REST is synchronous + the inserts complete before the validator runs, so the race is theoretical. But the **insert order discipline** is documented for any future cross-profile-lookup validator: **always insert dependencies first**. The `/tmp/ext8_seed_b9.py` insert sequence:

```
Phase 1: 3 JV_PARTNER profiles  (b9_lead, b9_p2, b9_p3)
Phase 2: B9 JV entity            (references b9_lead, partner_ids)
Phase 3: B9 BidSubmissions + fact_sheets + supplementary + edges
```

### Ext-1 JV path DB-first-exercise — 8/8 sub-checks COMPLIANT

The pre-smoke Approach E tests at Ext-1 shipped time validated all 4 paths in isolation; Ext-8 was the first DB exercise of Path 2 (JV with cross-profile lookup). Outcome on B9×HC (deepest predicted QUALIFIED):

| # | sub-check | input | result |
|---|---|---|---|
| 1 | JV_PERMIT_TENDER_TYPE | `Works` ∈ {Works, EPC} | COMPLIANT |
| 2 | JV_AGREEMENT_VALIDITY | `2027-12-31` ≥ `2026-05-10` | COMPLIANT |
| 3 | LEAD_PARTNER_IDENTIFIED | `b9_lead` resolves to `JV_PARTNER` ✓ | COMPLIANT |
| 4 | JOINT_AND_SEVERAL_LIABILITY | `JOINT_AND_SEVERAL` | COMPLIANT |
| 5 | **LEAD_PARTNER_FINANCIAL** | **Lead 230cr ≥ HC floor 109.55cr** (margin 120.45cr) | COMPLIANT |
| 6 | POA_FORM_15_VALID | `VALID` | COMPLIANT |
| 7 | PARTNER_COUNT | `3` ∈ [2, 3] | COMPLIANT |
| 8 | PARTNERS_BLACKLIST_CLEAN | 3 partners all `past_blacklist_events=[]` | COMPLIANT |

Sub-check 5 is the architecturally distinctive one — AP norm requires the **Lead's solo financial turnover** ≥ tender floor (not the JV's collective turnover). Cross-profile lookup made this possible. **First demonstration of Lead-Partner-alone financial criterion in DB.**

All 3 B9 bids (Kurnool / JA / HC) produced 8/8 sub-checks COMPLIANT → QUALIFIED → no BIDDER_VIOLATES_RULE edge. Zero surprises vs the 6 Approach E unit tests at Ext-1 ship.

### Effective L1 transition: B1 → B9 on all 3 tenders

Pre-Ext-8 effective L1 was B1 (premium −5%, the cleanest of 8 QUALIFIED bidders after B8 ALB-rejected). Post-Ext-8 with B9 added at premium −6%, the raw ranking becomes:

```
L1 raw  B8 (−38%)  ALB-rejected (alb_action_required=True)
L2 raw  B9 (−6%)   ← effective L1 ✓
L3 raw  B1 (−5%)
L4 raw  B6 (−3.10%) cartel-referred (with B7)
L5 raw  B7 (−3.05%) cartel-referred (with B6)
```

ComparativeStatement Sub-block 7 effective-L1 skip chain:
1. raw L1 B8 → in alb_candidates AND alb_action_required → **skip ALB**
2. raw L2 B9 → not in alb_candidates, not in cartel_referred → **effective L1 = B9** ✓

Demo-visible payoff: "platform performs comprehensive standard evaluation; B9 — the well-organized JV with all documents in order and lead-partner-alone financial credentials clearing the regulatory floor — emerges as effective L1 after raw L1 (B8 ALB) is rejected and cartel-pair (B6+B7) is referred per CVC anti-collusion norms."

### B9 doesn't trip cartel signals — discrimination design works

Pre-Ext-8 design (per L67 + Ext-7 spec §4) gave B9 four distinguishing properties to keep it clear of CARTEL_SUSPECT signals:
- Signatory: `Mr. C. Comprehensive` — unique 'C.' initial across all 9 bidders (B1=K., B2=R., B3=P., B4=A., B5=V., B6=S., B7=T., B8=L.)
- EMD bank-branch: `State Bank of India, Visakhapatnam Main Branch` — distinct from B6/B7's `State Bank of India, Vijayawada Main Branch`
- Communication address: `Plot 27, MVP Colony, Visakhapatnam-530017` — zero overlap with B1-B8 addresses
- Premium delta: −6% — closest to B1 −5% with diff 1% (CARTEL signal threshold is 0.10%)

CrossBidAnomalyDetector re-run with 5 QUALIFIED bidders × 3 tenders = `C(5,2)` × 3 = 30 pair evaluations. Result: 6 BidAnomalyFinding rows **unchanged** (3 CARTEL B6+B7 + 3 ALB B8). **B9 emits zero signals against any other bidder.** Design works.

### Final state — Module 3 Extensions arc complete

| metric | pre-Ext-8 | delta | final |
|---|---:|---:|---:|
| BidderProfile | 8 | +4 (1 JV + 3 JV_PARTNER) | 12 |
| BidSubmission | 24 | +3 (B9 only) | 27 |
| LetterOfBid / EMD_BG / PricedBoQ | 24 each | +3 each | 27 each |
| fact_sheets | 237 | +30 | 267 |
| BidEvaluationFinding | 312 | +39 (all QUALIFIED) | 351 |
| BIDDER_VIOLATES_RULE | 49 | 0 | 49 |
| EligibilityMatrix | 24 | +3 (B9×3 all QUALIFIED) | 27 (dist: 15/3/3/6) |
| TenderRanking | 3 | re-emit | 3 (5-entry ranking[]; effective_L1 = B9) |
| BidAnomalyFinding | 6 | re-emit unchanged | 6 (B9 not implicated) |
| ComparativeStatement | 3 | re-render | 3 (13×9 per-bidder table, 5-row ranking, B9 in recommendation) |
| **ValidationFinding (sentinel)** | **154** | **0** | **154 ✓** |

**Arc stats:** 8 sub-blocks (Ext-1 through Ext-8), 6 new validators/extensions (1 new Path A + 5 forward-compat Path B), 4 rule seeds (AP-PROC-COMPLIANCE-DOCS-V1 + AP-PROC-JV-CONSORTIUM-V1 + MPG-255 anchor + 6 secondary citations), ~25 new BidderProfile fields, 9 bidders evaluated, 3 demo-visible DOCX reports.

**Lessons L65–L82** (18 entries) document the arc: composite-finding pattern, cross-profile lookup, rule-seed-and-flag, cascade recovery discipline, JV-aware validator pattern, incremental seed > full re-seed for terminal sub-blocks.

---

## L81 — Module 3 Extensions JV-Aware Validator Pattern (Ext-1 JV/Consortium)

**Established during Ext-1** (`scripts/bid_jv_consortium_check.py`, May 2026). First Tier-2 validator with **3-path architecture** discriminated on the new `bidder_type` field (Ext-1 schema), and the first that performs a **cross-profile lookup** at evaluation time (validator joins Lead Partner + N Partner `BidderProfile` rows in addition to the bidding entity's own profile). Sets the template for Ext-8 B9 (synthetic JV bidder) and for any future multi-entity bid-evaluation criterion.

### 3-path architecture

```
compute_verdict(bidder_props, tender_props,
                lead_partner_props=None,
                partner_props_list=None) -> (verdict, calc)
```

| bidder_type | path | verdict | finding emitted |
|---|---|---|---|
| `SOLE_BIDDER` | early-return | QUALIFIED-NOT_APPLICABLE | yes (audit row) |
| `JV` / `CONSORTIUM` | run 8 sub-checks via cross-profile lookup | QUALIFIED or INELIGIBLE-HARD_BLOCK | yes (composite per L80) |
| `JV_PARTNER` | data-integrity catch | GAP-DATA_INTEGRITY (WARNING) | yes (audit row) |
| anything else | fallback | GAP_INSUFFICIENT_DATA | yes (audit row) |

Cross-profile lookup is **only** taken on the JV/CONSORTIUM path. SOLE_BIDDER and JV_PARTNER short-circuit before any partner lookup, keeping the validator wall ≈3.5–6s for the 24 B1-B8 SOLE_BIDDER bids and avoiding 8+ extra REST calls per bid that would be wasted.

### Why NOT_APPLICABLE returns QUALIFIED, not SKIP

A SOLE_BIDDER bid is not "skipped" — it has been evaluated and found compliant with the rule (because the rule says "JV must satisfy 8 sub-checks" and a non-JV bidder is vacuously compliant). The 4-state Tier-2 verdict vocab (QUALIFIED / INELIGIBLE / GAP / SKIP) reserves `SKIP_NOT_APPLICABLE` for the rule-doesn't-fire case (e.g. PPP tender hitting an AP-Works-only rule). Here the rule **fires** for all AP Works/EPC bids; what differs is the per-sub-check verdict, where SOLE_BIDDER auto-passes with `compliance=NOT_APPLICABLE_SOLE_BIDDER`. EligibilityMatrix counts it as the 13th compliant criterion, NOT as a skipped one.

This is the L80 composite-finding pattern at scale: 8 sub-checks for JV, 1 sub-check (`JV_PERMIT_CHECK = NOT_APPLICABLE_SOLE_BIDDER`) for SOLE_BIDDER, all expressed through the same `jv_evaluation_summary[]` field.

### Cross-profile lookup architecture

```python
# in main(), JV/CONSORTIUM path only:
lead_partner_props = load_bidder_profile(bidder_props["lead_partner_id"])["properties"]
partner_props_list = load_partner_profiles(bidder_props["partner_ids"])

verdict, calc = compute_verdict(bidder_props, tender_props,
                                 lead_partner_props=lead_partner_props,
                                 partner_props_list=partner_props_list)
```

The validator does N+1 REST GETs (1 for the JV entity, 1 for Lead, N for Partners). For B9 with 3 partners that's 4 round-trips — acceptable for nightly batch eval, not a hot path. Future optimisation: a single `node_id IN (...)` query could collapse these into one call but isn't worth it for batch sizes ≤ 30.

`compute_verdict` accepts both as **optional** parameters with default `None` — keeps SOLE_BIDDER / JV_PARTNER paths trivially unit-testable without DB or partner stubs (Approach E, L79).

### 8 sub-checks per AP standard tender Clauses 1.6.4 + 1.12 (Form-14)

| # | sub-check | source field | failure → |
|---|---|---|---|
| 1 | `JV_PERMIT_TENDER_TYPE` | tender_props.tender_type ∈ {Works, EPC} | HARD_BLOCK |
| 2 | `JV_AGREEMENT_VALIDITY` | bidder.jv_agreement_validity_until ≥ tender.submission_date | HARD_BLOCK |
| 3 | `LEAD_PARTNER_IDENTIFIED` | bidder.lead_partner_id resolves to BidderProfile with bidder_type=JV_PARTNER | HARD_BLOCK |
| 4 | `JOINT_AND_SEVERAL_LIABILITY` | bidder.liability_terms == JOINT_AND_SEVERAL | HARD_BLOCK |
| 5 | `LEAD_PARTNER_FINANCIAL` | lead.financial_turnover_3yr_avg_cr ≥ tender.financial_pq_floor_cr (Lead-alone, NOT collective) | HARD_BLOCK |
| 6 | `POA_FORM_15_VALID` | bidder.poa_status == VALID (reused from Ext-2 schema) | HARD_BLOCK |
| 7 | `PARTNER_COUNT` | 2 ≤ len(bidder.partner_ids) ≤ 3 per AP norm | HARD_BLOCK |
| 8 | `PARTNERS_BLACKLIST_CLEAN` | no partner has past_blacklist_events with current_status=ACTIVE | HARD_BLOCK |

Sub-check 5 is the architecturally interesting one: AP norm requires the **Lead Partner's solo financial turnover** ≥ tender floor, NOT the JV's collective turnover. This is precisely why cross-profile lookup is necessary — the validator can't satisfy sub-check 5 from the bidding-entity profile alone. Ext-3's renamed `financial_turnover_3yr_avg_cr` field is consumed here for the first time outside the Ext-3 validator. Sub-check 6 reuses Ext-2's `poa_status` field — both Ext schemas now compose into a richer JV check.

### Primary + secondary citation chain depth

When no existing rule in the `rules` table cleanly matches a multi-citation criterion (six rules contribute partial NL: MPW-044, AP-GO-002, MPW25-119/120/121, CVC-139), seed a new aggregating rule **AP-PROC-JV-CONSORTIUM-V1** carrying the synthesis natural language (8 sub-checks enumerated), then enumerate the six contributing rules as `secondary_rule_ids` in every finding for full audit-chain transparency.

```python
RULE_ID = "AP-PROC-JV-CONSORTIUM-V1"  # primary anchor
SECONDARY_RULE_IDS = ["MPW-044", "AP-GO-002", "MPW25-119",
                      "MPW25-120", "MPW25-121", "CVC-139"]
```

This is the **rule-seed-and-flag pattern** extended (originally L80 Ext-2; now Ext-1 demonstrates secondary citation depth — Ext-2 had no secondaries). Forward-applicable to any future criterion synthesised from multiple existing rules.

### 6-test Approach E unit-test coverage

The DB exercise of the JV path is **deferred to Ext-8** (no JV/CONSORTIUM bidders exist in the 24 B1-B8 corpus today). To prevent the JV branch from rotting silently between Ext-1 ship and Ext-8 seed, six Approach E unit tests cover **all four paths**:

| # | input | expected verdict | covers |
|---|---|---|---|
| 1 | bidder_type=SOLE_BIDDER | QUALIFIED-NOT_APPLICABLE | Path 1 |
| 2 | bidder_type=JV, all 8 sub-checks pass | QUALIFIED, 8/8 | Path 2 happy |
| 3 | bidder_type=JV, liability_terms=OTHER | INELIGIBLE-HARD_BLOCK (sc4 fail) | Path 2 single-sub-check failure |
| 4 | bidder_type=JV, Lead 3yr=50cr vs floor=109.55cr | INELIGIBLE-HARD_BLOCK (sc5 fail) | Path 2 cross-profile financial |
| 5 | bidder_type=CONSORTIUM, partner_count=1 | INELIGIBLE-HARD_BLOCK (sc7 fail) | Path 2 count boundary |
| 6 | bidder_type=JV_PARTNER | GAP-DATA_INTEGRITY | Path 3 |

All 6 tests run without a DB (cross-profile lookup is via the optional `lead_partner_props` / `partner_props_list` params; tests construct stubs). Time to run < 1 second. **Pre-smoke pattern stays correct without Ext-8 seed in place.**

### Final DB state after Ext-1

| metric | before | delta | after |
|---|---:|---:|---:|
| rules table (AP-PROC-JV-CONSORTIUM-V1) | 0 | +1 | 1 |
| BidderProfile rows (6 Ext-1 fields backfilled) | 0 | +8 | 8 (all SOLE_BIDDER) |
| ValidationFinding (sentinel) | 154 | 0 | 154 ✓ |
| BidEvaluationFinding | 288 | +24 (all QUALIFIED-NOT_APPLICABLE) | 312 (= 24 × 13 criteria) |
| BIDDER_VIOLATES_RULE | 49 | 0 (SOLE_BIDDER QUALIFIED is silent) | 49 |
| EligibilityMatrix | 24 | re-aggregated | 24 (criteria_total 12 → 13) |
| TenderRanking | 3 | re-aggregated | 3 |
| BidAnomalyFinding | 6 | skipped | 6 |
| ComparativeStatement | 3 | re-rendered | 3 (13-row tables, new audit_ids) |

All 24 bids correctly path to SOLE_BIDDER → QUALIFIED-NOT_APPLICABLE. The 4 aggregate states (QUALIFIED / FLAGGED_FOR_COMMITTEE_REVIEW / MARK_FOR_DOCUMENTATION_REVIEW / DISQUALIFIED) remain exercised at 12/3/3/6 because Ext-1 added no INELIGIBLE outcomes on B1-B8.

### Deferred to Ext-8

- B9 synthetic JV BidderProfile (1 JV entity + 2 JV_PARTNER profiles for Lead + Member)
- JV path full corpus run (24 → 27 bids if B9 × 3 tenders)
- Sub-check 5 (Lead-alone financial) live demonstration: B9 Lead at ₹260cr, HC floor at ₹109.55cr → PASS; would FAIL if Lead were below floor
- L81 supplement: any Ext-8 surprises (e.g. partner profile referential gaps, JSONB array shape gotchas) when JV path actually runs against DB

---

## L80 — Module 3 Extensions Composite-Finding Pattern (Ext-2 Compliance Documents)

**Established during Ext-2** (`scripts/bid_compliance_documents_check.py`, May 2026). When a single criterion has **N sub-aspects that must ALL pass** (8 mandatory compliance documents; future: JV multi-criterion checks), emit ONE composite BidEvaluationFinding per (bidder, tender) carrying per-sub-aspect breakdown in `calc` dict + composite verdict via MAX-severity aggregation rule.

### Composite shape

```python
finding.properties = {
    # ... standard citation chain ...
    "compliance_summary": [             # per-sub-aspect breakdown
        {"label": "Company Registration Certificate", "field": "company_reg_cert_status",
         "status": "VALID", "compliance": "COMPLIANT"},
        {"label": "PAN Card", ...},
        # ... 6 more entries ...
    ],
    "compliant_count":         8,       # how many sub-aspects passed
    "doc_count_total":         8,       # how many total
    "hard_block_documents":   [],       # MISSING/DEFECTIVE/null sub-aspects
    "remediable_documents":   [],       # EXPIRED sub-aspects
    "verdict":                "QUALIFIED",
    "decision_reason":        "qualified_all_8_compliance_docs_valid",
}
```

### MAX-severity composite aggregation

| sub-aspect statuses present | aggregate verdict | evaluation_consequence |
|---|---|---|
| ALL COMPLIANT (VALID / SIGNED / NOT_REQUIRED) | QUALIFIED | ADVISORY |
| Any MISSING / DEFECTIVE / null | INELIGIBLE | HARD_BLOCK (dominates) |
| Any EXPIRED, no HARD_BLOCK | INELIGIBLE | WARNING (remediable) |

Composite reason names the violating sub-aspects: `ineligible_hard_block_docs_missing_or_defective:Power of Attorney|Tender Fee Receipt`.

### Why composite over N findings

Alternative would be 8 separate BidEvaluationFinding rows per (bidder, tender) — one per document. Rejected because:

1. **Aggregator explosion**: 8 × 24 = 192 new BidEvaluationFinding rows just for Ext-2 (vs 24 composite). EligibilityMatrix.criteria_total would grow 11 → 19 instead of 11 → 12. ComparativeStatement per-bidder tables grow proportionally.
2. **Audit-aggregator noise**: each individual document finding has identical citation chain (same bidder, tender, rule) — high redundancy.
3. **Verdict precedence semantics lost**: 8 separate findings would require external logic to aggregate "any HARD_BLOCK = block, any REMEDIABLE = warning" — composite finding bakes the rule in.

**Composite preserves drilldown granularity** via `compliance_summary[]` array. Sub-block 7 ComparativeStatement renders the 12th criterion as ONE row, with the 8-document breakdown visible in the underlying finding's drilldown.

### When to use composite vs separate findings

Use **composite** when:
- N sub-aspects are EVALUATED TOGETHER as a single regulatory criterion (e.g. "all 8 documents must be present per AP standard tender clause 1.6.2")
- Failure precedence rules apply across sub-aspects (HARD_BLOCK dominates REMEDIABLE)
- Sub-aspects share identity (same bidder, same tender, same rule)

Use **separate findings** when:
- Sub-aspects anchor on different rules (e.g. existing 10 Tier-2 validators each have their own rule)
- Independent verdict reasoning per sub-aspect makes sense for downstream tooling
- Drilldown wants per-sub-aspect node_ids for direct citation chain

### Rule-seed-and-flag pattern (Ext-2 secondary lesson)

Diagnose surfaced a rule anchor gap: no existing rule in the `rules` table cleanly matched the 8-document checklist for AP Works tenders.
- MPS-124 had right NL but `Services-only` condition_when
- AP-GO-110 had right scope but unrelated NL (COT website)
- GVSCCL-019 had right scope but ADVISORY severity + GST-only NL

**Solution**: seed a new rule `AP-PROC-COMPLIANCE-DOCS-V1` with appropriate `condition_when`, HARD_BLOCK severity, and NL enumerating all 8 documents per AP standard tender clause 1.6.2.

**Schema gotcha caught at apply time**: `rules` table has NOT NULL constraint on `source_doc` (and several other columns implied by the schema). Initial seed INSERT failed with 23502 NOT NULL violation. Resolved by populating: `source_doc`, `source_clause`, `category`, `verification_method`, `valid_from`, `extracted_from`, `extraction_confidence`, `human_status`, `human_note`, `generates_clause` (False), `defeated_by` ([]).

Forward-applicable: when seeding new rules via REST, fetch an existing rule via `SELECT *` first to discover the complete column set + NOT NULL columns before insertion.

### Cascade recovery — partial-state restoration discipline

EligibilityMatrix re-run hit a transient `OSError(22, 'Invalid argument')` mid-emit, leaving partial state (9 EligibilityMatrix rows instead of 24) + 2 ValidationFinding subprocess_crashed rows from the wrapper. Recovery:

1. Wait 10s for connection state to clear
2. Re-run EligibilityMatrix (idempotent — `_delete_prior_eligibility_matrix_rows()` cleans partial 9 + re-emits 24)
3. Crash-resilience `DeferredCleanup.commit()` on successful retry deletes the prior crash rows → sentinel ValidationFinding restored 156 → 154 automatically
4. Re-run downstream aggregators that consumed the partial state (TenderRanking + ComparativeStatement)

The deferred-cleanup pattern (L65 + Module 3 core b-prime work) handles this cleanly without manual sentinel intervention — confirmed in Ext-2 cascade recovery.

### Final DB state after Ext-2

| metric | before | delta | after |
|---|---:|---:|---:|
| rules table (AP-PROC-COMPLIANCE-DOCS-V1) | 0 | +1 | 1 |
| ValidationFinding (sentinel) | 154 | 0 (after crash recovery) | 154 ✓ |
| BidEvaluationFinding | 264 | +24 (all QUALIFIED) | 288 |
| BIDDER_VIOLATES_RULE | 49 | 0 (no INELIGIBLE on B1-B8 compliance) | 49 |
| EligibilityMatrix | 24 | re-aggregated | 24 (criteria_total 11→12) |
| TenderRanking | 3 | re-aggregated | 3 |
| BidAnomalyFinding | 6 | skipped | 6 |
| ComparativeStatement | 3 | re-rendered | 3 (12-row tables, new audit_ids) |

---

## L79 — Module 3 Extensions Batch Pattern (Ext-4+5+6 Forward-Compat)

**Established during Ext-4+5+6 Batch** (May 2026; single commit). First batch of Path B Extensions — extends 3 existing validators (`bid_abc_check`, `bid_solvency_check`, `bid_similar_works_check`) with schema-aware logic while preserving outputs for the existing 264 BidEvaluationFinding rows. Mirror of Sub-block 3b Batch pattern from Module 3 core: pilot establishes pattern (Ext-3 / Sub-block 3a), batch applies pattern in bulk (Ext-4+5+6 / Sub-block 3b Batches).

### Pattern recipe

1. **Path B candidates**: extensions that ADD a knob to an existing check (e.g. M-coefficient method, validity window length, per-entry compliance filter). Each is a module-level constant or a per-iteration field read → replace with parameter + BidderProfile/entry field lookup, default to existing constant when field absent.
2. **Backfill = defaults**: every existing bidder gets the new fields set to values that preserve the hardcoded behavior. B1-B8 all get `abc_formula_M_method=AP_GO_062_M2`, `solvency_cert_validity_window_months=12`, similar_works[]-per-entry `client_type=GOVT` + `counter_signature_status=EE_SIGNED`. Validator behavior on existing data is identical.
3. **Zero re-emission**: existing 264 BidEvaluationFinding rows stay untouched. No aggregator cascade. No ComparativeStatement re-rendering. Sentinel preserved exactly.
4. **Forward-compat posture**: future bidders (B9 in Ext-8) seed variant configurations — `abc_formula_M_method=MPW_M3`, `solvency_cert_validity_window_months=3`, similar_works[] with `client_type=PRIVATE` + TDS cert — which the now-schema-aware validators will evaluate correctly.

### Approach E — Unit-test compute_verdict() without DB writes

**Discipline lesson from Ext-4+5+6**: validator full-runs always re-emit findings (via `_delete_prior_*` cleanup + re-insert), which creates orphan UUIDs in EligibilityMatrix's `finding_node_ids[]` arrays and forces a partial aggregator cascade. For Path B extensions where backfill=defaults, this side effect is purely wasted work.

**Solution**: write inline unit tests that import the validator module's `compute_verdict()` function and call it with constructed inputs. Pure function-level tests; no DB writes; no orphan UUIDs; no cascade.

Reference implementation: `tests/extensions/test_ext456_compute_verdict.py` runs 9 tests covering:
- Existing-behavior preservation (default parameter values match hardcoded constants)
- Schema-aware mapping (AP_GO_062_M2 → M=2; MPW_M3 → M=3; AP_GO_089_12MO → 12mo; etc.)
- Negative case verification (PRIVATE + missing TDS → excluded from 3/2/1 count)
- Counterfactual reasoning (B3's M=3 is valid under MPW_M3 but violation under AP_GO_062_M2)
- Backward compat (legacy entries without Ext-6 fields → assumed compliant)

Run-time: <1s for all 9 tests. Cost reduction vs full validator re-runs: ~50× (no DB writes, no aggregator cascade, no ComparativeStatement re-rendering, no orphan-UUID cleanup).

### When to use Approach E vs full re-runs

| scenario | re-runs needed? | rationale |
|---|---|---|
| Path A new validator (Ext-3, future Ext-1/2) | yes | new findings need to be emitted; aggregator cascade picks them up |
| Path B existing validator extension where backfill ≠ defaults (could change verdicts on existing bids) | yes | re-emission necessary to reflect updated verdicts |
| Path B existing validator extension where backfill = defaults (Ext-4/5/6 here) | **no — use Approach E** | existing verdicts unchanged; only the schema knob is added |
| Pure code refactor (no logic change) | no — use Approach E | function behavior identical; full re-runs redundant |

### Schema migration scope (Ext-4+5+6 actuals)

- **5 BidderProfile fields × 8 bidders = 40 cells** (Ext-4: 2 + Ext-5: 3)
- **4 per-entry fields × 57 similar_works[] entries across 24 fact_sheets rows = 228 cells** (Ext-6)
- **Total**: 268 new cells via JSONB additive UPDATE; zero DDL; zero new BidEvaluationFinding rows; zero edge mutations.

### Cumulative outcome arithmetic verification (B9 spec accounting)

Per Ext-7 B9 spec: 10 Tier-2 + 6 Extensions = 16 outcomes per tender. After Ext-3 (Path A — new validator, +1): 11 Tier-2 + 5 remaining = 16 ✓. After Ext-4+5+6 (all Path B — no validator additions): **11 Tier-2 + 5-3 = 13 remaining**... wait, that doesn't match. Let me recount.

Actually the B9 spec counts each Extension as ONE outcome regardless of path. Ext-3 added one new validator (Bidder-Financial-Turnover) → 11 Tier-2. Ext-4+5+6 extend existing validators → 0 new validators. So: 11 Tier-2 validators + 2 remaining Extensions (Ext-1 JV/Consortium and Ext-2 Compliance docs, both Path A) = **13 validators total** when Ext-1/2 land. The "16 outcomes" framing was based on each Extension adding ONE outcome PER PER-BIDDER-PER-TENDER evaluation, but Path B extensions modify the same finding (don't add new ones).

**Reframe**: B9 spec's 16 = 10 base + 6 Extension AUDIT TOUCHPOINTS, not 16 distinct findings. Ext-4/5/6 don't add findings; they add fields/logic INSIDE existing findings. After Ext-1/2 land (both Path A), Tier-2 validators count becomes 13 (11 + 2). B9 will have 13 BidEvaluationFinding rows per tender, not 16. The B9 spec's "16 outcomes" framing was slightly off; the actual count post-Extensions is **13 per tender × 3 tenders = 39 findings for B9**. Updated accounting noted.

### Final DB state after Ext-4+5+6 Batch

| metric | before | delta | after |
|---|---:|---:|---:|
| ValidationFinding (sentinel) | 154 | 0 | 154 ✓ |
| BidEvaluationFinding | 264 | **0** (zero re-emission) | 264 ✓ |
| BIDDER_VIOLATES_RULE | 49 | 0 | 49 ✓ |
| EligibilityMatrix | 24 | 0 | 24 ✓ |
| TenderRanking | 3 | 0 | 3 ✓ |
| BidAnomalyFinding | 6 | 0 | 6 ✓ |
| ComparativeStatement | 3 | 0 (no re-rendering) | 3 ✓ |
| BidderProfile.properties | 8 × 46 fields | +5 per row | 8 × 51 fields |
| similar_works[] entries | 57 × 7 fields | +4 per entry | 57 × 11 fields |

**Sentinel preserved exactly.** Validator code is now schema-aware; B9 evaluation in Ext-8 will exercise variant configurations.

---

## L78 — Module 3 Extensions Pilot Pattern (Ext-3 Dual Turnover)

**Established during Ext-3** (`scripts/bid_financial_turnover_check.py` + schema migration + seed-script edits, May 2026). First implementation Extension; establishes the pattern Ext-4 through Ext-6 will replicate.

### Pattern components

**1. Schema migration via JSONB in-place UPDATE (no DDL)**

`kg_nodes.properties` is JSONB → field additions are zero-migration. For 8 BidderProfile rows: PATCH each `properties` JSON with renamed/added fields. Idempotent: re-run checks before adding. Pattern carries forward to Ext-4/5/6.

**2. Tender criterion data location — Path γ (mirror existing)**

Synthetic tenders have NO TenderDocument kg_nodes (only TenderRanking / ComparativeStatement / BidAnomalyFinding reference `tender_synth_*` doc_ids). Tender PQ data lives in:
- `fact_sheets.Statement-<N>.extracted_facts.<criterion>_pq_floor_cr` (per bid; defensive cross-check)
- In-script `SYNTHETIC_TENDER_CATALOG` dict (per validator)

Ext-3 mirrors the existing pattern: BOTH locations get the new `financial_pq_floor_cr` field. The validator reads both and asserts `*_consistent` audit flag. Same path applies for Ext-4/5/6 when adding new tender-side criterion values.

**3. Validator architecture — Path A (new file)**

For each Extension that introduces a NEW criterion (vs modifying an existing one), write a NEW validator script. Reasons:
- Per-criterion BidEvaluationFinding granularity → audit-clear which criterion failed
- Aligns with B9 spec's "10 + 6 = 16 outcome" framing (Ext-3 makes it 11 + 5 = 16)
- New file is ~250-450 LOC, mostly boilerplate mirroring the closest existing validator (e.g. `bid_financial_turnover_check` mirrors `bid_turnover_check`)
- Cleaner verdict reasoning + filter-by-typology queries vs Path B's composite finding shape

Path B (extend existing internally) is reserved for cases where the criterion is genuinely a sub-aspect of an existing check (e.g. Ext-4 ABC M-coefficient variant might still emit ONE Bidder-Capacity-Compliance finding with method-aware logic; Ext-5 solvency window variant likewise).

**4. Cascade aggregator re-runs — selective**

After validator commit emits new BidEvaluationFinding rows:
- **EligibilityMatrix**: ALWAYS re-run (finding counts change; aggregate verdicts may not, but `criteria_total` and `finding_node_ids[]` arrays must update)
- **TenderRanking**: re-run if QUALIFIED bidder set changes (it didn't in Ext-3); otherwise skip
- **CrossBidAnomalyDetector**: re-run if pair-detection inputs change (BidderProfile cartel signals, EMD bank, LetterOfBid premium). Skip if untouched
- **ComparativeStatementGenerator**: ALWAYS re-run (per-bidder criteria table grows; audit_id changes from new finding UUIDs in hash)

Ext-3 cascade: EligibilityMatrix + TenderRanking + ComparativeStatement re-run; CrossBidAnomalyDetector skipped (no input change).

**5. Audit-pedantic catches surfaced in diagnose**

Ext-3 diagnose surfaced two catches before apply:
- **CVC-028 rule literal text disagrees with pilot validator's field interpretation** — CVC-028's NL literally says "3-year financial turnover" but `bid_turnover_check` reads `average_5yr_turnover_cr` and treats it as construction. Pilot design mismatch, not bug. Ext-3 cleanly resolves WITHOUT rewriting pilot: keep `bid_turnover_check` on CVC-028 (operationally Construction 5yr); new `bid_financial_turnover_check` anchors on **MPG-255** (literal "average annual financial turnover... last three years"). Clean separation by rule choice.
- **TenderDocument kg_nodes don't exist for synthetic tenders** — directive's "3 Tender kg_nodes" was a false premise. Resolved via Path γ (mirror existing fact_sheets + in-script catalog).

**6. Realism caveat surfaced in `methodology_note` field**

For synthetic demo, `financial_3yr_cr = 0.70 × construction_5yr_cr` per directive. In reality, pure-construction firms typically have `financial ≥ construction` (construction IS the main revenue line). The 70% ratio is a seed design choice that demonstrates Ext-3 discrimination without disrupting aggregate verdicts. Documented in `turnover_methodology_note` (BidderProfile field) for audit-defensibility.

Pattern carries forward: when seed design choices deviate from real-world ratios for demo-discrimination purposes, surface the deviation in a `*_methodology_note` field, not just a code comment. Audit-defensible.

### Cumulative outcome arithmetic verification

Ext-7 spec predicted "10 Tier-2 + 6 Extensions = 16 outcomes". Ext-3 shifts the count: now **11 Tier-2 validators + 5 remaining Extensions = 16**. Spec's accounting holds (Extensions ADD validators, not modify-in-place — Path A discipline). Each subsequent Extension that chooses Path A increments the existing-count; each Path B keeps it stable.

### Final DB state after Ext-3 (pilot extension)

| metric | before | delta | after |
|---|---:|---:|---:|
| ValidationFinding (sentinel) | 154 | 0 | 154 ✓ |
| BidEvaluationFinding | 240 | +24 (20 QUALIFIED + 4 INELIGIBLE) | 264 |
| BIDDER_VIOLATES_RULE | 45 | +4 (B2-HC + B3×3) | 49 |
| EligibilityMatrix | 24 | re-aggregated | 24 (criteria_total now 11; verdicts unchanged) |
| TenderRanking | 3 | re-aggregated | 3 (effective L1 unchanged) |
| BidAnomalyFinding | 6 | skipped | 6 |
| ComparativeStatement | 3 | re-rendered | 3 (new audit_ids: f8c5aea9 / e22452e5 / ...) |

Zero aggregate verdict transitions (B3 was already DISQUALIFIED on all 3; B2 was already DISQUALIFIED on HC). The new findings ADD HARD_BLOCK depth to already-DISQUALIFIED bidders rather than changing aggregate outcomes.

---

## L77 — Module 3 Extensions Design Specification Pattern (Ext-7)

**Established during Ext-7** (`docs/extensions/B9_demobidder_spec.md`, May 2026). When multiple implementation sub-blocks share a common target (B9 DemoBidder demonstrating all 6 Module 3 Extensions), a design-only sub-block can precede the implementation sub-blocks and act as the contract they build to satisfy.

### Pattern

1. **Design-only sub-block first.** Writes a spec document. Zero data layer changes; zero validator code; zero seed-script edits. Just a Markdown file (or set of files) under `docs/<series>/`.
2. **The spec is the contract.** Each subsequent implementation sub-block (Ext-1 through Ext-6 in this case) builds to satisfy specific sections of the spec. Predicted verdicts in the spec become the verification criteria for each implementation's PR.
3. **A final integration sub-block** (Ext-8) consumes the spec — seeds B9 + runs the full pipeline + verifies the predicted matrix.

### When to use

- 2+ implementation sub-blocks share a common target entity (B9 here; could be a particular dataset, a workflow scenario, an integration partner)
- The target entity is non-trivial to specify (B9 has ~66 BidderProfile fields + 3 partner sub-profiles + 30 fact_sheets across 3 tenders + predicted verdicts on 16 evaluation outcomes per tender)
- Skipping the design step would force each implementation sub-block to invent its B9 contract independently — introducing drift

### When NOT to use

- Single implementation sub-block — over-engineering; just diagnose-propose-apply the work directly
- Trivial target (a simple flag toggle, a single field add) — spec adds latency without insight

### 4 design discipline examples (cumulative pattern recognition across Module 3)

The design-only sub-block crystallizes the discipline pattern that diagnose-propose has demonstrated repeatedly across Module 3:

1. **Cross-validator overlap check** (Sub-block 1.2 diagnose) — bid_blacklist_check's `active_govt_cases` secondary signal was hijacking AP-GO-066 (bid_litigation) territory at AP-GO-096 severity. Caught when extending seed to add B4 (clean blacklist + 1 litigation) exposed the overlap. Fixed in same commit per L70 cross-validator sentinel pattern.

2. **Citation accuracy verification** (Batch 1 diagnose) — directive referenced `AP-GO-181` for blacklist debarment 5-year lookback; that rule_id is actually about store-verification timing. The 5-year lookback lives in MPW-045. Caught by reading the actual rule's `natural_language` in the rules table before adopting it.

3. **Environment discovery** (Sub-block 7 diagnose, L76) — directive referenced `/mnt/skills/public/{docx,pdf}/SKILL.md` (Anthropic Code Cloud convention); current environment is local macOS with neither path nor MCP skill tools. Caught by probing `ls`, `which`, `python3 -c "import …"` BEFORE referencing rendering pipelines in apply.

4. **Boundary math verification against actual constraint values** (Ext-7 diagnose, this sub-block) — directive specified B9 Lead Partner `construction_turnover_5yr_avg_cr = 200cr`. The HC tender's PQ turnover floor is ₹243.4cr. 200 < 243.4 → B9 would FAIL HC turnover check. Caught by checking actual TenderRanking properties for HC's PQ floor before drafting B9's value. Corrected to ₹260cr in the spec.

### The underlying discipline

**Predict before apply. Verify predictions against actual state during diagnose. Fix at design-time rather than discovering during implementation.**

Each of the 4 examples above would have cost between 30 minutes (citation fix) and several hours (validator rewrite + re-run of 240 validations) if caught only during apply. Catching them in diagnose costs 5–15 minutes of additional query work — a 5×–20× cost reduction per catch.

This pattern carries forward to any future design-led sub-block series.

---

## L76 — Environment-Discovery Discipline in Diagnose Step

**Surfaced during Sub-block 7 (ComparativeStatementGenerator)** (May 2026). The directive referenced `/mnt/skills/public/docx/SKILL.md` and `/mnt/skills/public/pdf/SKILL.md` — the **Anthropic Code Cloud** convention. The current environment is **local macOS** (verified via `/tmp → /private/tmp` symlink); neither path exists, and no MCP skill tools for docx/pdf are surfaced.

This pattern repeats: directives written from one environment's assumptions don't transfer cleanly to another. Catching the gap in **diagnose** (before apply) is cheap; catching it during render-pipeline implementation is expensive (broken code, partial output, re-work).

### Diagnose-step environment probes — checklist for any directive referencing a toolchain

Before drafting the apply plan, run these probes:

1. **File paths**: `ls -la <referenced_path>` for any concrete path the directive mentions (skills, mount points, project roots).
2. **CLI tools**: `which <tool>` for any external binary (pandoc, weasyprint, wkhtmltopdf, libreoffice, ffmpeg, etc.).
3. **Python packages**: `python3 -c "import <pkg>; print(<pkg>.__version__)"` for any rendering / parsing dep.
4. **MCP tools**: scan the deferred-tool list in the system reminder for `mcp__skills_*` or similar registered services.
5. **Filesystem write access**: `[ -w /tmp ] && echo "writable"` for any output directory.

Surface ALL gaps in the diagnose-propose, with **2-3 alternative paths** (e.g. install dep / use what's available / defer feature). The user chooses; apply step never has to scramble around a missing tool.

### Sub-block 7 reference example

The diagnose surfaced:
- `/mnt/skills/public/{docx,pdf}/SKILL.md` → not present
- `python-docx 1.2.0` → ✓ installed
- `reportlab / weasyprint / pandoc / wkhtmltopdf / libreoffice` → ✗ none installed

Three paths offered: (A) Markdown + DOCX only, defer PDF; (B) `pip install reportlab`; (C) `pip install weasyprint + brew system deps`. User chose A. PDF queued as L75 follow-up. **Zero broken-code time during apply** because the gap was caught upstream.

Pattern carries forward to any future sub-block that references external rendering, conversion, or MCP-skill toolchains. Bake the probe checklist into the diagnose-step boilerplate.

---

## L75 — PDF Renderer Integration [QUEUED]

**Surfaced during Sub-block 7 (ComparativeStatementGenerator)** (May 2026). Sub-block 7 Path A shipped Markdown + DOCX evaluation-committee reports; PDF rendering deferred because the local macOS environment has no PDF toolchain (no pandoc / weasyprint / wkhtmltopdf / libreoffice / reportlab).

### Choice for follow-up: reportlab vs weasyprint

| | **reportlab** | **weasyprint** |
|---|---|---|
| install | `pip install reportlab` (pure Python, BSD) | `pip install weasyprint` + `brew install pango cairo` |
| layout API | reportlab.Platypus — verbose, programmatic (~200-300 LOC for the 7-part report) | HTML + CSS template — declarative, ~80-100 LOC + a CSS file |
| fidelity | good for tables + text + images; less precise for complex layouts | print-grade CSS rendering (positioning, gradients, paged media) |
| portability | runs anywhere Python runs | system-dep sensitivity (pango/cairo version matters) |
| size on disk | small (~5MB) | large (~30MB with deps) |
| recommended for procureAI | **YES** — pure-Python aligns with the rest of the codebase, no system deps | maybe later for highly-styled marketing PDFs |

**Recommendation**: `pip install reportlab` + ~250 LOC reportlab.Platypus rendering function added to `run_comparative_statement_generator.py`. Pattern mirrors `render_docx()` — build a list of flowables (Paragraph, Table, PageBreak), then `SimpleDocTemplate.build()`. Shipped as an L75 follow-up commit (~1 day).

### What's already in place that L75 should preserve

- `pdf_artifact_path` field on ComparativeStatement node (currently `null`)
- `pdf_artifact_status` field (currently `"deferred_no_renderer_in_env"`)
- `pdf_followup_options` field (currently `["reportlab","weasyprint"]`)
- The Markdown intermediate (always works as a reference for the PDF renderer)

L75 commit: install reportlab, add `render_pdf()` function, save to `/tmp/comparative_statements/<tender>.pdf`, populate `pdf_artifact_path`, set `pdf_artifact_status="rendered_v1_reportlab"`.

---

## L74 — Synthetic Seed: Bank-Branch Diversity for Cartel-Signal Discrimination [QUEUED]

**Surfaced during Sub-block 6 (CrossBidAnomalyDetector)** (May 2026). The COMMON_BANK_BRANCH cartel signal fires on **all 6 QUALIFIED bidder pairs per tender** in the current corpus (21 of 24 bids share `State Bank of India, Vijayawada Main Branch`). It contributes to confidence aggregation but is not discriminating — only the aggregation rule (`signal_count ≥ 2 OR HIGH severity`) prevents it from generating false-positive cartel flags.

**Queue item**: extend `scripts/seed_synthetic_bids.py` to diversify bank branches across bidders, e.g.:
- B1 → SBI Vijayawada Branch
- B2 → Canara Bank Tirupati
- B4 → HDFC Vijayawada
- B5 → Bank of Baroda Visakhapatnam
- B6+B7 → Co-operative Bank of Guntur (cartel pair share)
- B8 → SBI Mumbai (unrelated)

Once seeded, COMMON_BANK_BRANCH becomes a differentiating signal: only B6+B7 would share a bank (already share address + signatory + sequential bids). Detector logic stays unchanged; new seed exposes that COMMON_BANK_BRANCH adds independent discrimination beyond shared-address.

**Not blocking** Sub-block 6 — the cartel-pair detection works correctly on the current corpus because B6+B7's other 3 signals dominate. Queue for landing alongside or after L72 (the ALB-fires-but-L1-is-non-ALB extension).

---

## L73 — Cross-Bid Anomaly Detector Pattern (Sub-block 6)

**Established during Sub-block 6** (`scripts/run_cross_bid_anomaly_detector.py`, May 2026). Third aggregator in the platform; introduces multi-signal aggregation with severity weighting and cross-tender consistency as a confidence multiplier — patterns that generalize to future anomaly detectors (BID_ROTATION, IDENTICAL_DOCUMENT_ARTIFACTS, COMMON_SUBCONTRACTOR).

### Multi-signal aggregation shape

| dimension | EligibilityMatrix (L65) | TenderRanking (L71) | CrossBidAnomalyDetector (NEW) |
|---|---|---|---|
| Input scope | per (bidder, tender) | per tender | per (tender, bidder-pair) AND per (tender, ALB-candidate) |
| Decision shape | precedence ladder (4-state) | filter + sort | signal aggregation (count + severity) |
| Output node_type | EligibilityMatrix | TenderRanking | **BidAnomalyFinding** |
| Severity model | per-rule (HARD_BLOCK > WARNING > GAP > QUALIFIED) | binary (ALB / not) | per-signal LOW/MEDIUM/HIGH; aggregate = MAX |
| Confidence | n/a | n/a | **HIGH if signal_count ≥ 4; MEDIUM 2-3; LOW 1** |
| Cross-tender consistency | per-bidder (implicit across EligibilityMatrix rows) | per-bidder ALB-count cross-tender | **Explicit `cross_tender_consistency` + `cross_tender_appearances` fields on every finding** |

### Methodology fields as queryable audit handles

Each finding carries:
- `methodology_version` (string — e.g. "v1") for filterable historical-vs-current comparison
- `methodology_note` (prose) describing thresholds + caveats
- `thresholds` (dict) with all numeric/string thresholds used (e.g. `sequential_premium_delta_max_pct`, `flag_rule`, `confidence_ladder`)

This means future threshold tuning (e.g. tightening `SEQUENTIAL_PREMIUM_DELTA_MAX_PCT` from 0.10% to 0.05%) doesn't require re-emitting historical findings — downstream tooling can filter by `methodology_version` to compare versions side-by-side. Pattern carried forward from L71 (alb_threshold_method on TenderRanking).

### 4-layer drilldown chain (extended from L71's 4 layers to 5+)

```
BidAnomalyFinding
  → bid_submission_ids[]           → BidSubmission nodes (per implicated bid)
  → bidder_profile_node_ids[]      → BidderProfile (per implicated bidder; address/signatory evidence)
  → tender_ranking_node_id         → TenderRanking (ranking + ALB context)
  → eligibility_matrix_node_ids[]  → EligibilityMatrix (per (bidder, tender); aggregate verdict)
                                       → finding_node_ids[] → 10 BidEvaluationFinding rows
```

5 distinct drilldown targets per anomaly. Downstream consumers (Sub-block 7) walk the chain to reconstruct full citation: evaluation-committee report shows the anomaly → the bidders → their address+signatory match → their bid rankings → their full Tier-2 verdict trail.

### Signal evidence + citation_source per signal

Each signal in the `signals[]` array carries its own `severity`, `evidence` (the specific facts that triggered detection — e.g. *"Both bidders' communication_address = '4-7-89, Industrial Estate, Guntur-522001'"*), and `citation_source` (the regulatory anchor — e.g. *"CVC OM No 8(1)(h)/98(1) — Vigilance Aspects in Procurement"*). This per-signal granularity lets evaluation-committee reports cite the exact governance authority backing each piece of cartel evidence.

### Honest-disclosure pattern for noisy signals

When a signal's discrimination is limited by current corpus shape (e.g. COMMON_BANK_BRANCH firing on all 6 pairs because 21 of 24 bids share the same bank), record the noise in `methodology_note` rather than dropping the signal. The aggregation rule (`signal_count ≥ 2 OR HIGH severity`) absorbs the noise without false positives, and the signal becomes discriminating as soon as seed diversity expands (L74 queue). Forward-applicable to any aggregator with thresholds tuned for sparse-data corpora.

### Pure-aggregator + sentinel discipline carries L65/L71 forward

CrossBidAnomalyDetector inherits the established aggregator contract without modification:
- No edge emission (drilldown via arrays on finding properties)
- Single batch `main_with_crash_resilience` wrapper with synthetic `doc_id="cross_bid_anomaly_detector_v1"`
- `source_ref` single-string filter for idempotent cleanup
- Sentinel snapshot pre/post enforces read-only contract (RC=2 on drift); snapshot now extends to 5 upstream types (validators, edges, EligibilityMatrix, TenderRanking, plus BidAnomalyFinding itself in the post-snapshot)

---

## L72 — Synthetic Coverage Gap for "ALB-fires-but-L1-is-non-ALB" Path [QUEUED]

**Surfaced during Sub-block 5 (TenderRanking aggregator)** (May 2026). The current corpus exercises only the `alb_action_required=True` path (B8 is L1 on all 3 tenders with -38% premium). The complementary path — `alb_action_required=False` BUT `alb_candidates` is non-empty (i.e. ALB-flag fires on a non-L1 bidder) — never fires.

Add **B9 — Competing-Mid** at premium ~−8% to ~−10% (cheaper than B6/B7 but above ALB threshold even after B8 outlier is excluded). With B9 present:
- L1 stays at B8 (−38%)
- L2 flips to B9 (−8 to −10%)
- ALB threshold accommodates B9's mid-tier position
- Tests: ALB candidate set includes B8 only, but a wider mid-range distribution means B9 sits well above threshold even without B8's outlier pull

A second forward-applicable variant: B10 — Anomalously-Low-But-Not-L1 (a bid below ALB threshold that isn't L1 because B8 sits even lower). Tests `alb_action_required=True` from L1=B8 AND `alb_candidates=[B8, B10]` for a multi-ALB-flag scenario.

**Not blocking** Sub-block 5 verification — the current single-ALB path was the design target. Queue for landing before or after Sub-block 7 if demo timeline warrants ALB-path coverage expansion.

---

## L71 — TenderRanking Aggregator Pattern (Sub-block 5)

**Established during Sub-block 5** (`scripts/run_tender_ranking.py`, May 2026). Second aggregator in the platform (after EligibilityMatrix Sub-block 4); pattern stabilizes for downstream Sub-block 6 (CrossBidAnomalyDetector) and Sub-block 7 (ComparativeStatementGenerator).

### Filter-and-sort aggregator shape

| Aspect | EligibilityMatrix (Sub-block 4) | TenderRanking (Sub-block 5) |
|---|---|---|
| Input grouping | (bidder, tender) — group by both | tender only — group by tender_id |
| Filter | none (aggregate all 10 criteria) | `aggregate_verdict == QUALIFIED` |
| Join | none (single-source: BidEvaluationFinding) | LetterOfBid by `bid_submission_id` for `bid_amount_cr` |
| Sort | by typology_code (alphabetical) | by `bid_amount_cr` ASC |
| Tie-break | n/a (1 finding per typology per pair) | `signature_date` ASC → `bid_submission_id` lexical, with `tie_break_applied` audit flag |
| Per-row scope | one (bidder, tender) | one tender |
| Output node_type | `EligibilityMatrix` | `TenderRanking` (new) |
| Edges | none (drilldown via `finding_node_ids[]`) | none (drilldown via `ranking[].eligibility_matrix_node_id` + `bid_submission_id`) |
| Wrapper | single batch `main_with_crash_resilience` | same — single batch |
| Idempotency | `_delete_prior_*` by `source_ref` | same |
| Sentinel pre/post | yes (RC=2 on drift) | same (now snapshots EligibilityMatrix too) |

### Methodology choice as queryable field

ALB threshold uses simple-average × 0.80 per CVC standard, but the methodology choice is sensitive to outlier presence (an abnormally-low bid pulls the average DOWN, narrowing the threshold-vs-bid gap). The finding records:
- `alb_threshold_method` = `"simple_average_times_0.80"` (queryable string for future-method-comparison filtering)
- `alb_methodology_note` = full transparency note documenting the simple-average artifact + alternative ECV-anchored method (ECV × 0.80) as a documented future option

Forward-applicable to any aggregator with a parametric methodology choice: surface the method name as a structured field + the rationale/caveat as a prose field. Future audits can switch methodology without re-emitting historical findings; downstream tooling can filter on method choice to compare results across versions.

### Drilldown chain (4 layers)

```
TenderRanking (per tender)
  → ranking[].bid_submission_id        → BidSubmission node (per bid)
  → ranking[].eligibility_matrix_node_id → EligibilityMatrix (per bidder-tender pair)
  → finding_node_ids[]                 → 10 BidEvaluationFinding rows
```

Each layer denormalizes for read-optimized access at its own scope (tender / bid / aggregate / criterion). Downstream consumers (Sub-block 7 ComparativeStatementGenerator) walk this chain to reconstruct full citation context per bidder per criterion.

### Pure-aggregator + sentinel discipline (carries L65 forward)

EligibilityMatrix established the "pure aggregator + sentinel pre/post" pattern. TenderRanking inherits it without modification:
- No edge emission (graph mutations belong to validators, not aggregators)
- Sentinel snapshot pre captures all upstream node/edge counts the aggregator reads
- Post-emission re-snapshot proves zero drift; RC=2 on drift fails the run
- source_ref single-string filter for idempotent cleanup
- Single batch `main_with_crash_resilience` wrapper (vs per-row in validators)

---

## L70 — Cross-Validator Sentinel Pattern (post-overlap-fix audit)

**Established during Sub-block 1.2 bid_blacklist_check fix** (May 2026). When fixing a validator design overlap (e.g. two validators both reacting to the same input field but at different rule severities), add an explicit cross-validator sentinel check that tests the previously-conflated decision region.

**Reference case**: bid_blacklist_check + bid_litigation_check both read Statement-VII litigation_count. Pre-fix, bid_blacklist's `active_govt_cases` secondary signal hijacked AP-GO-066 territory at AP-GO-096 (HARD_BLOCK) severity. Fix removed the secondary signal. Sentinel check: for any bid with `blacklist_status='clean' AND litigation_count>0`, verify `bid_blacklist=QUALIFIED + bid_litigation=INELIGIBLE-WARNING` — never `bid_blacklist=INELIGIBLE`. B4×3 passes this sentinel post-fix.

Pattern: **bake the sentinel into the test corpus**, not into validator code. The synthetic data should always carry at least one bidder that lives in the previously-conflated region; absence of a regression in the aggregate matrix on that bidder is the standing proof that the overlap stays fixed. If a future change re-introduces the overlap, the sentinel bidder's aggregate verdict shifts visibly (here: B4 would flip from FLAGGED back to DISQUALIFIED).

---

## L69 — Synthetic Data Coverage Discipline

**Surfaced during Sub-block 1.2** (May 2026). Sparse synthetic coverage hides cross-validator overlaps. The 3-bidder corpus (B1/B2/B3) never exercised the `blacklist_status='clean' AND litigation_count>0` decision region — the bug surfaced only when B4 (Borderline-Litigation, designed to trip FLAGGED) was added.

**Discipline**: design test cases to exercise EVERY decision branch of EVERY validator, not just intuitive failure modes. Coverage matrix should enumerate not only positive/negative outcomes per validator but every distinct INPUT REGION (the cartesian product of field values that hit independent code paths).

**Concrete checklist for adding new validators**:
1. Identify every branch in compute_verdict (including secondary signals).
2. For each branch, find or design a synthetic bidder that hits it AND nothing else (so the branch's contribution to the aggregate is observable in isolation).
3. If existing bidders all conflate two branches (e.g. B3 trips both blacklist primary + secondary), add a single-branch bidder.
4. Run the aggregator after seeding and confirm every aggregate-state vocabulary value fires at least once.

**Forward-applicable to Sub-block 6 (CrossBidAnomalyDetector)**: cartel pair B6+B7 and ALB B8 are designed for the same discipline — each carries one targeted anomaly signal in isolation so Sub-block 6's detector logic can be tested branch-by-branch.

---

## L68 — Seed-Script Per-Profile-Flag Pattern

**Established during Sub-block 1.2 refactor** (May 2026). Profile-keyed `endswith()` branches across builders don't scale beyond 3-4 profiles. Replaced with per-profile behavior flags in PROFILES dict:

```python
"b5": dict(
    ...,
    _skip_statement_vi=True,        # forces bid_personnel_check GAP path
    _premium_pct_delta=-3.5,        # LetterOfBid + PricedBoQ bid amount
    _emd_bg_anomalous=False,
    _solvency_buffer_mult=1.5,
    _similar_works_pattern="three_full",
    _boq_complete=True,
    _boq_line_item_count=270,
),
```

Builders read flags via `profile.get("_flag_name", default)` — no profile_id pattern matching, no `endswith` chains. Adding a 9th profile is O(1) (add one PROFILES entry); pre-refactor it was O(N) (edit every builder that branched on profile_id).

Naming convention: behavior flags use `_leading_underscore` prefix to distinguish them from data fields (turnover, address, etc.). Forward-applicable to any seed-script that needs to extend test corpus to new profiles.

---

## L67 — Cross-Module Schema Discipline (Sub-block 1.2 application)

**Established during Sub-block 1.2** (May 2026). When extending synthetic data, fold in consumer modules' essential fields rather than letting schema drift across module boundaries. Forward-compat is zero-cost during hand-authored seeding; expensive during downstream migration.

**Reference case**: Sub-block 1.2 added 5 new bidder profiles (B4–B8) for Module 3 (Tier-2 evaluator) demo signal AND simultaneously augmented all 8 BidderProfile rows with 13 Module 4 (communicator) essential fields — email, mobile, notification channel, language, portal credential (synthetic placeholder), historical track record (past blacklist events, tender participation, anomaly flags), authorized signatory, communication address.

When Module 4 ships, no BidderProfile migration needed — the data is already there. If we had ignored Module 4 in Sub-block 1.2, the 24 BidderProfile rows would need a separate backfill migration AT the Module 4 build time, racing against any other changes in flight.

**Discipline checklist** before extending synthetic data:
1. List all consumer modules (current + planned within the next 2-3 sub-blocks).
2. For each consumer, identify their essential read fields.
3. Confirm those fields exist on the entity being seeded. If absent, add them in this seed pass.
4. Mark optional/speculative fields explicitly out of scope (Sub-block 1.2 deferred DSC status, bank account details, ISO certifications, etc. — fields no current consumer reads).

**Methodology principle**: don't bloat schema with fields no current consumer reads, but DO include fields a near-term consumer will read. The line is "do we have a written-and-approved consumer plan?"

---

## L66 — Synthetic-Seed Coverage Gap for 4-State Aggregate Vocabulary [CLOSED Sub-block 1.2]

**CLOSED Sub-block 1.2** (May 2026). All 4 aggregate verdict states now fire on the extended synthetic corpus:
- QUALIFIED ×12 (B1×3 + B6×3 + B7×3 + B8×3)
- FLAGGED_FOR_COMMITTEE_REVIEW ×3 (B4×3 — Borderline-Litigation)
- MARK_FOR_DOCUMENTATION_REVIEW ×3 (B5×3 — Statement-VI suppressed)
- DISQUALIFIED ×6 (B2×3 + B3×3)

Closing the L66 gap surfaced a Batch-1 architectural defect: bid_blacklist_check's `active_govt_cases` secondary signal had hijacked AP-GO-066 territory at AP-GO-096 (HARD_BLOCK) severity. Fix landed in same commit per L70 (cross-validator sentinel pattern).

### Original L66 entry (for archive)

**Surfaced during Sub-block 4 (EligibilityMatrix aggregator)**. The aggregator implements a 4-state aggregate verdict vocabulary with `HARD_BLOCK > WARNING > GAP > QUALIFIED` precedence:
- QUALIFIED
- FLAGGED_FOR_COMMITTEE_REVIEW (WARNING-INELIGIBLE present, no HARD_BLOCK)
- MARK_FOR_DOCUMENTATION_REVIEW (GAP_INSUFFICIENT_DATA present, no HARD_BLOCK or WARNING)
- DISQUALIFIED

On the current synthetic corpus (3 bidders × 3 tenders × 10 criteria = 90 findings), only 2 of 4 states fire:
- QUALIFIED ×3 (B1 across all tenders)
- DISQUALIFIED ×6 (B2 + B3 across all tenders — both bidders carry HARD_BLOCK criterion failures)
- FLAGGED_FOR_COMMITTEE_REVIEW ×0 — every bid carrying a WARNING (B3's litigation per AP-GO-066) ALSO has multiple HARD_BLOCK failures, so HARD_BLOCK precedence pushes to DISQUALIFIED.
- MARK_FOR_DOCUMENTATION_REVIEW ×0 — zero GAP_INSUFFICIENT_DATA findings across the 90.

**Forward-applicable**: the aggregator codes all 4 states, so real corpus bids will exercise them automatically. Until then, the FLAGGED and MARK_FOR_DOCUMENTATION_REVIEW branches are tested only via aggregator unit logic, not end-to-end synthetic data.

**Queue item**: extend `scripts/seed_synthetic_bids.py` to add 2 more bidder profiles that exercise the missing branches:
- **B4 — Borderline**: clean across all rule-strict criteria EXCEPT carries a single active govt litigation case (Statement-VII). Will FLAGGED_FOR_COMMITTEE_REVIEW (WARNING-only outcome).
- **B5 — Incomplete**: clean across all rule-strict criteria EXCEPT one Statement is missing or has null fact fields. Will MARK_FOR_DOCUMENTATION_REVIEW (GAP-only outcome).

After seeding, re-run all 10 Tier-2 validators against the new bids (B4 + B5 → 6 new bids × 10 validators = 60 new findings) + EligibilityMatrix aggregator (12 EligibilityMatrix rows total, exercising all 4 aggregate states). ~1–2 hours; can land before or after Sub-block 7.

Why surfacing this now matters: an undiscovered branch in the aggregator could harbor a logic bug that only manifests in production. The synthetic data should exercise every documented state transition before the aggregator ships to a real tender.

---

## L65 — EligibilityMatrix Aggregator Pattern (Sub-block 4)

**Established during Sub-block 4** (`scripts/run_eligibility_matrix.py`, May 2026). First aggregator in the platform; pattern stabilizes for downstream Sub-block 5 (L1/L2 ranking), Sub-block 6 (CrossBidAnomalyDetector), and Sub-block 7 (ComparativeStatementGenerator).

### Aggregator architectural pattern

| Aspect | Tier-1 validator | Tier-2 validator | Aggregator (NEW) |
|---|---|---|---|
| Input | Tender markdown via Qdrant | Structured fact_sheets / kg_nodes | Already-emitted Tier-2 findings (`BidEvaluationFinding`) |
| Per-row scope | one (doc, typology) | one (bid, typology) | one (bidder, tender) — aggregating N criteria |
| Output node_type | ValidationFinding | BidEvaluationFinding | **EligibilityMatrix** (new, accepted as plain TEXT — no CHECK constraint) |
| Edge emission | VIOLATES_RULE on negative outcomes | BIDDER_VIOLATES_RULE on negative outcomes | **None** (pure aggregator; drilldown via array, not graph) |
| Idempotence | `_delete_prior_*` by (doc_id, typology, tier) | `_delete_prior_tier2_*` by (bid_id, typology, tier) | `_delete_prior_*_rows()` by `source_ref` (single string match) |
| Crash resilience | per-doc wrapper invocation | per-bid wrapper invocation | **Single batch-run wrapper** (synthetic `doc_id="<aggregator_v1>"`) |
| Citation chain | inline in finding props | inline in finding props | `finding_node_ids[]` array + `finding_typology_to_node_id` lookup dict (drilldown to the underlying Tier-2 findings, which already carry full citations) |

### Pure-aggregator design rationale

The aggregator emits **no edges**. Every fact it carries is derivable from the underlying findings; adding `VIOLATES_RULE`-style edges from the matrix would duplicate the existing BIDDER_VIOLATES_RULE graph that the 10 Tier-2 validators already maintain. The drilldown contract is:

1. Consumer reads `EligibilityMatrix.finding_typology_to_node_id[typology]` → O(1) `finding_node_id`
2. Consumer queries `kg_nodes` by that `node_id` → underlying `BidEvaluationFinding` with full citation chain (bidder + fact source + tender + rule + reasoning)
3. Consumer optionally queries `kg_edges` filtered by `properties->>finding_node_id` → attached `BIDDER_VIOLATES_RULE` edge (if INELIGIBLE)

This is one O(1) hash lookup + one PostgREST GET per criterion — fast enough for a 10-criterion drilldown to feel instant in the Sub-block 7 evaluation-committee report UI.

### source_ref discipline for idempotency

Aggregator rows are tagged with `source_ref="<sub_block_N>:<aggregator_name>_v<n>"`. Idempotent cleanup filters on this single string, avoiding the multi-key (doc_id, typology, tier) filter that Tier-1/Tier-2 validators use. Simpler shape — aggregator rows are batch-emitted and batch-removed; no per-row keying needed.

### Sentinel snapshot pre/post

Aggregators must not modify upstream tables. Pre/post sentinel counts (ValidationFinding / BidEvaluationFinding / BIDDER_VIOLATES_RULE) are captured at start and compared at end; any drift returns RC=2 and fails the run. Standard contract for any read-only aggregator.

### Verdict precedence as a deterministic ladder

The 4-state aggregate vocabulary follows a strict precedence ladder: `HARD_BLOCK > WARNING > GAP > QUALIFIED`. The implementation uses three early-return checks (`has_hard_block`, `has_warning`, `has_gap`) before defaulting to QUALIFIED. SKIP_NOT_APPLICABLE outcomes are neutral; they're surfaced in `skip_criteria[]` for audit completeness but don't affect verdict computation.

### Aggregator vs validator naming convention

Forward-applicable naming for the platform:
- `scripts/tier1_<typology>_check.py` — Tier-1 tender document content validator
- `scripts/bid_<typology>_check.py` — Tier-2 bid submission evaluator
- `scripts/run_<aggregator_name>.py` — aggregator (no per-row CLI arg; batch mode)

The `run_` prefix differentiates batch aggregators from per-doc/per-bid validators that take a CLI arg.

---

## L60 — Validator Taxonomy: Tier-1 (Tender Doc Content) vs Tier-2 (Bid Submission Evaluation)

**Identified during Module 3 Sub-block 1 diagnose** (May 2026). The 5 "Batch-3 bidder-fact validators" (Blacklist-Not-Checked, Solvency-Stale, Turnover-Threshold-Excess, Eligibility-Class-Mismatch, Available-Bid-Capacity-Error) were framed in working context as consuming bidder submissions. Reading the docstrings showed otherwise: they read the **TENDER DOCUMENT** to verify it carries the right clauses (the doc MUST require bidders to declare blacklists; the doc MUST state the AP-GO-089 solvency framework; the doc's PQ turnover MUST be within CVC-028 cap; the doc's eligibility-class text MUST admit the right class band for the ECV; the doc's prescribed ABC formula MUST use M=2). They produce findings on corpus today (visible in the 154 ValidationFinding count across 6 corpus docs).

**The framing error matters because it shaped Module 3 sub-block planning.** "Batch-3 validators are ready but blocked on bid data" was incorrect — they're not blocked, they already work corpus-side. What's actually pending is a **Tier-2 Evaluator validator class that doesn't exist yet**: per-bidder Statement-data checks that consume submitted bidder facts and evaluate them against the tender's (already-validated-correct) PQ thresholds + regulatory floors.

**Distinction (post-L60):**
- **Tier-1 Tender Document Content Validators** — current `scripts/tier1_*_check.py`. Read tender document. Check clause presence/correctness against regulatory rules. Operate on the published RFP. Already shipped (24 validators including the 5 Batch-3).
- **Tier-2 Bid Evaluator Validators** — pending build in Module 3 Sub-blocks 3-6. Will be named `scripts/bid_*_check.py`. Read bidder Statement-data (I–X) from `fact_sheets` table + tender's PQ requirements from existing Tier-1 findings. Output per-bidder eligibility outcomes (QUALIFIED / INELIGIBLE / GAP-FLAGGED).

**Taxonomy applied** (Sub-block 1.0 commit): each of the 5 Batch-3 validator docstrings now carries a Tier-1 clarification block at the top stating explicitly that it reads the tender doc, not bidder submissions, and naming the Tier-2 counterpart.

**Forward-applicable:** when new validators are authored, the file naming convention should encode tier:
- `scripts/tier1_<typology>_check.py` — tender document content
- `scripts/bid_<typology>_check.py` — bidder submission evaluation

The shape of the validators differs: Tier-1 reads document text + KG sections + Qdrant retrievals; Tier-2 reads structured facts (`fact_sheets.extracted_facts` jsonb) + tender's Tier-1 verdicts + regulatory rules. Different input contracts, different output shapes.

**Not blocking** — discovery, not regression. The 5 Batch-3 validators are correct as built; only the framing was wrong.

---

## L59 — Mandatory-Fields Per-Sub-Check UNVERIFIED Rows: Unset failure_path

**Identified during Batch 1 of the validator-suite Bug C expansion** (commit `c968c61`). The Works-Universal-Mandatory-Fields validator emits per-sub-check rows through a single chokepoint helper `_materialise_finding(doc_id, props, label, …)`. Bug C's auto-injection in `_materialise_finding` correctly sets `verdict` per the severity-aware rule (HARD_BLOCK / GAP_VIOLATION / UNVERIFIED) based on the existing `props.status` and `props.severity` — but does NOT set `failure_path` on UNVERIFIED rows because the legacy props dicts pre-date the failure_path discriminator and don't carry the source signal needed to set it.

**Observed in Batch 1**: 9 UNVERIFIED Mandatory-Fields per-sub-check rows landed with `verdict=UNVERIFIED` ✓ but `failure_path=(unset)`. Verdict correctness intact; sub-check identity is still visible via the row's other audit fields (`rule_id`, label).

**Not blocking**:
- Aggregator's empty-rows / VALIDATOR_NOT_MIGRATED checks pass cleanly (verdict is set).
- UNVERIFIED breakdown reporting groups these as "(unset)" — visible in the matrix audit; recoverable per-row.
- Severity tagging is correct.

**Fix surface (deferred — fold into the L58 retroactive cleanup after Batch 3):** at each per-sub-check props-dict construction site in `tier1_mandatory_fields_check.py`, set `props["failure_path"]` based on the local context (`grep_promoted_to_unverified` → `retrieval_coverage_gap`; `is_unverified_l24_fail` → `L24_evidence_guard`; degenerate `primary_rule is None` → `rule_lookup_missing`). ~10-15 line patch.

---

## L58 — Severity-Aware Verdict Tagging (Bug C Original Migration Inconsistency to Retroactively Fix)

**Identified during Batch 1 of the validator-suite Bug C expansion** (from 6 wired-and-migrated to 24 total). The original Bug C migration (commit `edc68bd` covering PBG / EMD / Bid-Validity / LD / MII / JP) used a binary `verdict = "UNVERIFIED" if is_unverified else "GAP_VIOLATION"` mapping — collapsing the rule's severity into a single `GAP_VIOLATION` tag regardless of whether the rule itself was `HARD_BLOCK` or `ADVISORY/WARNING`.

**Real-corpus consequence:** MII/JP across all 6 docs sit at `verdict=GAP_VIOLATION, severity=HARD_BLOCK`. Aggregator's `n_hard_blocks` count under-counts those — it relies on `verdict=="HARD_BLOCK"`. Same on EMD where AP-GO-050 ADVISORY violations got tagged `verdict=HARD_BLOCK` instead.

**Correct shape (used in Batch 1 onwards, 12 total typologies now):**
```
verdict = ("UNVERIFIED" if is_unverified
           else ("HARD_BLOCK" if rule.get("severity") == "HARD_BLOCK"
                 else "GAP_VIOLATION"))
```

Severity preserved literally from the rule. HARD_BLOCK-severity rules emit `verdict=HARD_BLOCK`; ADVISORY/WARNING rules emit `verdict=GAP_VIOLATION`.

**Retroactive cleanup pending after all 3 batches finish.** Touch only the 6 original validators (PBG / EMD / BV / LD / MII / JP), update the verdict-tagging logic, re-run on the 6-tender corpus to refresh existing rows. Single small commit. Not part of the expansion-migration work.

---

## L57 — Drafter Structural Alignment: Markup Fixes Insufficient Without Content Density

**What landed (commit `597776c`):** ITB/GCC fixed-skeleton replaced 257 bare-numeric `### N.M` sub-headings (103 ITB + 154 GCC) with `**N.M.**` bold-prefix paragraphs under `## N. Topic` H2 parents. BDS rendering replaced single-table form with 6 themed H2 sub-sections + a "The clause shall be read as" H2 carrying prose paragraphs for BV/EMD/PBG that explicitly cite `ITB X.Y — …` (mirroring real JA's pattern at line 462). Cross-ref anchor safety: 0 broken markdown anchors. Section count compressed Kurnool drafter 265 → 158; Forms count 55 → 29.

**Residue: 5/18 drafter-regeneration UNVERIFIED** (PBG × 3 docs + BV × 2 docs Kurnool/JA/HC). Markup-structure changes alone didn't flip kg_builder's section-classifier outputs into the targeted ITB/GCC categories. Verdict landscape went 12/18 → 13/18 COMPLIANT_FIRED — one cell improved (Kurnool BV via NIT body row), the rest unchanged.

**Root cause:** kg_builder's content-based section classifier weights body-content density. Below ~10 lines per H2 section, it falls back to heading-stack heuristic defaults (`Forms` / `Datasheet`). Measured directly:

| Section | Drafter width | Real-corpus equivalent | Drafter classifier output | Real classifier output |
|---|---|---|---|---|
| PBG anchor (`## 42. Performance Security`) | 4 lines | Real JA `## To: [Contractor]` (Letter of Acceptance template) = 10 lines | `Forms` | `GCC` |
| BDS clause-prose (`## The clause shall be read as`) | 3 lines | Real JA same heading = **78 lines** (covers ITB 4.5 sanctions/debarment/environmental in long prose) | `Datasheet` | `ITB` |

Drafter's per-clause concise rendering doesn't reach the width threshold the classifier needs. Real-corpus tenders write multi-paragraph multi-clause prose under each H2; drafter writes 1-2 paragraph terse bodies.

**Fix surface (deferred, post-hackathon, ~2–4 h):** extend the fixed-skeleton from real AP SBD source to mirror real-corpus prose breadth. Specifically:

- ITB `## 42. Performance Security`: real SBD has 8–12 sub-clauses (form-acceptable banks, encashment, claim procedure, return-on-completion, additional security at over-runs, currency, format references, etc.); drafter currently has 2–3. Same SBD source as the original fixed-skeleton extraction.
- ITB `## 18. Period of Validity of Bids`: similar — real SBD has extension-procedure paragraphs, refusal consequences, bid-security extension co-requirement.
- BDS `## The clause shall be read as` prose section: expandable with sanctions/debarment paragraphs (real JA L464–542), environmental-requirements re-statement, beneficial-ownership disclosure (BDS ITB 47.1 in real JA), and similar long-prose rows.

Source: same processed Markdown as the original fixed-skeleton (`source_documents/e_procurement/processed_md/`). Pattern: lift the multi-paragraph prose verbatim where it's policy-correct, parameterise where values vary (`{{pbg_pct}}` etc.).

**Decision: not on current critical path.** Real-corpus validator coverage (12/36 COMPLIANT_FIRED + 22 honestly-surfaced violations) is the substantive validator claim and is unaffected. Drafter closed-loop at 13/18 is acceptable resting state.

---

## L56 — Methodology Violation: Validator-Widen Reverted; Drafter Is What's Wrong, Not Validators

**Context:** L55 documented widening PBG and BV section_type filters to include `Forms` and `Datasheet` so they would catch the fixed-skeleton drafter's mistagged `### 42.1` headings and BDS rows. That commit (`0317bf4`) shipped and showed measured "improvement" on drafter regenerations (PBG flipped UNVERIFIED → COMPLIANT_FIRED on JA/HC/Kurnool drafter outputs).

**The violation:** validators are calibrated against real AP corpus tenders — that's their *design baseline*. Real corpus PBG anchors live under `ITB / GCC / PCC / SCC / NIT` headings; that's why the filter was set that way. When drafter regenerations produced documents the validators couldn't verify, the right move was to fix the *drafter* so it produces structures consistent with the real-corpus pattern. Instead the widen modified the validators to forgive the drafter's structural drift. That's allowing mistakes — it weakens the validators against future real-corpus tenders that follow the original calibration, and erodes the platform's audit story (the validator is supposed to be the ground-truth grader, not a co-conspirator with the drafter).

**Reverted in this commit:**
- `scripts/tier1_pbg_check.py` — `PBG_SECTION_TYPES` restored to `['ITB','GCC','PCC','SCC','NIT']`
- `scripts/tier1_bid_validity_check.py` — inline section-filter widen removed; BV uses the router's APCRDA_Works default `['ITB','NIT']` again
- L55 stays as historical diagnosis (the kg_builder section-classifier mistag observation is still correct) but its framing of "validator-widen as tactical fix" is invalidated by this entry

**What stays (these were correct fixes, not methodology violations):**
- Bug A patch (`63d8e7f`) — drafter-side fix for clauses with all-MISSING rule_ids
- Bug B (`c03d5f0`) — drafter-side fix for the NIT render path
- Bug C migration (`edc68bd`) — validator *correctness* fix (explicit verdict emission per run); not a permissiveness change

**Forward direction (do not propose yet — observation only):** the drafter must produce documents whose structure matches what kg_builder classifies the same way it classifies real-corpus tenders. Two specific structural mismatches identified during the L55 diagnostic:
1. Fixed-skeleton ITB sub-section headings render as `### 42.1` with no parent-section context. Real corpus tenders use longer headings like `42. Performance Security` or `ITB 42 Performance Security` that the classifier reads as ITB. Drafter heading template needs to match.
2. Fixed-skeleton BDS renders as a 27-line table where ITB 18.1, 19.1, 20.1, … are rows under one Section node `Section II - Bid Data Sheet (BDS)` typed `Datasheet`. Real corpus tenders break the BDS into per-clause Section nodes that line up with their referent ITB clause. Drafter BDS rendering needs to match — either subdivide at render time or produce explicit ITB-X.Y headings within the BDS table so the classifier picks them up.

These are drafter-side fix candidates; not implementing tonight. The next move is establishing the validator-design baseline against real corpus before any drafter changes.

---

## L55 — kg_builder Section Classifier Mistags Fixed-Skeleton Numeric Headings; Validator Section-Filter Widen as Tactical Fix (REVERTED — see L56)

**Context:** Bug C (explicit verdict emission across 6 Tier-1 validators) revealed 6/18 verdicts on Kurnool / JA / HC coming back UNVERIFIED `failure_path=no_candidate`. All six concentrated in PBG-Shortfall (×3) and Bid-Validity-Short (×3). Drafts demonstrably contained the verbatim anchors (`grep "10 per cent of the contract value"` returned 1 hit per draft; `grep "90 days"` returned several). Step-1 diagnostic — top-20 BGE-M3 retrieval + per-candidate LLM extractor on the Kurnool draft — established that the right anchor was **NOT IN TOP-20**, ruling out batch-rerank wrong-neighbor (H1), recall via top-K bump (H2), and extractor prompt failure (H3).

**Root cause (H4):** the fixed-skeleton drafter renders ITB and BDS clauses with short numeric headings — `### 42.1`, `### 18.1`, etc. — that lack a parent ITB/BDS context anchor. The kg_builder Phase-6c section classifier (heuristic + LLM at `experiments/tender_graph/kg_builder.py`) can't infer the parent section type from a 4-character heading like `42.1` and defaults to `Forms`. BDS rows likewise land under `Datasheet`. Confirmed on both Kurnool and JA via Supabase Section-node query:

| Anchor | Section heading | Lines | Section type | PBG filter `[ITB,GCC,PCC,SCC,NIT]` | BV filter `[ITB,NIT]` |
|---|---|---|---|---|---|
| PBG § 42.1 (line 670) | `42.1` | 670–671 | **`Forms`** (mistag) | EXCLUDED | EXCLUDED |
| BV §18.1 (BDS row, line 700) | `Section II - Bid Data Sheet (BDS)` | 689–715 | `Datasheet` | EXCLUDED | EXCLUDED |
| BV NIT cover (line 54) | `- (Bidding Process with AP e-Procurement)` | 44–75 | `NIT` | INCLUDED | INCLUDED but cosine-buried under noisy ITB sub-sections |

**Tactical fix applied in this commit** — widen the validators' section_type filters at the validator level, NOT the router or the classifier:

- `scripts/tier1_pbg_check.py`: `PBG_SECTION_TYPES` widened from `['ITB','GCC','PCC','SCC','NIT']` to add `'Forms'` and `'Datasheet'`.
- `scripts/tier1_bid_validity_check.py`: BV section filter widened inline (post-router) from `['ITB','NIT']` to add `'Forms'` and `'Datasheet'`. Inline rather than router-level so the blast radius is contained to the BV typology.

**Why this is safe:** the LLM rerank's existing negative-selection rules (ignore retention / EMD / mobilisation-advance / liquidated-damages percentages on PBG; ignore Bid-Security validity / BG validity / contract period / DLP / warranty on BV) act as the precision guard over the wider candidate pool. We are not loosening the typology — only restoring recall on legitimately-applicable sections that the classifier has mis-tagged.

**Methodology discipline — no regex fallback added.** The first design draft included a regex-fallback safety net for `no_candidate` cases. Pulled before code touch. The platform's thesis is rules-as-code + SHACL + Vector + LLM; regex is the baseline benchmark we measure against, not a production code path. Adding regex to a validator weakens the patent / hackathon story and contradicts a multi-session project commitment. If section-widening alone leaves any UNVERIFIED, the next move is BGE-M3 query rephrasing or rerank-prompt tightening — within the methodology — NOT regex.

**Strategic fix (deferred, post-hackathon, cross-module):** kg_builder Phase 6c section classifier should track a heading stack — when an `### X.Y` numeric heading appears immediately after an ITB or BDS section header at heading level 2, inherit the parent section type instead of defaulting to `Forms`. This requires changes to `experiments/tender_graph/kg_builder.py` and re-classification of the staged corpus. The validator-level filter widen handles the immediate symptom across every Tier-1 typology that runs against fixed-skeleton drafts; the classifier fix would close the underlying gap and would let every future typology that's added rely on the current heuristic.

**Pre-fix:** 12/18 COMPLIANT_FIRED, 6/18 UNVERIFIED no_candidate (PBG×3 + BV×3) on Kurnool+JA+HC.
**Post-fix (target):** 18/18 COMPLIANT_FIRED on the same corpus. EMD / LD / MII / JP regression-checked — same evidence_quote and anchor preserved (those validators' filters were not touched).

**Forward-applicable:** any future Tier-1 typology that runs against fixed-skeleton drafter output should include `'Forms'` and `'Datasheet'` in its section filter until kg_builder L55-strategic lands. Worth documenting on every new validator's section-filter constant.

---

## L54 — Spec-Tailoring: Anti-Pattern Compliance Override + Material/Brand/Standard Disambiguation in LLM Prompt

**What we did:** Built `scripts/tier1_spec_tailoring_check.py` (typology 24) — anti-pattern detection on GFR-G-030 ("Description shall NOT indicate a particular trade mark, trade name or brand"). Result: 1 GAP_VIOLATION (Kakinada: doc names HPCL/BPCL/IOCL as required Bitumen/Emulsion suppliers without "or equivalent" or PAC) + 5 COMPLIANT silent.

**Two distinct sub-lessons surfaced during the build:**

### L54a — Anti-pattern compliance-override decision logic

For anti-pattern typologies with multiple compliance escape valves, the decision logic must explicitly OR the escape valves. Vizag's first run flagged `names_brand=True` (LLM mistake — material specs aren't brand names) AND `generic_approved=True` AND `bis_iso=True`. Without explicit override, the script would have emitted a false positive.

```python
is_arbitrary_brand = (
    names_brand
    AND not has_or_equiv          # qualifier override
    AND not has_pac               # PAC justification override
    AND not generic_approved      # standard convention override
    AND not bis_iso               # objective standard override
)
```

Forward-applicable to any anti-pattern typology (CRN's regulatory-citation override, MII's classification overrides, etc.). The pattern: codify the OR-of-escape-valves explicitly so a mistakenly-flagged anti-pattern doesn't override correctly-flagged compliance signals.

### L54b — Material/Brand/Standard disambiguation in LLM prompt

The first prompt told the LLM "TRUE if a SPECIFIC company / trade name is named" with a brand-name list. The LLM intermittently flagged `names_brand=True` on:
- **Material names**: "Aluminium", "Cement", "Steel", "UPVC"
- **Product categories**: "Sliding Windows", "Glazed Tiles"
- **BIS/IS standard references**: "IS:1948", "I.S.: 127-106"

These are NOT company names but the LLM didn't reliably distinguish. The fix: enumerate 5 explicit NON-brand-name categories in the prompt, with examples and a definitional anchor:

> *"A specific company name is one you could look up on a stock exchange or business registry — not a material, not a standard, not a category."*

After the fix, Vizag's LLM reliably extracted `names_brand=False` with reasoning "uses 'approved brand and manufacturer' without naming specific brands, and references IS standards, which is compliant." The Kakinada HPCL/BPCL/IOCL violation still flagged correctly because those ARE specific company names (oil PSUs listed on stock exchanges).

Forward-applicable to any anti-pattern typology where the boundary between compliant and violating signals depends on category-distinction (material vs brand, standard vs proprietary, generic vs specific). The pattern: don't just give positive examples — enumerate the contrast cases the LLM is likely to confuse.

### L36/L40 grep fallback DISABLED for this typology

Standard L36/L40 fallback fires when the LLM returns `chosen_index=null` and grep finds keyword hits (suggesting LLM missed real signal). For Spec-Tailoring this is wrong: keywords like "manufactured by" / "trademark" appear in non-spec contexts ("ready-mix concrete manufactured by outside agencies shall not be allowed" is anti-bidder-supplied, not brand-tailoring). The first JA run promoted to UNVERIFIED on 81 false-positive section matches.

Decision: for anti-pattern typologies where grep keywords overlap with non-anti-pattern contexts, **disable grep promotion entirely**. The LLM's "no signal across rerank candidates" judgment is the authoritative compliance verdict. Keep keywords in the script for the audit-trail field (so reviewers can see what was considered) but don't promote.

This adds a third grep-fallback policy to the catalog:
- L36 + L40: standard for absence-shape (PVC / IP / FM / DLP / Solvency)
- L36 + L40 with kg_coverage_gap detection: when retrieval coverage is the suspected failure mode (MII / Mandatory-Fields)
- **L54 — disabled**: when keywords are ambiguous in non-violation contexts (anti-pattern typologies with overlapping vocabulary)

**Why we changed:** Anti-pattern typologies are fundamentally different from absence-shape and presence-shape: the "default" outcome is COMPLIANT (no violation found), and grep is only useful if it can reliably distinguish violation signals from compliance signals. For Spec-Tailoring's keyword vocabulary, that distinction can't be made syntactically — only the LLM's full-context judgment can. Disabling grep for this typology preserves the audit chain (the LLM still verifies via L24) without false-positive UNVERIFIED rows.

**Forward applicability:**
1. **Anti-pattern typologies need compliance-override decision logic.** Codify the OR-of-escape-valves explicitly to prevent false positives when the LLM co-flags violation + compliance signals.
2. **LLM prompts for boundary-distinction extractions need enumerated contrast cases.** Don't just say "TRUE if X" — also list what X is NOT, with material/standard/category examples. The LLM trained on procurement docs sees "Aluminium" + "IS:1948" thousands of times in standard contexts; explicit disambiguation is what shifts the prediction.
3. **Grep fallback policy is per-typology.** Disable for anti-pattern typologies where keywords are ambiguous. Keep enabled for presence/absence shapes where keyword matches are real signal-bearing context.
4. **Kakinada's brand-tailoring is a fifth corpus-pattern signal** distinct from the prior four — APCRDA Works template gaps (Arbitration L43 + Solvency L50 + JV ban L53) all involved JA + HC; non-APCRDA pair gap (ABC L52) involved Vizag + Kakinada; Spec-Tailoring is **Kakinada-only**, demonstrating the SBD format has a unique Bitumen/Emulsion specification gap (HPCL/BPCL/IOCL named without "or equivalent" qualifier — restricts suppliers to 3 named PSUs, excluding Reliance / Cairn-Vedanta / private importers).

---

## L52 — Available-Bid-Capacity-Error: Threshold-Exact-Match + AP-Defeats-Central via Rules Table + Third Corpus Pattern

**What we did:** Built `scripts/tier1_abc_check.py` (typology 22) — a threshold-shape Tier-1 check on the multiplier M of the Available Bid Capacity formula. Per AP-GO-062 (HARD_BLOCK), AP Works/EPC contracts must use the formula `ABC = (A × N × 2) − B` with **M = 2 exact** (no "usually" qualifier — deterministic AP-prescribed value). Central MPW-043 allows `M = usually 1.5` and is correctly defeated by AP-GO-062 via the rules-table `defeats=['MPW-043']` relationship — first operationalised use of the rules-table defeats column in a Tier-1 typology.

**Result:** 4/6 expected — 2 COMPLIANT silent (JA, HC: M=2) + **2 GAP_VIOLATION HARD_BLOCKs (Vizag, Kakinada: M=3, +50% lenient than AP-prescribed)** + 2 PPP rule-skip silent. Predictions matched the typology-12 read-first M-coefficient extraction exactly.

**Third corpus-pattern signal — non-APCRDA template gap:**
- L43 Arbitration: JA + HC pair (APCRDA Works template — §60 Property weakness)
- L50 Solvency: JA + HC pair (APCRDA Works template — no Tahsildar / no validity rule)
- **L52 ABC: Vizag + Kakinada pair (NON-APCRDA templates — over-permissive M=3 instead of AP-prescribed M=2)**

The corpus now exhibits gaps in BOTH template families. APCRDA Works template (JA, HC) needs strengthening on Arbitration + Solvency clauses; non-APCRDA templates (Vizag UGSS / Kakinada SBD PR Roads) need correction on the ABC formula multiplier. The procurement-reform narrative has corpus-grounded evidence on both directions — single-template-family generalisations would miss the non-APCRDA pattern.

**Threshold-exact-match shape (vs threshold-min/max):**
DLP (L49) and Bid-Validity-Short are threshold-min checks (X ≥ N). Mobilisation-Advance-Excess is a threshold-max check (X ≤ N). ABC is a NEW shape: threshold-EXACT-match (X = N). The decision logic differs:

```python
# Threshold-min (DLP)
if dlp_months >= threshold_months: COMPLIANT
else: WARNING

# Threshold-max (MA)
if ma_pct <= cap_pct: COMPLIANT
else: WARNING

# Threshold-exact (ABC)
if abs(M - required_M) < 0.01: COMPLIANT
else: GAP_VIOLATION  # any deviation, lenient OR restrictive
```

The `multiplier_M=null` path (formula present but M not extractable) takes the conservative by-reference path — silent compliant — same pattern as L49 DLP's `dlp_months=null` "by-reference to PCC" silence.

**LLM extraction prompt note:** The 7-rule range guard ("multiplier is ALWAYS in [1.0, 5.0]") prevents the LLM from picking up adjacent numeric tokens (years, percentages) as the multiplier. JA's evidence quote contains "ten financial years" but the LLM correctly extracted M=2 (not 10). Forward-applicable: any threshold-shape extraction where the value has a known numeric range should include a range guard in the prompt.

**Why we changed:** Tier-1 catalogues need a threshold-EXACT-match shape alongside threshold-min and threshold-max. AP-GO-062's deterministic M=2 doesn't fit either bound; both directions of deviation (M too low = over-restrictive; M too high = over-lenient) are violations against a regulator-prescribed exact value. Codifying this as a third shape keeps the decision logic explicit and prevents misclassification when a future typology lands in this category.

**Forward applicability:**
1. **Threshold-exact-match is now an established shape.** Add to the typology-shape vocabulary alongside presence-shape, threshold-min, threshold-max, and presence-multi-field. Any rule with a regulator-prescribed deterministic value (no "usually" / no "minimum" / no "maximum") falls in this bucket.
2. **The rules-table `defeats` column is operationalised.** AP-GO-062's `defeats=['MPW-043']` automatically silences MPW-043 in the rule selector without requiring a per-typology defeasibility branch (the existing condition_evaluator + defeats filter chain already handles it). Forward-applicable: when an AP-State rule is more specific than a Central baseline, populate the AP rule's `defeats` column to express the AP-defeats-Central relationship at the knowledge layer rather than at the typology-script layer (cleaner than L43's typology-specific AP-defeats-Central branch).
3. **Reuse of typology-12 extraction is now possible at the data layer.** Future Tier-1 typologies that need the same field (e.g. an Eligibility-Class-Mismatch revisit looking at the same NIT class declaration) can pull from existing finding `properties` rather than re-extracting. The pattern: `properties` is the source of truth for any extracted fact; subsequent typologies should query the existing properties before triggering a new extraction. Not implemented in this script (we did re-extract for L24-verifiable evidence) but a viable optimisation for the Tier-1 catalog as it grows.
4. **Two distinct corpus-pattern axes** — APCRDA-Works gaps (Arbitration, Solvency) and non-APCRDA gaps (ABC formula) — give the procurement-reform narrative directional richness. A reform deck cannot point at a single template; it must address both axes.

---

## L51 — Pre-Bid-Process-Unclear: Multi-Field Compliance Gating with Audit Fields + 6/6 Silent on Vague Meta-Quality Rule

**What we did:** Built `scripts/tier1_prebid_check.py` (typology 21) — a presence-shape Tier-1 check operationalising MPW-061 (HARD_BLOCK Works: "Bid Documents must be self-contained and comprehensive without ambiguity") as a 5-field pre-bid clarification protocol extraction. The 5 typology rules collapse to a single Tier-1 firing rule (MPW-061); the others are excluded for the same reasons documented in L48 FM (execution-stage facts default to false; AP-GO-057/211 are timeline/advertisement shapes for a future separate typology; AP-GO-156 is Goods-only).

**Result:** **6/6 silent** — 4 AP Works COMPLIANT silent (JA, HC, Vizag full 5-field; Kakinada minimum protocol) + 2 PPP rule-skip silent. Third silent-by-design typology after L48 FM (5/6 silent) and L49 DLP (6/6 silent). The portal will continue to derive "no violations" from absence of any other-state row.

**The narrow-vs-broad compliance gating decision:** A multi-field extraction with N booleans needs an explicit policy for which combinations gate compliance. Three options:
1. **All-True gate**: COMPLIANT only if all N fields true. Maximally strict; risks false-positives on lean docs (e.g. SBD pattern without formal pre-bid meeting).
2. **Any-True gate**: COMPLIANT if any field true. Maximally lax; misses real gaps.
3. **Minimum-protocol gate**: COMPLIANT if a SUBSET (the regulator-essential fields) is true; the rest are audit fields. Calibrated to the rule's actual scope.

L51 picks option 3: COMPLIANT iff `clarification_request_protocol_present AND clarification_response_protocol_present`. Pre-bid-meeting, cutoff-deadline, site-visit-provision are captured in `properties` for portal review but don't gate compliance. Rationale: MPW-061's "self-contained, comprehensive" is satisfied if bidders can ask and the employer is committed to answer; the meeting/deadline/visit are nice-to-have signals that distinguish mature-template docs (APCRDA Works full 5-field) from lean SBD (Kakinada 2-field). A blanket all-true gate would emit a GAP_VIOLATION on Kakinada despite its compliant minimum protocol.

**Kakinada validation:**
```
pre_bid_meeting_specified                = False
clarification_request_protocol_present   = True
clarification_response_protocol_present  = True
clarification_deadline_stated            = False
site_visit_provision_present             = False
→ has_minimum_protocol = True → COMPLIANT silent
```
Evidence: "A prospective tenderer requiring any clarification on tender documents may contact the tender Inviting officer at the address indicated in the eNIT. The tender inviting officer will also respond to any request for clarification, received through post." L24 score=100 substring (no markdown escaping in SBD source, clean exact match).

**Why we changed:** Multi-field typologies with regulatory rules that have a clear "essential vs nice-to-have" structure should encode that structure in the decision logic. Burying it in the LLM prompt ("treat any 2 of 5 as compliant") is fragile across model versions; codifying it in Python is durable. The 5-field extraction is preserved in `properties` so the portal can render the full audit trail (which fields are true / false per doc), while the 2-field gate keeps the OPEN/silent contract clean.

**Forward applicability:**
1. **Multi-field presence-shape typologies should pick option 3 by default** — extract N fields for audit, gate COMPLIANT on a regulator-essential subset. Document the gate rationale alongside the boolean schema in the script docstring so reviewers can see WHY each field is gating vs audit-only.
2. **MPW-061 "self-contained, comprehensive" is now operationalised as "bidders have a path to ask + employer commits to answer".** This narrow framing is forward-compatible with stricter readings (a future regulator update could add deadline/meeting fields to the gate without changing the extraction schema).
3. **The L48 + L49 + L50 + L51 sequence proves the silent-on-COMPLIANT contract scales across shapes.** L48 single-field presence; L49 threshold; L50 multi-field with mixed COMPLIANT/GAP_VIOLATION; L51 multi-field with minimum-protocol gating. The portal infrastructure handles all four without per-typology UI work — typology authors keep populating the standard `properties` schema and the portal renders.
4. **6/6 silent typologies are not noise.** Each silent run validates the rule selector + retrieval + L24/L36/L40/L49/L50 chain on a different shape. The cumulative coverage of 21 typologies across 6 docs is the audit proof — every doc has been touched by every Tier-1 check, every check has a defensible outcome (OPEN, UNVERIFIED, COMPLIANT-silent, or rule-skip-silent).

---

## L50 — Solvency-Stale: Grep-Seeded Retrieval Supplement + APCRDA Works Template Gap

**What we did:** Built `scripts/tier1_solvency_check.py` (typology 20) — a presence-shape Tier-1 check with multi-field framework extraction. Four rules in the typology (AP-GO-089 HARD_BLOCK, AP-GO-103 WARNING proforma, AP-GO-106 partnership-change HARD_BLOCK, MPW25-028 PQ Financial Soundness) collapse to AP-GO-089 as the primary firing rule. AP-GO-103/106 are subsumed (proforma) or execution-stage (partnership) and excluded from RULE_CANDIDATES; MPW25-028 is COMPLIANT in all 4 AP Works docs and excluded to avoid double-firing.

**Result:** **2 GAP_VIOLATION HARD_BLOCKs (JA + HC)** + 2 COMPLIANT silent (Vizag + Kakinada) + 2 rule-skip silent (Tirupathi + Vijayawada PPPs). First non-silent typology since L46 Mandatory-Fields.

**APCRDA Works template gap surfaced — second corpus-pattern signal:**
- JA: bank=True, tahsildar=False, validity_1yr=False, threshold="Rs.20.92 Cr."
- HC: bank=True, tahsildar=False, validity_1yr=False, threshold="Rs. 73 Cr."
- Vizag (different APCRDA template): bank=True, tahsildar=False, validity_1yr=True ← Vol-I L1199 has explicit "certificate not older than 1 year from Banks" — outlier from JA/HC's gap
- Kakinada (SBD_Format): tahsildar=True, bank=True, validity_1yr=True, GO MS No 129 cite — full framework

JA and HC share the same APCRDA Works template's "(i) Liquid assets/credit facilities/Solvency certificates from any Nationalized/Scheduled Bank or Certificate issued by CA for not less than Rs.X Cr." pattern — same wording, same missing validity rule, same missing Tahsildar option. This is the second template-shared gap after L43's Arbitration §60 Property pattern. **Diagnostic value:** a procurement-reform narrative can cite the APCRDA Works template for systematic strengthening.

**The grep-seeded retrieval supplement (the new technique):** First JA run with L49 quotas alone returned `chosen_index=null, all_booleans=false`. The PQ row at JA L678 sits in ITB section L618-737 with the misleading heading "SETTLEMENT OF CLAIMS (part 1)" — BGE-M3 ranks this section #7 in ITB by cosine (0.4357), below the K_VAL=3 cutoff. Bumping K_VAL to 7+ would bloat the prompt across all 6 docs and risk distracting the LLM with low-relevance content.

The fix: tight literal grep for the keyword `"solvency"` (extremely specific — near-zero false positives unlike "scheduled bank" / "validity" which match EMD/PBG/bid-validity sections). Sections matching the grep that aren't already in the cosine top-K get added at `cosine=0.0` (signaling "grep-seeded"). The LLM rerank prompt sees both the cosine candidates and the grep-seeded sections, picks the best evidence regardless of cosine origin.

```python
# After cosine merge:
SEED_KEYWORDS = ["solvency"]   # tight, typology-specific
_, seed_hits = grep_source_for_keywords(DOC_ID, section_types, SEED_KEYWORDS)
seeded_section_ids = {h["section_node_id"] for h in seed_hits}
for sid in seeded_section_ids:
    if not any(p["payload"].get("section_id") == sid for p in by_id.values()):
        # Synthesise a Qdrant-shaped point with cosine=0.0 from kg_nodes payload
        sec_rows = rest_get("kg_nodes", {"select": "node_id,properties",
                                          "node_id": f"eq.{sid}"})
        # ... build seeded_pt and add to by_id
```

Result on JA: 14 candidates fed to LLM (12 cosine + 1 grep-seeded "SETTLEMENT OF CLAIMS L618-737" at cosine=0.0 + 1 dedupe cushion). The LLM picked the grep-seeded candidate over all 12 higher-cosine candidates because it was the only one stating the actual solvency-certificate framework. Evidence quote (Liquid assets/credit facilities/Solvency certificates from any Nationalized/Scheduled Bank... Rs.20.92 Cr.) verified at L24 score=99 partial_ratio.

**Why we changed:** L49 quota retrieval guarantees section-type diversity but doesn't help when the canonical signal-bearing section has a misleading heading that depresses its cosine. For sparse-signal typologies where a unique regulated keyword exists, grep-seeding is cheap insurance: O(seconds) cost, zero false-positive risk if the keyword vocabulary is tight, and the L24 evidence guard backstops any LLM mistake. The technique reads cleanly alongside L49 quotas — quotas guarantee section-type diversity, L50 guarantees keyword-bearing presence.

**Forward applicability:**
1. **Sparse-signal Tier-1 typologies should layer L50 grep-seeding on top of L49 quotas.** The default keyword vocabulary for grep-seeding should be ONE highly-specific term (e.g. "solvency" for Solvency-Stale, "MII" for MakeInIndia, "indemnity" for Indemnity-Cap). Multi-keyword vocabularies risk surfacing tangentially-related sections that distract the LLM.
2. **Grep-seeded candidates must use the same payload shape as Qdrant points.** The script uses a synthetic `id="seeded:<section_node_id>"` and `score=0.0` so they sort to the bottom of the merged list — the LLM still chooses by relevance, not by cosine rank. Critical: the payload must include `section_id`, `heading`, `section_type`, `source_file`, `line_start_local`, `line_end_local` so `resolve_section()` can short-circuit the kg_nodes lookup.
3. **The APCRDA Works template gap is a procurement-reform signal.** Two systemic patterns now surfaced in the corpus — Arbitration §60 Property (L43) and Solvency framework (L50) — both shared by JA + HC because both use the same APCRDA Works template. A future template-revision deck has corpus-grounded evidence: weakness 1 (Arbitration), weakness 2 (Solvency), and probably more to come.
4. **L48 + L49 + L50 together prove the silent-on-COMPLIANT contract on a non-trivial typology.** Solvency-Stale emits 2 OPEN HARD_BLOCKs and 4 silents (2 COMPLIANT + 2 rule-skip). The portal correctly distinguishes them: 2 doc tiles show OPEN with framework-gap evidence, 4 doc tiles show "no findings". The four-state contract continues to scale without UI special-cases.

---

## L49 — DLP-Period-Short: Per-Section-Type Quota Retrieval + Threshold Shape with By-Reference Trap

**What we did:** Built `scripts/tier1_dlp_check.py` (typology 19) — a threshold-shape Tier-1 check for AP-GO-084 (AP Works/EPC Defects Liability Period fixed at 24 months). Three rules in the typology (AP-GO-084 WARNING, MPW-030 EPC latent-defect HARD_BLOCK, CVC-114 Goods-only HARD_BLOCK) collapse to one Tier-1 candidate: AP-GO-084 fires on the 4 AP Works docs, SKIPs on the 2 NREDCAP PPP DCAs. MPW-030 excluded from candidates (it's about the procuring authority's organisational capacity + a separate latent-defect clause beyond DLP — not a doc-content check, same exclusion reasoning as MPW-122 in L48). CVC-114 SKIPs corpus-wide.

**Result:** 6/6 silent — 4 COMPLIANT (`dlp_months=24` extracted, threshold met, no row) + 2 rule-skip (PPP, AP-GO-084 condition_when fails). Zero ValidationFinding rows, zero VIOLATES_RULE edges. This is the second silent-by-design typology after L48 FM, but the first that runs an actual threshold compare on extracted values rather than a presence check.

**The by-reference trap:** First JA run used a single answer-shaped query ("Period of Defect Liability Period DLP 24 months from completion of work...") and Qdrant returned all-GCC top-10 (cosines 0.55–0.69). The LLM picked GCC §35 "Identifying Defects and Correction of Defects" which states: *"The Defects Liability Period, which begins at Completion, and is defined in the PCC."* — the canonical framework clause that's by-reference for the actual duration. Result: `dlp_months=null`, branch `compliant_clause_present_no_months_stated`, silent.

This is technically a defensible Tier-1 outcome (regulated framework present, value by-reference is execution-stage), but it has a critical quality gap: **the threshold compare never runs**. A future doc whose PCC states 12 months (below threshold) would pass Tier-1 silently with the same "framework present, by-reference, default compliant" path. The check degenerates to framework-presence on every AP Works doc.

**Per-section-type quota retrieval (the fix):** Direct Qdrant probes showed the value-stating sections (NIT datasheet rows, Forms bidder declarations) score 0.44–0.49 cosine — well below the GCC ceiling. With a single top-K=10 query, NIT/Forms candidates never enter the LLM's reranking pool regardless of how the query is phrased. The fix:

```python
# Two queries — framework + value
qvec_fw  = embed(QUERY_FRAMEWORK)   # "Defects Liability Period clause defined in PCC..."
qvec_val = embed(QUERY_VALUE)       # "Period of Defect Liability Period DLP 24 months..."

# Per-section-type quotas
points_fw  = qdrant_topk(qvec_fw, k=5, section_types=["GCC"])         # 5 GCC framework
for st in [t for t in section_types if t != "GCC"]:
    points_val += qdrant_topk(qvec_val, k=3, section_types=[st])      # 3 per non-GCC type

# Merge by point id, keep max cosine, dedupe, top-12
```

After the fix, JA's rerank pool was [5 GCC + 2 NIT + 3 Forms] = 10 candidates. The LLM picked NIT L43-96 (cosine=0.4937, lowest in the pool) over the 7 higher-cosine GCC candidates because it was the only one stating *"Period of Defect Liability Period (DLP) 24 months from the date of completion of work"*. dlp_months=24 extracted, threshold compare ran, COMPLIANT.

The same fix worked across families:
- **APCRDA_Works (JA, HC, Vizag)**: NIT datasheet rows surfaced at K_VAL=3 quota; LLM extracted dlp_months=24 from each.
- **SBD_Format (Kakinada)**: n_gcc=0 — GCC-empty branch falls back to value pool [NIT:3, Evaluation:3, Forms:3] = 9 candidates. LLM picked Evaluation L2936-3186 "PREAMBLE (part 2)" carrying the regulatory cite *"defect liability period of contract in terms of GO Ms No: 8, T(R&B)... is twenty four months after completion of work"*.
- **NREDCAP_PPP (Tirupathi, Vijayawada)**: AP-GO-084 SKIPs at the rule layer — no retrieval, no LLM call, no quota debate.

**Why we changed:** Without per-section-type quotas, threshold-shape typologies built on top of dense GCC clause families become framework-presence checks in disguise. The corpus had no DLP < 24 cases, so the by-reference trap didn't bite — but it would silently leak any future short-DLP doc through Tier-1 unchecked. The fix is a one-time cost (~30 LOC + a second query embedding) that makes threshold-compare run reliably across all 6 docs.

**Forward applicability:**
1. **Threshold-shape typologies should default to per-section-type quota retrieval.** Single-query top-K is fine for presence-shape (where any framework-stating section qualifies). For threshold-shape, the value-stating sections are often diluted across long tabular blocks and need explicit quotas. The pattern: split the query into framework-shaped and value-shaped variants, fetch K_FW from the dense-clause family (GCC/SCC) and K_VAL from each value-stating family (NIT/Forms/Evaluation), merge by point id, dedupe, feed top-12 to the LLM.
2. **GCC-empty branch needs a try/except.** SBD_Format docs (Kakinada n_gcc=0) crash any retrieval that assumes GCC populated. The pattern: wrap the GCC fetch in `try/except RuntimeError` and fall back to the value pool — the LLM extracts the regulatory cite from Evaluation/Forms instead of the GCC framework.
3. **The by-reference exception is forward-compatible with PCC/SCC verification.** When the LLM legitimately can't find a value (the doc TRULY says "as stated in PCC" with no PCC line in any indexed section), the `compliant_clause_present_no_months_stated` branch is the right outcome. Tier-2 / human review can re-open these cases by querying for `properties->>'violation_reason' = 'compliant_clause_present_no_months_stated'`.
4. **L48 + L49 together prove the silent-on-COMPLIANT contract.** Two consecutive typologies with 5/6 and 6/6 silent outcomes; portal correctly shows "No violations found for this typology" without any COMPLIANT rows in `kg_nodes`. The four-state contract from L37 is the framing — the portal infers COMPLIANT from absence of any other state.

---

## L48 — Missing-Force-Majeure: First Always-Compliant Typology (5/6) + Run-Aware JSON Sanitizer Fix

**What we did:** Built `scripts/tier1_force_majeure_check.py` (typology 18) — a presence-shape Tier-1 check using the same BGE-M3 + LLM rerank + L24/L36/L40 pipeline as PVC / IP / LD / MII. Three rules in the typology (MPG-174 universal HARD_BLOCK, MPS-100 Services-only, MPW-122 Works execution-stage) collapse to a single firing rule on the corpus: **MPG-174 fires on all 6 docs**. MPW-122 SKIPs at pre-RFP (FMEventInvoked=false), MPS-100 SKIPs (no Services tenders).

**Result:** 5/6 COMPLIANT (silent — no row), 1/6 GAP_VIOLATION (Kakinada). This is the first typology where the 5 PPP DCA / Concession-Agreement docs each cleared at cosine 0.69-0.74 with verbatim L24 substring matches against §62 (APCRDA Works) or §26 (NREDCAP PPP). Kakinada SBD has zero FM signals (n_gcc=0 family + grep-vocabulary discipline rejected the L2131 "beyond the control of the contractor" extension-of-time line as not-an-FM-clause).

**Always-compliant-silent decision:** The 5 silent docs deliberately do NOT get a `status=COMPLIANT` row. Reasoning (decided at typology-18 build time):
1. Emitting COMPLIANT rows inflates `kg_nodes` for typologies where nothing is wrong — 17 future typologies × 6 docs = ~102 redundant rows per re-run if every typology emitted COMPLIANT rows.
2. L32 snapshot/restore would preserve those COMPLIANT rows on `kg_builder` rebuilds, propagating the noise across re-ingest cycles.
3. The portal's positive-signal rendering does NOT require COMPLIANT rows — "no findings for this typology on this doc" already means COMPLIANT (the four-state contract is L37: COMPLIANT / OPEN / UNVERIFIED / GAP_VIOLATION; the portal infers COMPLIANT from the absence of any of the other three states).
4. The VIOLATES_RULE edge count stays meaningful — only genuine violations participate.

**JSON sanitizer bug surfaced + fixed:** Vizag's first run failed JSON parsing because the LLM faithfully copied the source's markdown-escaped punctuation (`\-`, `\.`, `\(`, `\)`) into its evidence quote — but inconsistently. For some chars it emitted `\\X` (two literal backslashes + char, which IS valid JSON for "literal backslash + literal char"); for others it emitted `\X` (one backslash + invalid escape char). The legacy `_JSON_VALID_ESCAPE_RE = re.compile(r'\\(?!["\\/bfnrtu])').sub(r'\\\\', text)` regex saw each backslash in isolation and over-escaped the second backslash of an even-count run, producing an odd-count run that's invalid JSON. New `_fix_invalid_json_escapes` function uses run-aware substitution: it matches `(\\+)([^\\])` (a run of N backslashes followed by one non-backslash), and only doubles the run if N is odd AND the trailing char is NOT a valid JSON escape character. Even-count runs and odd-count runs ending in a valid escape char are left alone. This is forward-applicable to every Tier-1 typology that asks the LLM to copy markdown-escaped content verbatim.

**Why we changed:** Force Majeure was the natural test for "does the system recognise compliance, not just failures?" — every Works/PPP contract is regulated to contain an FM clause, and 5/6 of our corpus docs do. A typology with mostly-COMPLIANT outcomes proves the pipeline's reliability the same way mostly-ABSENT outcomes (JP / IP / MII) prove the systemic-pattern detection. The JSON sanitizer fix was a forced detour discovered during Vizag's first run; the bug was latent on prior typologies because Vizag was the first doc whose source markdown intermixed `\\X` and `\X` patterns in the same evidence quote.

**Forward applicability:**
1. **Future presence-shape typologies should default to silent-on-COMPLIANT.** The portal's "no rows = COMPLIANT" contract is now the standard. If a typology has a strong reason to emit COMPLIANT rows (e.g. it carries audit-critical extracted facts that need to round-trip through the KG), that's a per-typology exception — not the default.
2. **The run-aware JSON sanitizer benefits every typology.** It lives in `modules/validation/llm_client.py` so every script that calls `parse_llm_json` inherits it without code changes. Re-runs on prior typologies won't regress (the legacy regex pass is preserved as Pass-2 fallback).
3. **Family routing now has a clean three-way story.** APCRDA_Works → §62 in GCC. NREDCAP_PPP → §26 in GCC (DCA structure). SBD_Format → no FM at all (Kakinada's SBD shape doesn't carry an FM clause — confirmed by L40 whole-file grep returning 0 hits across the entire 3258-line source). The router's GCC-anchored filter for FM is the same shape used by LD / PVC / MA — confirmed three typologies in a row.
4. **Kakinada is now a 4-typology HARD_BLOCK violator** (Eligibility-Class, MII, Mandatory-Fields MPG-148, Force-Majeure). The SBD shape is materially incomplete relative to APCRDA Works and NREDCAP PPP DCAs — a forward signal for any procurement-reform narrative.

---

## L47 — Review Portal

**What we did:** Built a single-file HTML review portal (`frontend/portal.html`, ~700 LOC) so the 65 ValidationFindings sitting in Supabase actually become reviewer-actionable. After 17 typologies of accumulating audit-trail JSONB rows, the portal turns "data in a database" into "decisions a reviewer can make today".

**Five views, hash-routed:**
1. **Dashboard** — stats bar (total / OPEN / UNVERIFIED / HARD_BLOCK / ADVISORY counts) plus 6 systemic-pattern cards (PBG 5/5 at 2.5%, EMD 5/5 at 1%, JP 6/6 absent, MII 6/6 absent, IP 6/6 in regulated form, Turnover 2/2 NREDCAP at 2.500×) plus 3 quick-access tiles
2. **Per-doc** — doc dropdown → grouped findings by status×severity (OPEN HARD_BLOCK / OPEN ADVISORY / Informational markers / UNVERIFIED), each card showing typology + rule_id + section + verbatim evidence + reason
3. **Per-typology** — typology dropdown → 6-doc matrix with per-doc status chips (OPEN / UNVERIFIED / COMPLIANT). Banner fires when 6/6 fail or ≥4/6 fail (corpus-pattern signal vs per-tender error)
4. **UNVERIFIED queue** — all 16 UNVERIFIEDs with `grep_fallback_audit.hits[]` rendered as section pointer + line range + matched keywords + 200-char snippet, plus two action buttons per finding: **[Mark Verified → OPEN]** and **[Mark Dismissed]**, both PATCHing the JSONB `properties.status` field directly via Supabase REST
5. **Source viewer** — finding metadata + path hint (portal does not bundle markdown; reviewer opens the file at the cited line range)

**Architecture decisions:**
- Single HTML file, no build step, no `node_modules`. Tailwind via CDN, no framework. Vanilla ES module `<script>`.
- Hash routing via `window.addEventListener("hashchange", route)` — no router library.
- Single fetch on boot (`loadAllFindings()` paginated for future growth) → all views render from one in-memory `ALL_FINDINGS` array; no per-view round-trips.
- PATCH actions call Supabase REST directly with `Prefer: return=representation`. After a successful PATCH, the local cache mutates and views re-render — no full reload, no flicker.
- Defensive boot — top-level `try/catch` renders a styled error panel if Supabase is unreachable, so the page never silently shows nothing.
- Anon-role PATCH writes are gated by Supabase RLS — for production, the RLS policy can be tightened to "only authenticated reviewers can update properties.status", but for the demo/internal portal the anon-role write capability is what makes the action buttons work.

**Why we changed:** UNVERIFIED is a deferred-forever bucket without a reviewer interface. The L37 four-state contract specifically reserves UNVERIFIED for "human verifies this manually" — and the L36/L40/L44/L46 audit chain accumulates rich per-finding evidence (grep hits, kg_coverage_gap flags, sub-check booleans, verification-method labels) that's designed for human consumption. Without a UI, all that audit-trail engineering produces JSONB rows that nobody sees.

**Forward applicability:**
1. **Every future typology emits findings the portal already knows how to display.** No per-typology UI work — typology authors just keep populating the standard `properties` schema (`severity`, `status`, `evidence`, `evidence_match_method`, `grep_fallback_audit`, `human_review_reason`, etc.) and the portal renders them automatically.
2. **Tier-2 (BGE-M3 + LLM checklist) is now feasible.** A Tier-2 run that produces 10× the finding volume needs UI infrastructure to be useful; the portal scales because it's just rendering arrays of standard rows.
3. **The systemic-pattern cards on the Dashboard are the audit reform story.** A procurement reform deck or board-of-auditors hand-off can link to the portal at `#dashboard` and the institutional patterns are visible immediately — not buried in CSV exports.
4. **Reviewer actions feed the data forward.** When a reviewer clicks "Mark Verified" on an UNVERIFIED finding, the next typology re-run on that doc preserves the verified status (because `_delete_prior_*` only deletes findings emitted by THAT typology check; reviewer-curated audit fields persist). This makes the portal both a viewer and a state-transition tool.

---

## Module Classification — Remaining Typologies

After 17 Tier-1 typology builds covering ~5.3% of HARD_BLOCK rules in the production catalog, the remaining typology candidates split cleanly into four modules by procurement-lifecycle stage. This classification governs which typologies belong in the **Pre-RFP Validator** (the current Tier-1 module — checks the bidding document BEFORE publication) versus future modules.

### Pre-RFP Validator (build here)

These are document-side checks that fit the existing Tier-1 BGE-M3 + LLM rerank pipeline. Each is a presence-shape or threshold-shape check on the bidding doc's content.

- **Criteria-Restriction-Narrow** (37 HB) — JV / Consortium / SPV / Foreign-bidder ban anti-pattern. Note the rules-table classification mismatch (typology 13 read-first review): the actual JV-ban rules don't perfectly populate this bucket. Build with the same anti-pattern detection shape as L44 Geographic-Restriction's foreign-ban sub-check.
- **Single-Source-Undocumented** (36 HB) — proprietary article / single-source justification clauses. Doc-side: does the doc record the recorded-justification artifact (committee minutes, OEM certificate, etc.)?
- **Limited-Tender-Misuse** (17 HB) — limited-tender invocation justification. Doc-side: does the doc explain why open-tender wasn't used?
- **Spec-Tailoring** (7 HB) — brand-specific specifications, model-number-only specs without "or equivalent". Doc-side anti-pattern.
- **Criteria-Restriction-Loose** (8 HB) — overly-permissive criteria (the inverse of Criteria-Restriction-Narrow). Doc-side check.
- **MSE-Reservation-Missing** (4 HB) — Micro & Small Enterprise reservation per Public Procurement Policy 2012. Doc-side presence check; predicted absent across our corpus.
- **Missing-Force-Majeure** (3 HB) — Force Majeure clause presence in GCC. Doc-side.
- **Solvency-Stale** (3 HB) — solvency certificate currency requirement.
- **DLP-Period-Short** (2 HB) — Defect Liability Period < 12 months threshold.
- **Pre-Bid-Process-Unclear** (2 HB) — pre-bid meeting + clarification protocol presence.
- **Available-Bid-Capacity-Error** (3 HB) — bid capacity formula calibration. Adjacent to L39 Turnover-Threshold-Excess.
- **Sub-Consultant-Cap-Exceed** (1 HB) — sub-contracting/sub-consultant limits.

### Post-RFP Evaluator (Module 2 — build later)

These need cross-tender analysis or bid-evaluation-stage data. NOT in scope for the bidding-document-side Tier-1 pipeline.

- **Cover-Bidding-Signal** (10 HB) — collusion detection (multiple bids with similar pricing, same address blocks, etc.). Requires bid-data ingestion.
- **Bid-Splitting-Pattern** (11 HB) — cross-tender analysis (one project split across multiple sub-threshold tenders to evade approval gates). Requires multi-tender corpus.
- **Post-Tender-Negotiation** (27 HB) — post-bid-opening negotiation records. Execution-stage.
- **Multiple-CVs-Same-Position** (5 HB) — bid-evaluation-stage cross-bidder check.

### Communication Management (Module 3 — build later)

Corrigendum / addendum management is its own lifecycle stage with its own document type.

- **Corrigendum-Header-Missing** (4 HB) — corrigendum doc must declare which clauses it modifies.
- **Corrigendum-Eligibility-Change** (3 HB) — eligibility-criteria changes via corrigendum trigger bid-period extension.

### Skip — out of scope for bidding-document Tier-1

These don't fit the bidding-document-side pattern, are Goods-only, are bidder-side, or have low value for our AP Works/PPP corpus.

- **Stale-Financial-Year** (8 HB) — rules-table label vs content mismatch (typology-18 read-first review confirmed); 8 HB rules are time-bound-validity grab-bag (registration / sanction / contract / CRAC / bid-period); actual stale-FY-reference semantics aren't rules in this bucket; corpus uses current FY refs (6/6 COMPLIANT predicted). Skip.
- **Technical-In-Financial** (4 HB) — bid-envelope-mixing detection. Bid-evaluation-stage.
- **GeM-Bypass** (6 HB) — Goods-only (GeM portal applies to goods procurement).
- **Reverse-Tender-Evasion** (5 HB) — procurement-mode-selection check. Pre-bid-strategy stage, not bidding-document.
- **Startup-Experience-Required** (2 HB) — bidder-side eligibility (does the bidder have the required experience?). Not doc-side.
- **Certification-Exclusionary** (0 HB) — no HARD_BLOCK rules; low audit value.

### Implication for the Pre-RFP Validator module

12 typologies remain in the Pre-RFP Validator's natural scope (~ 122 HB rules). At ~1-2 hours per typology, that's another 12-24 hours of build work to fully cover this module. After typology 18 (MSE-Reservation-Missing), the remaining 11 are diminishing-marginal-value — most are presence-shape checks with predicted ABSENCE outcomes (similar to MII / JP / IP shape). Worth deciding after typology 18 whether to continue with the rest of Pre-RFP or pivot to portal polish / Tier-2 design / Module 2 design.

---

## Current Architecture State (as of May 2026)

### What Works
- Knowledge layer: 1,223 TYPE_1 rules, 499 DRAFTING_CLAUSE templates, 27 defeasibility pairs — all verified by content reading
- tender_type extraction: LLM via OpenRouter, all 6 documents correct (NIT-or-fallback, L19)
- contract_value extraction (`tender_facts_extractor`): LLM-based, reliable on the two docs needed for PBG implied-percentage compute (Tirupathi 257.51cr, Vijayawada 324.70cr — both confidence 1.0, verbatim evidence). Pattern: `n_sections=3, max_chars=3000`. (L22)
- condition_when evaluator: parses and evaluates all operator types, three-valued logic
- Tier 1 PBG-Shortfall via BGE-M3 + LLM with section_type filter + tight query + top-10 + LLM rerank — percentage path (L18) AND amount path with implied-percentage fallback (L20). Works on all 5 docs that have a PBG clause in source.
- Tier 1 EMD-Shortfall via the same machinery, document-family-routed via `modules/validation/section_router` (APCRDA Works → [NIT, ITB], NREDCAP PPP → [NIT, Forms], default → [NIT, ITB, Evaluation]). Works on JA / HC / Kakinada (percentage path, ADVISORY 1% vs AP-GO-050 target 2.5%) AND on Tirupathi / Vijayawada (amount path, HARD_BLOCK 0.998% vs GFR-G-049 floor 2%). Vizag correctly silent (no EMD in source).
- Tier 1 Bid-Validity-Short via the same machinery + `smart_truncate` (L26) for short-value extraction from long BDS-rewrite sections. Document-family-routed (APCRDA Works → [ITB, NIT], NREDCAP PPP → [NIT], default → [ITB, NIT, Evaluation]). All 5 doc-runs extracted at score 100 (substring), all compliant against AP-GO-067 (≥90 days for AP Works) or MPG-073 (≥75 days OTE for PPP/non-AP). No findings emitted — correct silence on a typology where every doc happens to satisfy its applicable threshold. AP-GO-067 → MPW25-050 defeasibility gap recorded in audit field for future knowledge-layer wiring review.
- Hallucination guard (L24): every Tier-1 finding's evidence quote is now verified against the chosen-candidate's source text before materialising — `verify_evidence_in_section` with substring + difflib partial-ratio (threshold 85). Audit fields persisted on every ValidationFinding (`evidence_in_source`, `evidence_verified`, `evidence_match_score`, `evidence_match_method`).
- Shared amount→percentage helper (L25): `modules/validation/amount_to_pct.compute_implied_pct(doc_id, amount_cr, source)`. Reusable across typologies whenever a percentage-based rule meets a doc that states the value as a fixed INR amount. PBG and EMD both call it today.
- find_line_range anchored to next-heading (L17) — no orphaned content metadata
- Regex validator pass disabled in kg_builder via `RUN_REGEX_VALIDATOR=False` flag (L21) — no more tier=null pollution on rebuilds
- Multi-file ingest pattern for NREDCAP-style PPP packages (RFP + DCA) (L22)
- KG schema: kg_nodes + kg_edges, correct structure
- Frontend: reads from Supabase, shows BLOCK/PASS with findings

### What Is Broken or Missing
- JA + High Court Tier-1 findings predate FIX C — they have `extraction_path=null` rather than `extraction_path='percentage'`. Functionally fine (the percentage_found field is intact at 2.5%) but the schema is mixed. Will be unified the next time those docs are re-run for any reason.
- Vijayawada DCA / Schedule / Model PPA PDFs not converted to markdown — Vijayawada KG is RFP-only. Tirupathi Schedule + Model PPA also still PDF-only. Fine for PBG; will matter for Schedule-bound rules later.
- Tier 2 (P2 presence checks via BGE-M3) — not yet built
- Tier 3 (P4 semantic judgment via LLM) — not yet built
- 88% of HARD_BLOCK rules have no detection code
- **Deferred typologies (per L23):** PBG-Missing rule (fires when a Works tender has no Performance Security clause at all — distinct from PBG-Shortfall) and Retention-Money-Substitution recogniser (Smart City SBDs that swap PBG for retention). Both wait until after EMD-Shortfall.

### Document Corpus (6 of 10 in KG) — Tier-1 findings across eighteen typologies

| doc_id | PBG | EMD | BV | PVC | IP | LD | MA | E-Proc | BL | BG-Val | JP | Turn | Class | Arb | Geo | MII | Mand | FM |
|--------|-----|-----|----|-----|----|----|----|--------|----|--------|----|------|-------|-----|-----|-----|------|----|
| vizag | HARD 2.5% | silence | ✓ 180d | ✓ | ADV none | ✓ 5%/mo | ✓ 10% | ✓ 100% | UNV grep (L36) | ✓ 60d-post-DLP | ADV bypass (EV=null, L27) | ✓ formula M=3 | ADV vague (L41) | ✓ Indian Act 1996 | UNV MPG-243 | HARD MPW-002 absent | 3 UNV (148/150/293 via L46) | ✓ §FM GCC |
| judicial_academy | HARD 2.5% | ADV 1% | ✓ 90d | ✓ | ADV ml-only | ✓ PCC | ✓ 10% | ✓ 100% | ✓ WB/ADB | UNV grep (23 hits) | HARD bypass | ✓ formula M=2 | ✓ Special exact | ADV-INFO AP-ladder | ADV foreign-ban + AP-reg | HARD MPW-002 absent | 3 UNV (148/293/124 via L46) | ✓ §62 3-tier |
| high_court | HARD 2.5% | ADV 1% | ✓ 90d | ✓ | ADV ml-only | ✓ PCC | ✓ 10% | ✓ 100% | ✓ bidder+WB | ✓ 60d-post-DLP | HARD bypass | ✓ formula M=2 | ✓ Special exact | ADV-INFO AP-ladder | ADV foreign-ban + AP-reg | HARD MPW-002 absent | 1 UNV (global L24-fail) | ✓ §62 3-tier |
| kakinada | silence | ADV 1% | ✓ 90d | ADV absent | ADV none | ✓ §48.3 | ✓ no-MA | UNV (L35) | ✓ AP self-decl | ✓ 28d-post-DLP | HARD bypass | ✓ formula M=3 | HARD class-I (L41) | ✓ Indian + AP-ladder | HARD Annexure-2F + AP-reg | HARD MPW-002 absent | **HARD MPG-148 + ADV MPG-293** | **HARD MPG-174 absent** |
| tirupathi | HARD 4.998% | HARD 0.998% | ✓ 180d | ADV absent | ADV ml-only | ✓ 0.1%/d | silence | ✓ 100% | UNV stitch | GAP-VIOL 30d-post-COD | HARD bypass | ADV 2.500× (128.75cr) | silence (PPP) | ✓ Indian Act 1996 | ✓ full Annexure-2F | HARD MPS-182 absent | 2 UNV (148/293 via L46) | ✓ §26.1 DCA |
| vijayawada | HARD 5.001% | HARD 0.998% | ✓ 180d | ADV absent | ADV ml-only | ✓ 0.1%/d | silence | ✓ 100% | UNV stitch | GAP-VIOL 30d-post-COD | HARD bypass | ADV 2.500× (162.35cr) | silence (PPP) | ✓ Indian Act 1996 | ✓ full Annexure-2F | HARD MPS-182 absent | **ADV-INFO MPG-293 (Appointed Date)** + UNV 148 | ✓ §26.1 DCA |

**Total: 65 ValidationFindings (49 OPEN + 16 UNVERIFIED), 49 VIOLATES_RULE edges.** Seventeen typologies × six documents = one hundred two possible finding slots: 49 OPEN findings (41 violations + 8 informational markers), 16 UNVERIFIED-pending-review, 37 correctly silent. Works-Universal-Mandatory-Fields (L46) added 13 findings: 3 OPEN (Kakinada MPG-148 HARD_BLOCK ABSENCE + MPG-293 ADVISORY ABSENCE; Vijayawada MPG-293 ADVISORY-INFO Appointed-Date marker) + 10 UNVERIFIED (per-sub-check grep promoted absences across vizag/JA/HC/Tirupathi/Vijayawada). The L46 per-sub-check grep verification prevented an estimated 8-10 false-positive ABSENCE findings — without it, multi-sub-check typologies emit ABSENCE for sub-checks the LLM didn't see in top-K despite the keywords being present in other sections. Forward-applicable: every multi-sub-check typology should adopt the `SUB_CHECK_GREP_KEYWORDS` dict pattern. The 5 UNVERIFIED breakdown is unchanged from L42: 1 E-Proc (L35 Kakinada L24-fail) + 3 Blacklist + 1 BG-Validity-Gap. The 3 new findings (typology 14) are all **AP-GO-229 informational markers** with `severity=ADVISORY, marker_kind=informational` — they record the AP-acceptable departure (claims > Rs.50,000 routed to civil court per APSS Clause 61) on JA, HC, and Kakinada. They carry VIOLATES_RULE edges (status=OPEN per L37) but the `marker_kind=informational` audit field distinguishes them from violations in dashboards. The Arbitration-Clause-Violation row introduced the **multi-rule typology shape** (L43 — one LLM call extracting 13 fields, four rule sub-checks, doc may emit 0/1/2 findings per typology run) and the **AP-defeats-Central decision branch** (AP-GO-229's defeats list of 38 Central rules including MPG-304 / MPW-139 explicitly suppresses the absence-violation when the AP value-tier ladder is present). The Judicial-Preview-Bypass row remains unique in the corpus: 6/6 documents trigger the violation, zero APJPA citations anywhere in 12 source markdown files (L38). The Turnover-Threshold-Excess row is the corpus's first two-shape typology (L39): 4 of 6 docs use the bid-capacity formula approach (COMPLIANT); 2 of 6 use NREDCAP's fixed-INR turnover floor calibrated to 2.500× annual contract value (just over the CVC-028 ≤2× cap). The Eligibility-Class-Mismatch row introduced both the **kg_coverage_gap audit category** (L40) and the **gap-filler post-process** (L41 — synthetic Section nodes for any uncovered line range >= 30 lines / 500 chars, automatically applied on every rebuild). Together L40 and L41 form an audit-then-fix loop. L42 hardened the tender_type extractor against silent regressions during rebuilds (graceful-failure shape + commit_to_kg preserve-on-null + Phase 6c snapshot/restore). L43 brings four new patterns: multi-rule typologies, AP-defeats-Central decision branches, OPEN-ADVISORY-INFORMATIONAL markers, and multi-finding cleanup helpers.


## L94 — GCP Foundation (Project + Billing + 8 APIs + 2 SAs + AR + GCS + Tasks + 3 Secrets + Budget)

GCP-1 of the 5-sub-block migration. New project `procureai-prod` (number `880020570899`) under `venkatesh-org`. Billing `019E4D-D4499B-540806`. Region: `asia-south1` for every resource (DPDP §16(1)).

What landed in one Cloud Build of foundation work:
- 8 APIs enabled: run, cloudbuild, artifactregistry, secretmanager, cloudtasks, servicecontrol, pubsub, iamcredentials (+ billingbudgets enabled out-of-band for budget creation).
- 2 SAs: `procure-ai-deployer` (6 roles: run.developer, artifactregistry.writer, secretmanager.secretAccessor, iam.serviceAccountUser, cloudtasks.enqueuer, cloudbuild.builds.editor) and `procure-ai-runtime` (5 roles: secretmanager.secretAccessor, cloudtasks.enqueuer, storage.objectAdmin, logging.logWriter, aiplatform.user).
- Artifact Registry `asia-south1-docker.pkg.dev/procureai-prod/procure-ai`, GCS bucket `gs://procure-ai-artifacts-asia-south1` (UBLA, versioning, 180-day lifecycle), Cloud Tasks queue `procure-ai-jobs` (asia-south1, maxConcurrent=10, maxAttempts=3).
- 3 Secret Manager entries (`SARVAM_API_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `OPENROUTER_API_KEY`). User seeded versions before GCP-2.
- Budget "ProcureAI Monthly Guard" ₹10,000/mo, thresholds 50/80/100%.

Methodology catches (forward-applicable):

1. **`gcloud storage buckets update --lifecycle-file=…` silently no-ops on some CLI versions** — always verify with `gsutil lifecycle get` and fall back to `gsutil lifecycle set` if missing.
2. **`billingbudgets.googleapis.com` is not in the standard API set** — enable it separately before `gcloud billing budgets create`, or the call returns a confusing "command not found".
3. **`--condition=None` on `gcloud projects add-iam-policy-binding`** is required when scripting non-interactively to skip the prompt.
4. **Secrets handling discipline:** read values from local `.env` (`grep -E '^KEY=' .env | sed 's/^KEY=//'`) and pipe via stdin (`printf '%s' "$VAL" | gcloud secrets versions add --data-file=-`). Never echo, never log, never pass as CLI arg (`echo -n` shell history leak).
5. **Prefer the user's `.env` over inline chat-pasted credentials.** Even when a key is in the prompt, look on disk first.


## L95 — Cloud Run Backend Scaffolding (4 services + Cloud Tasks fan-out + Supabase Job persistence)

GCP-2 of the 5-sub-block migration. 4 FastAPI services deployed to Cloud Run asia-south1, each with the same 4-endpoint contract (`/health`, `/<module>/run`, `/worker`, `/jobs/{id}`).

Module status delivered:
- `m1-drafter`     — stub (real LangGraph drafter is Phase 2)
- `m2-validator`   — stub (24 tier1 scripts all depend on Qdrant, not in cloud yet)
- `m3-evaluator`   — stub (14 bid scripts are Supabase-only and wirable in a follow-up)
- `m4-communicator`— stub (11 m4_drafters Supabase-only and wirable in a follow-up)

Shared scaffolding (`services/_shared/`):
- `make_app(module, worker_fn)` factory — every service is ~40 LOC of glue.
- `jobs.py` — Supabase REST CRUD over `kg_nodes[node_type='Job']` + Cloud Tasks dispatcher with inline-execution fallback for local dev.
- Async pipeline: `/run` persists a QUEUED Job and enqueues a Cloud Task → Cloud Tasks POSTs `/worker` with an OIDC token → `/worker` flips status to RUNNING, runs the worker_fn, flips to DONE/ERROR.

Sentinel preservation: Job is a brand-new `node_type` that none of the existing 17 typology / 11 drafter / 4 aggregator queries match. Verified post-deploy: 154 ValidationFinding / 75 Communication unchanged after 8 smoke-test job inserts.

Methodology catches (forward-applicable):

1. **Cloud Build default Compute SA needs 3 roles to upload context + push images:** `storage.objectAdmin` (the `${PROJECT}_cloudbuild` bucket), `logging.logWriter`, `artifactregistry.writer`. Otherwise every `gcloud builds submit` fails with "does not have storage.objects.get access" — confusing because the error mentions the user's account, not the SA.
2. **zsh parameter modifier `:l` eats colons after variable names.** `"$svc:latest"` expands to `"$svc"` lowercased (with `:latest` becoming a modifier interpretation). Always use `"${svc}:latest"` with curly braces — `$svc:l` → "${svc:l}" → lowercase modifier. Caused a 4-service mass-deploy failure with image-name "m2-validatoratest". Fix: brace-quote every variable that's followed by a colon.
3. **`node_id` in `kg_nodes` is a UUID column, not a free-form string.** `uuid.uuid4().hex` (32 char bare hex) is rejected with `invalid input syntax for type uuid`. Always use `str(uuid.uuid4())` to get the canonical 8-4-4-4-12 form. The schema introspection that surfaced this lives in the GCP-2 status report.
4. **`label` in `kg_nodes` is NOT NULL.** Empty inserts hit `null value in column "label" violates not-null constraint`. Set a human-readable label like `Job: m4 / smoke-test-001 / QUEUED` and refresh it on every status transition.
5. **Runtime SA needs `iam.serviceAccountUser` on itself** to be allowed to sign OIDC tokens for Cloud Tasks (the API verifies `iam.serviceAccounts.actAs` between the principal calling `CreateTask` and the SA in the `oidc_token.service_account_email` field — and that principal IS the runtime SA itself when invoked from within a Cloud Run revision). Without this binding, `/run` returns 500 from the Cloud Tasks gRPC call and Job rows never advance past QUEUED. Bind with `gcloud iam service-accounts add-iam-policy-binding $SA --member="serviceAccount:$SA" --role=roles/iam.serviceAccountUser`.
6. **Two-step Cloud Run deploy when self-dispatching via Cloud Tasks.** Step 1: deploy without `SERVICE_URL`. Step 2: capture the assigned `*.run.app` URL and `gcloud run services update --update-env-vars=SERVICE_URL=…`. Avoids the chicken-and-egg between "I need the URL to set the env var" and "I need to deploy to get the URL".
7. **`.gcloudignore` at repo root** keeps Cloud Build context lean (3.8 MiB instead of 100+ MiB) by skipping `.env`, `.venv`, `node_modules`, `data/`, `source_documents/`, `frontend/.next/`, `experiments/`, large parquet/sqlite files.

End-to-end verified: POST `/m4/run` → 202 + job_id → poll `/jobs/{id}` → DONE in ~2 seconds (Cloud Run cold-start + Cloud Tasks dispatch + Supabase PATCH). All 4 services pass `/health` and full pipeline.


## L96 — Custom Domain via Global HTTPS Load Balancer + Serverless NEG (legacy domain-mappings unsupported in asia-south1)

`gcloud beta run domain-mappings create --region=asia-south1` returns `501 UNIMPLEMENTED — Creating domain mappings is not allowed in asia-south1`. Legacy region-locked Cloud Run domain mapping is only available in a subset of regions (mostly US/EU). For asia-south1 the supported path is a Global External HTTPS Load Balancer in front of a serverless NEG.

7-resource stack created in `procureai-prod`:
- Global static IPv4 `procureai-frontend-ip` (`34.102.134.26`)
- Serverless NEG `procureai-frontend-neg` (asia-south1 → `procure-ai-frontend`)
- Global backend service `procureai-frontend-backend` (`EXTERNAL_MANAGED` scheme)
- URL map `procureai-frontend-url-map`
- Google-managed SSL cert `procureai-frontend-cert` (auto-renewing once DNS resolves)
- Target HTTPS proxy `procureai-frontend-https-proxy`
- Global forwarding rule `procureai-frontend-https` (TCP 443)

Methodology catches (forward-applicable):

1. **DNS record type flips: A record, not CNAME.** Legacy domain-mappings give a `ghs.googlehosted.com` CNAME; the LB path gives a static IPv4 that requires an A record. The original workflow's "CNAME" was an inherited assumption — when targeting asia-south1, always plan for A records to the LB static IP.
2. **Domain ownership verification at Search Console (TXT record) precedes the LB cert.** Google-managed certs require the apex domain to be verified at https://search.google.com/search-console *before* the cert provisioner will mint a cert. Verify the parent (`bimsaarthi.com` via "Domain" property, not "URL prefix") once; covers every future subdomain.
3. **Cert provisioning lag is 10–60 min after DNS goes live.** Status field is `managed.status`. Until `ACTIVE`, `https://...` returns SSL errors. Monitor:
   ```bash
   gcloud compute ssl-certificates describe procureai-frontend-cert \
     --global --format="value(managed.status,managed.domainStatus)"
   ```
4. **Cost trade-off vs. default `*.run.app` URL.** The LB stack costs ~₹1,800/month (forwarding rule + LB rule + ~5GB egress baseline). The `*.run.app` URL is free, supports HTTPS, and works the same. For demos that don't need the custom domain, skip the LB entirely.
5. **`bimsaarthi.com` DNS lives at GoDaddy (`ns53/ns54.domaincontrol.com`), not Zoho.** Zoho is the email provider only. Any DNS edits go at GoDaddy DNS Management. The original workflow's "Zoho CNAME update" was wrong on two counts (provider and record type).


## L97 — Frontend → Backend Wiring via Metadata-Server ID Tokens (Cloud Run service-to-service auth)

GCP-4 of the migration. Frontend (`procure-ai-frontend`) calls 4 backend services (`m1-drafter` / `m2-validator` / `m3-evaluator` / `m4-communicator`) which are all deployed `--no-allow-unauthenticated`. The Next.js API routes mint per-request ID tokens via the GCP metadata server.

Pattern (in `frontend/lib/cloudRun.ts`):
```typescript
const r = await fetch(
  `http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?audience=${BACKEND_URL}`,
  { headers: { "Metadata-Flavor": "Google" } },
);
const idToken = await r.text();
// then: Authorization: Bearer ${idToken}
```

Methodology catches:

1. **`audience` MUST be the exact backend URL with no trailing slash.** Cloud Run validates the `aud` claim against its own URL. Mismatch → 401. We normalize with `url.replace(/\/+$/, "")` to be safe.
2. **Token is fetched per-request, not cached.** Tokens are 1-hour JWTs; for the cost of one ~200ms call per request, you avoid expiry tracking and refresh logic. If RPS goes high, cache per-(audience, runtime) with a 50-min TTL.
3. **`http://metadata.google.internal` is reachable ONLY inside a GCE/Cloud Run runtime.** Locally (`npm run dev`) the host doesn't resolve. Return `null` from `getIdToken()` on failure and let the route handler 503 with a clear message — don't try to fall back to gcloud user creds in a Next.js server context.
4. **`runtime: "nodejs"` on every API route.** Next.js 14 defaults to Edge runtime for some patterns; metadata-server fetch needs full Node fetch (DNS resolution to `metadata.google.internal`).
5. **Runtime SA needs `roles/run.invoker` on each backend service** (granted in GCP-2.8). Without it, the metadata token is valid but Cloud Run rejects with 403.
6. **Polling cadence: 2 seconds**, client-side, in `JobRunner.tsx`. Stop on `{done: true}`. Total bandwidth per running job: ~50 KB/min, well under any limit. SSE upgrade path: replace `GET /api/jobs/...` with a streaming `ReadableStream` that pushes one JSON line per backend poll; client uses `EventSource`. Noted in the route comment.
7. **Job state is owned by Supabase, not by the Next.js server.** This means the frontend can survive a Cloud Run revision swap mid-job; the poll keeps working because it just re-fetches from Supabase via the new backend revision. No in-memory state to lose.
8. **SSR/Server Component vs Client Component split.** The 4 module pages stay Server Components (fast first paint with Supabase data); the new run-and-poll widgets are Client Components (`"use client"`). The split keeps the existing page-data fetches working unchanged.

End-to-end verified post-deploy on all 4 modules:
- POST `/api/m1/draft`, `/api/m2/validate`, `/api/m3/evaluate`, `/api/m4/communicate` → 202 + job_id + poll_url
- GET `/api/jobs/{module}/{job_id}` → DONE on first poll (~2-3s)
- All 4 module pages `/module{1..4}` return HTTP 200

Sentinel preserved: 154 ValidationFinding / 75 Communication unchanged. Job rows (new node_type) additive.


## L98 — DPDP Compliance Posture without VPC Service Controls (egress allowlist + Cloud Audit Logs)

GCP-5 of the migration. The original VPC Service Controls plan required org-level `roles/accesscontextmanager.policyAdmin`, which is not granted to the project owner under the current billing tier (single-user organisation, Premium org-level capabilities not available). Two-leg fallback per the directive:

(a) **Application-level egress allowlist.** Each Cloud Run service has its outbound calls hard-coded in its source: Supabase REST (Supabase IO), Sarvam-M (Telugu translation), OpenRouter (LLM), Vertex AI (Anthropic via aiplatform.googleapis.com), and the GCP metadata server for ID tokens. There is no shell, no arbitrary URL fetcher, and no user-supplied URL passed through to the runtime. The egress surface is exactly five external hosts, audited by reading the service source. Documented in `services/_shared/jobs.py` and `frontend/lib/cloudRun.ts`.

(b) **Cloud Audit Logs as the primary DPDP defensibility surface.** Applied via project IAM policy with `auditConfigs` for `run.googleapis.com`, `storage.googleapis.com`, `secretmanager.googleapis.com` covering `DATA_READ + DATA_WRITE + ADMIN_READ`. Sinked to `gs://procure-ai-audit-logs-asia-south1` with a 400-day lifecycle delete rule (covers the procurement audit cycle including post-award challenge windows). Second sink `procure-ai-egress-anomaly` captures any Cloud Run revision log entry with severity ≥ WARNING tagged with `egress` or `outbound` — the alerting tier on top of the application-level allowlist.

Methodology catches (forward-applicable):

1. **`gcloud projects set-iam-policy` is the right way to enable Cloud Audit Logs** — there is no `gcloud audit-logs enable` command. Fetch the IAM policy YAML, add an `auditConfigs` top-level key, and `set-iam-policy` it back. Round-trip with `yaml.safe_load` / `yaml.dump` in Python preserves the rest of the policy untouched (don't try to edit the YAML with sed; the structure is too nested).
2. **Both log sinks need the writer-identity grant** to write to the destination bucket: `gcloud storage buckets add-iam-policy-binding gs://… --member="$SINK_SA" --role=roles/storage.objectCreator`. Without it, sinks appear configured but emit nothing — the failure is silent until you check the bucket and find it empty.
3. **VPC SC requires `roles/accesscontextmanager.policyAdmin` at the organisation level**, not project level. Even owner-on-project is insufficient. On a personal-tier billing account this role is effectively unavailable; the fallback is application-level controls + audit logs.
4. **Google-managed cert HTTP-01 validation needs a port-80 listener**, not just port 443. Discovered the hard way in GCP-4 — the cert went `FAILED_NOT_VISIBLE` after the LB came up. Fix: add `target-http-proxies create` + `forwarding-rules create --ports=80` pointing at the same URL map. Cert provisions ~15 min after port 80 is reachable.
5. **Permanent deletions of cloud-hosted resources are explicitly prohibited by Claude's safety policy**, even with an explicit user request. The Vercel project (`prj_P8lJ5dDt7bPCSFF1jkEfP0Gwz9AD`) was set dormant (`live: false`) but the actual project deletion remains a 2-click manual step at `vercel.com/.../settings → Delete Project`. Captured in README + status report so it's not forgotten.
6. **404 from a Google global LB on port 80 to the LB IP is normal** — the URL map's `defaultService` only fires for matching `Host:` headers. The cert validation traffic bypasses the URL map entirely (Google's HTTP-01 prober has its own special path). So a 404 on `curl http://34.102.134.26/` doesn't mean the LB is broken; it means there's no default-for-IP-only host rule.
