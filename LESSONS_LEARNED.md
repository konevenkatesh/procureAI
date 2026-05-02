# AP Procurement AI — Lessons Learned
**Project:** BIMSaarthi Technologies / RTGS Hackathon  
**Period:** Sessions from April–May 2026  
**Maintained by:** Claude (conversation) + Claude Code (implementation)  
**Rule:** Every strategy change, no matter how small, is recorded here with the reason.

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

## Current Architecture State (as of May 2026)

### What Works
- Knowledge layer: 1,223 TYPE_1 rules, 499 DRAFTING_CLAUSE templates, 27 defeasibility pairs — all verified by content reading
- tender_type extraction: LLM via OpenRouter, all 6 documents correct (NIT-or-fallback, L19)
- condition_when evaluator: parses and evaluates all operator types, three-valued logic
- Tier 1 BGE-M3+LLM with section_type filter + tight query + top-10 + LLM rerank: working on Vizag, JA, High Court (all percentage-based PBG)
- find_line_range anchored to next-heading (L17) — no orphaned content metadata
- KG schema: kg_nodes + kg_edges, correct structure
- Frontend: reads from Supabase, shows BLOCK/PASS with findings

### What Is Broken or Missing
- Tier 1 retrieval still misses on PPP documents (Tirupathi, Vijayawada) — amount-to-percentage conversion needed (FIX C / L15)
- Kakinada PBG missing from markdown — needs investigation
- Regex validator still runs inside kg_builder on every build (L14) — needs disabling
- Tier 2 (P2 presence checks via BGE-M3) — not yet built
- Tier 3 (P4 semantic judgment via LLM) — not yet built
- 88% of HARD_BLOCK rules have no detection code

### Document Corpus (6 of 10 in KG)
| doc_id | Type | Department | Tier-1 PBG Finding |
|--------|------|-----------|---------|
| vizag_ugss_exp_001 | Works | GVMC Sewerage | 2.5% (cos 0.6844) |
| judicial_academy_exp_001 | Works | APCRDA | 2.5% (cos 0.665) |
| tirupathi_wte_exp_001 | PPP | NREDCAP | none — fixed-amount, FIX C pending |
| high_court_exp_001 | Works | AP High Court | 2.5% (cos 0.6567) |
| kakinada_pkg11_exp_001 | Works | Kakinada Smart City | none — markdown gap |
| vijayawada_wte_exp_001 | PPP | NREDCAP | none — fixed-amount, FIX C pending |
