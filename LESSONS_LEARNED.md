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

### Document Corpus (6 of 10 in KG) — Tier-1 findings across five typologies

| doc_id | PBG | EMD | Bid-Validity | PVC | Integrity-Pact |
|--------|-----|-----|-------------|-----|---------------|
| vizag | HARD_BLOCK 2.5% | silence | compliant 180d | compliant | ADVISORY absent (none) |
| judicial_academy | HARD_BLOCK 2.5% | ADVISORY 1% | compliant 90d | compliant | ADVISORY absent (multilateral framework only) |
| high_court | HARD_BLOCK 2.5% | ADVISORY 1% | compliant 90d | compliant | ADVISORY absent (multilateral framework only) |
| kakinada | silence | ADVISORY 1% | compliant 90d | ADVISORY absent | ADVISORY absent (none) |
| tirupathi | HARD_BLOCK 4.998% | HARD_BLOCK 0.998% | compliant 180d | silence (PPP) | ADVISORY absent (multilateral framework only) |
| vijayawada | HARD_BLOCK 5.001% | HARD_BLOCK 0.998% | compliant 180d | silence (PPP) | ADVISORY absent (none) |

**Total: 17 ValidationFindings, 17 VIOLATES_RULE edges.** Five typologies × six documents = thirty possible finding slots: 17 are filled with violations, 13 are correctly silent (compliant docs, PPP rule-layer skips on PVC, EMD genuinely-absent-from-source on Vizag). All presence findings have `evidence_match_score >= 98`; absence findings carry `evidence_match_method='absence_finding_no_evidence'` per L29; multilateral-framework-only IP findings carry verified ADB/WB framework evidence + `pact_type='multilateral_framework_only'` + an explanatory `note` per L30. Every Integrity-Pact finding is ADVISORY because the IP_Threshold subterm is org-defined per CVC-116 — UNKNOWN→ADVISORY downgrade per L27.
