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

### Document Corpus (6 of 10 in KG) — Tier-1 findings across seventeen typologies

| doc_id | PBG | EMD | BV | PVC | IP | LD | MA | E-Proc | BL | BG-Val | JP | Turn | Class | Arb | Geo | MII | Mand |
|--------|-----|-----|----|-----|----|----|----|--------|----|--------|----|------|-------|-----|-----|-----|------|
| vizag | HARD 2.5% | silence | ✓ 180d | ✓ | ADV none | ✓ 5%/mo | ✓ 10% | ✓ 100% | UNV grep (L36) | ✓ 60d-post-DLP | ADV bypass (EV=null, L27) | ✓ formula M=3 | ADV vague (L41) | ✓ Indian Act 1996 | UNV MPG-243 | HARD MPW-002 absent | 3 UNV (148/150/293 via L46) |
| judicial_academy | HARD 2.5% | ADV 1% | ✓ 90d | ✓ | ADV ml-only | ✓ PCC | ✓ 10% | ✓ 100% | ✓ WB/ADB | UNV grep (23 hits) | HARD bypass | ✓ formula M=2 | ✓ Special exact | ADV-INFO AP-ladder | ADV foreign-ban + AP-reg | HARD MPW-002 absent | 3 UNV (148/293/124 via L46) |
| high_court | HARD 2.5% | ADV 1% | ✓ 90d | ✓ | ADV ml-only | ✓ PCC | ✓ 10% | ✓ 100% | ✓ bidder+WB | ✓ 60d-post-DLP | HARD bypass | ✓ formula M=2 | ✓ Special exact | ADV-INFO AP-ladder | ADV foreign-ban + AP-reg | HARD MPW-002 absent | 1 UNV (global L24-fail) |
| kakinada | silence | ADV 1% | ✓ 90d | ADV absent | ADV none | ✓ §48.3 | ✓ no-MA | UNV (L35) | ✓ AP self-decl | ✓ 28d-post-DLP | HARD bypass | ✓ formula M=3 | HARD class-I (L41) | ✓ Indian + AP-ladder | HARD Annexure-2F + AP-reg | HARD MPW-002 absent | **HARD MPG-148 + ADV MPG-293** |
| tirupathi | HARD 4.998% | HARD 0.998% | ✓ 180d | ADV absent | ADV ml-only | ✓ 0.1%/d | silence | ✓ 100% | UNV stitch | GAP-VIOL 30d-post-COD | HARD bypass | ADV 2.500× (128.75cr) | silence (PPP) | ✓ Indian Act 1996 | ✓ full Annexure-2F | HARD MPS-182 absent | 2 UNV (148/293 via L46) |
| vijayawada | HARD 5.001% | HARD 0.998% | ✓ 180d | ADV absent | ADV ml-only | ✓ 0.1%/d | silence | ✓ 100% | UNV stitch | GAP-VIOL 30d-post-COD | HARD bypass | ADV 2.500× (162.35cr) | silence (PPP) | ✓ Indian Act 1996 | ✓ full Annexure-2F | HARD MPS-182 absent | **ADV-INFO MPG-293 (Appointed Date)** + UNV 148 |

**Total: 65 ValidationFindings (49 OPEN + 16 UNVERIFIED), 49 VIOLATES_RULE edges.** Seventeen typologies × six documents = one hundred two possible finding slots: 49 OPEN findings (41 violations + 8 informational markers), 16 UNVERIFIED-pending-review, 37 correctly silent. Works-Universal-Mandatory-Fields (L46) added 13 findings: 3 OPEN (Kakinada MPG-148 HARD_BLOCK ABSENCE + MPG-293 ADVISORY ABSENCE; Vijayawada MPG-293 ADVISORY-INFO Appointed-Date marker) + 10 UNVERIFIED (per-sub-check grep promoted absences across vizag/JA/HC/Tirupathi/Vijayawada). The L46 per-sub-check grep verification prevented an estimated 8-10 false-positive ABSENCE findings — without it, multi-sub-check typologies emit ABSENCE for sub-checks the LLM didn't see in top-K despite the keywords being present in other sections. Forward-applicable: every multi-sub-check typology should adopt the `SUB_CHECK_GREP_KEYWORDS` dict pattern. The 5 UNVERIFIED breakdown is unchanged from L42: 1 E-Proc (L35 Kakinada L24-fail) + 3 Blacklist + 1 BG-Validity-Gap. The 3 new findings (typology 14) are all **AP-GO-229 informational markers** with `severity=ADVISORY, marker_kind=informational` — they record the AP-acceptable departure (claims > Rs.50,000 routed to civil court per APSS Clause 61) on JA, HC, and Kakinada. They carry VIOLATES_RULE edges (status=OPEN per L37) but the `marker_kind=informational` audit field distinguishes them from violations in dashboards. The Arbitration-Clause-Violation row introduced the **multi-rule typology shape** (L43 — one LLM call extracting 13 fields, four rule sub-checks, doc may emit 0/1/2 findings per typology run) and the **AP-defeats-Central decision branch** (AP-GO-229's defeats list of 38 Central rules including MPG-304 / MPW-139 explicitly suppresses the absence-violation when the AP value-tier ladder is present). The Judicial-Preview-Bypass row remains unique in the corpus: 6/6 documents trigger the violation, zero APJPA citations anywhere in 12 source markdown files (L38). The Turnover-Threshold-Excess row is the corpus's first two-shape typology (L39): 4 of 6 docs use the bid-capacity formula approach (COMPLIANT); 2 of 6 use NREDCAP's fixed-INR turnover floor calibrated to 2.500× annual contract value (just over the CVC-028 ≤2× cap). The Eligibility-Class-Mismatch row introduced both the **kg_coverage_gap audit category** (L40) and the **gap-filler post-process** (L41 — synthetic Section nodes for any uncovered line range >= 30 lines / 500 chars, automatically applied on every rebuild). Together L40 and L41 form an audit-then-fix loop. L42 hardened the tender_type extractor against silent regressions during rebuilds (graceful-failure shape + commit_to_kg preserve-on-null + Phase 6c snapshot/restore). L43 brings four new patterns: multi-rule typologies, AP-defeats-Central decision branches, OPEN-ADVISORY-INFORMATIONAL markers, and multi-finding cleanup helpers.
