"""
Test case generation pipeline (5 cases per approved rule) — batch + loader.
"""
from __future__ import annotations

import json
from pathlib import Path

from loguru import logger
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from builder.config import settings
from knowledge_layer.database import get_session
from knowledge_layer.models import TestCaseModel
from knowledge_layer.rule_store import get_approved_rules
from knowledge_layer.schemas import TestCase


TEST_CASE_SYSTEM_PROMPT = """\
For each rule below, generate exactly 5 test cases that span the following coverage:

  1. CLEAR_COMPLIANCE     → tender text obviously satisfies the rule. expected: PASS.
  2. CLEAR_VIOLATION      → tender text obviously violates the rule. expected: FAIL.
  3. BOUNDARY             → value sits exactly at the threshold (e.g. EMD = exactly 2.00%).
  4. EXCEPTION_APPLIES    → seeming violation but a defeasible exception applies → PASS.
  5. MISSING_FIELD        → required field absent → FAIL.

Each test case is realistic tender-document text (50–150 words). Use real-sounding
amounts, departments, and AP-flavoured phrasing.

Output FORMAT: a single JSON object keyed by rule_id, value is a list of 5
test case objects:

{
  "GFR-EMD-001": [
    {
      "test_id":           "TC-GFR-EMD-001-001",
      "document_excerpt":  "...50-150 word realistic tender excerpt...",
      "expected_result":   "PASS",
      "expected_severity": null,                       // null when PASS
      "reasoning":         "Why this excerpt PASSes the rule"
    },
    {
      "test_id":           "TC-GFR-EMD-001-002",
      "document_excerpt":  "...",
      "expected_result":   "FAIL",
      "expected_severity": "HARD_BLOCK",
      "reasoning":         "..."
    },
    ... (5 total per rule)
  ],
  "AP-GOMS79-RT-001": [ ...5 test cases... ]
}

Output JSON ONLY. No markdown, no commentary.
"""


def prepare_testcase_batches(rules_per_batch: int = 20) -> list[Path]:
    """Build test-case batches for all approved rules."""
    settings.testcase_batches_dir.mkdir(parents=True, exist_ok=True)

    rules = get_approved_rules()
    if not rules:
        logger.warning("No approved rules found.")
        return []

    created: list[Path] = []
    for batch_idx, start in enumerate(range(0, len(rules), rules_per_batch), 1):
        chunk = rules[start : start + rules_per_batch]
        batch_id = f"testcase_batch_{batch_idx:04d}"
        payload = {
            "batch_id": batch_id,
            "system_prompt": TEST_CASE_SYSTEM_PROMPT,
            "instructions_for_operator": (
                f"Generate 5 test cases per rule. Write the result to "
                f"data/testcase_results/{batch_id}.json."
            ),
            "rule_count": len(chunk),
            "rules": chunk,
        }
        out_path = settings.testcase_batches_dir / f"{batch_id}.json"
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        created.append(out_path)

    logger.info(f"Prepared {len(created)} test-case batches.")
    return created


def load_testcase_results(batch_glob: str = "*.json") -> dict:
    """Read test-case results and save to Postgres."""
    settings.testcase_results_dir.mkdir(parents=True, exist_ok=True)

    summary = {"files_read": 0, "testcases_loaded": 0, "validation_errors": 0, "errors": []}
    test_cases: list[TestCase] = []

    for result_file in sorted(settings.testcase_results_dir.glob(batch_glob)):
        summary["files_read"] += 1
        try:
            data = json.loads(result_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            summary["errors"].append((result_file.name, f"invalid JSON: {e}"))
            continue

        for rule_id, cases in data.items():
            if not isinstance(cases, list):
                continue
            for raw in cases:
                raw.setdefault("rule_id", rule_id)
                try:
                    test_cases.append(TestCase(**raw))
                except ValidationError as e:
                    summary["validation_errors"] += 1
                    summary["errors"].append((result_file.name, f"validation: {e.errors()[0]['msg']}"))

    if test_cases:
        summary["testcases_loaded"] = _save_test_cases(test_cases)
    return summary


def _save_test_cases(cases: list[TestCase]) -> int:
    saved = 0
    with get_session() as session:
        for c in cases:
            existing = session.query(TestCaseModel).filter_by(test_id=c.test_id).first()
            if existing:
                continue
            try:
                session.add(TestCaseModel(
                    test_id=c.test_id,
                    rule_id=c.rule_id,
                    document_excerpt=c.document_excerpt,
                    expected_result=c.expected_result,
                    expected_severity=c.expected_severity.value if c.expected_severity else None,
                    reasoning=c.reasoning,
                ))
                saved += 1
            except IntegrityError:
                session.rollback()
                continue
    return saved
