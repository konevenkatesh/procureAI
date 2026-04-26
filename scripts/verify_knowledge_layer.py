"""
Health check + progress dashboard for the knowledge layer.

Run any time to see how close you are to the production-ready targets:
  - 400+ approved rules
  - 750+ clause templates
  - 200+ production-ready SHACL shapes
  - 200+ vector concepts in Qdrant
"""
from __future__ import annotations

import sys

from rich.console import Console
from rich.table import Table

from knowledge_layer.database import get_session
from knowledge_layer.models import (
    ClauseTemplateModel,
    RiskTypologyModel,
    RuleModel,
    SHACLShapeModel,
    TestCaseModel,
    VectorConceptModel,
)
from knowledge_layer.vector_store import collection_count


console = Console()


def main() -> int:
    with get_session() as session:
        total_rules = session.query(RuleModel).count()
        approved = session.query(RuleModel).filter(
            RuleModel.human_status.in_(["approved", "modified"])
        ).count()
        pending = session.query(RuleModel).filter_by(human_status="pending").count()
        rejected = session.query(RuleModel).filter_by(human_status="rejected").count()

        p_counts = {
            p: session.query(RuleModel).filter(
                RuleModel.pattern_type == p,
                RuleModel.human_status.in_(["approved", "modified"]),
            ).count() for p in ["P1", "P2", "P3", "P4"]
        }

        total_clauses = session.query(ClauseTemplateModel).count()
        telugu_clauses = session.query(ClauseTemplateModel).filter(
            ClauseTemplateModel.text_telugu.isnot(None)
        ).count()
        verified_clauses = session.query(ClauseTemplateModel).filter_by(human_verified=True).count()

        shacl_total = session.query(SHACLShapeModel).count()
        shacl_ready = session.query(SHACLShapeModel).filter_by(production_ready=True).count()

        test_count = session.query(TestCaseModel).count()
        typology_count = session.query(RiskTypologyModel).count()
        concept_db_count = session.query(VectorConceptModel).count()

    qdrant_count = collection_count()

    # ── Render ───────────────────────────────────────────────────────────────
    rules_table = Table(title="Rules", show_header=True, header_style="bold magenta")
    rules_table.add_column("Metric")
    rules_table.add_column("Count", justify="right")
    rules_table.add_column("Target", justify="right")
    rules_table.add_column("Status", justify="center")
    rules_table.add_row("Total extracted", str(total_rules), "—", "—")
    rules_table.add_row("Approved",        str(approved), "400+",
                       "[green]✓[/green]" if approved >= 400 else "[red]✗[/red]")
    rules_table.add_row("Pending review",  str(pending), "—", "—")
    rules_table.add_row("Rejected",        str(rejected), "—", "—")
    rules_table.add_row("P1 (SHACL)",      str(p_counts["P1"]), "200+",
                       "[green]✓[/green]" if p_counts["P1"] >= 200 else "[yellow]…[/yellow]")
    rules_table.add_row("P2 (Vector)",     str(p_counts["P2"]), "100+",
                       "[green]✓[/green]" if p_counts["P2"] >= 100 else "[yellow]…[/yellow]")
    rules_table.add_row("P3 (Defeasible)", str(p_counts["P3"]), "—", "—")
    rules_table.add_row("P4 (Semantic)",   str(p_counts["P4"]), "—", "—")
    console.print(rules_table)

    clause_table = Table(title="Clauses", show_header=True, header_style="bold magenta")
    clause_table.add_column("Metric")
    clause_table.add_column("Count", justify="right")
    clause_table.add_column("Target", justify="right")
    clause_table.add_column("Status", justify="center")
    clause_table.add_row("Total templates", str(total_clauses), "750+",
                       "[green]✓[/green]" if total_clauses >= 750 else "[red]✗[/red]")
    clause_table.add_row("With Telugu",     str(telugu_clauses), "750+",
                       "[green]✓[/green]" if telugu_clauses >= 750 else "[yellow]…[/yellow]")
    clause_table.add_row("Human verified",  str(verified_clauses), "—", "—")
    console.print(clause_table)

    shape_table = Table(title="SHACL Shapes & Test Cases", show_header=True, header_style="bold magenta")
    shape_table.add_column("Metric")
    shape_table.add_column("Count", justify="right")
    shape_table.add_column("Target", justify="right")
    shape_table.add_column("Status", justify="center")
    shape_table.add_row("Shapes generated",  str(shacl_total), "—", "—")
    shape_table.add_row("Production ready",  str(shacl_ready), "200+",
                       "[green]✓[/green]" if shacl_ready >= 200 else "[red]✗[/red]")
    shape_table.add_row("Test cases",        str(test_count), "2000+",
                       "[green]✓[/green]" if test_count >= 2000 else "[yellow]…[/yellow]")
    console.print(shape_table)

    vec_table = Table(title="Vectors & Typology", show_header=True, header_style="bold magenta")
    vec_table.add_column("Metric")
    vec_table.add_column("Count", justify="right")
    vec_table.add_column("Target", justify="right")
    vec_table.add_column("Status", justify="center")
    vec_table.add_row("Concepts in Postgres", str(concept_db_count), "—", "—")
    vec_table.add_row("Vectors in Qdrant",    str(qdrant_count), "200+",
                       "[green]✓[/green]" if qdrant_count >= 200 else "[red]✗[/red]")
    vec_table.add_row("Risk typologies",      str(typology_count), "45",
                       "[green]✓[/green]" if typology_count >= 45 else "[red]✗[/red]")
    console.print(vec_table)

    ready = (
        approved >= 400 and total_clauses >= 750
        and shacl_ready >= 200 and qdrant_count >= 200
        and typology_count >= 45
    )
    console.print()
    if ready:
        console.print("[bold green]✓ READY for application layer[/bold green]")
    else:
        console.print("[bold red]✗ NOT YET READY — see targets above[/bold red]")
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(main())
