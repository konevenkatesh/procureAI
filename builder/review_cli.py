"""
Human review CLI for candidate rules.

Usage:
  python builder/review_cli.py review --batch 30
  python builder/review_cli.py stats
"""
from __future__ import annotations

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from knowledge_layer.database import get_session
from knowledge_layer.models import RuleModel
from knowledge_layer.rule_store import get_pending_rules, update_rule_status

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()


@app.command()
def review(batch: int = 20):
    """Interactively review pending candidate rules.

    Keys: a=approve  r=reject  m=modify  s=skip  q=quit
    """
    rules = get_pending_rules(limit=batch)
    if not rules:
        console.print("[green]No pending rules to review.[/green]")
        return

    console.print(f"\n[bold]Reviewing {len(rules)} pending rules[/bold]\n")
    counts = {"approved": 0, "rejected": 0, "modified": 0, "skipped": 0}

    for i, rule in enumerate(rules, 1):
        sev_color = {"HARD_BLOCK": "red", "WARNING": "yellow", "ADVISORY": "cyan"}.get(rule["severity"], "white")
        confidence = rule.get("extraction_confidence")
        conf_str = f"{confidence:.2f}" if confidence is not None else "—"

        body = (
            f"[bold]{rule['rule_id']}[/bold]  ({i}/{len(rules)})\n\n"
            f"[cyan]Source:[/cyan] {rule['source_doc']} — {rule['source_clause']}\n"
            f"[cyan]Section:[/cyan] {rule.get('extracted_from', '—')}\n\n"
            f"[yellow]Rule:[/yellow] {rule['natural_language']}\n\n"
            f"[{sev_color}]Severity: {rule['severity']}[/{sev_color}]   "
            f"[green]Pattern: {rule['pattern_type']}[/green]   "
            f"[blue]Typology: {rule['typology_code']}[/blue]   "
            f"[dim]Conf: {conf_str}[/dim]\n\n"
            f"[dim]When:[/dim]   {rule['condition_when']}\n"
            f"[dim]Verify:[/dim] {rule['verification_method']}"
        )
        console.print(Panel(body, title=f"Rule {i} of {len(rules)}", border_style="blue"))

        action = typer.prompt("  [a]pprove  [r]eject  [m]odify  [s]kip  [q]uit").strip().lower()

        if action == "q":
            break
        if action == "s":
            counts["skipped"] += 1
            continue
        if action == "a":
            update_rule_status(rule["rule_id"], "approved")
            counts["approved"] += 1
            console.print("  [green]✓ Approved[/green]\n")
        elif action == "r":
            reason = typer.prompt("  Reason for rejection")
            update_rule_status(rule["rule_id"], "rejected", note=reason)
            counts["rejected"] += 1
            console.print("  [red]✗ Rejected[/red]\n")
        elif action == "m":
            new_text = typer.prompt("  New rule text", default=rule["natural_language"])
            new_sev = typer.prompt("  New severity", default=rule["severity"])
            update_rule_status(
                rule["rule_id"],
                "modified",
                modified_text=new_text,
                modified_severity=new_sev,
            )
            counts["modified"] += 1
            console.print("  [yellow]✎ Modified and approved[/yellow]\n")
        else:
            console.print("  [dim](unrecognised — skipping)[/dim]\n")
            counts["skipped"] += 1

    console.print("\n[bold]Session summary:[/bold]")
    for k, v in counts.items():
        console.print(f"  {k}: {v}")


@app.command()
def stats():
    """Show DB-wide review status."""
    with get_session() as session:
        total = session.query(RuleModel).count()
        approved = session.query(RuleModel).filter_by(human_status="approved").count()
        modified = session.query(RuleModel).filter_by(human_status="modified").count()
        rejected = session.query(RuleModel).filter_by(human_status="rejected").count()
        pending = session.query(RuleModel).filter_by(human_status="pending").count()

    table = Table(title="Rule Review Status", show_header=True, header_style="bold magenta")
    table.add_column("Status")
    table.add_column("Count", justify="right")
    table.add_row("Approved (clean)", str(approved))
    table.add_row("Modified + approved", str(modified))
    table.add_row("Rejected", str(rejected))
    table.add_row("Pending review", str(pending))
    table.add_row("[bold]TOTAL[/bold]", f"[bold]{total}[/bold]")
    console.print(table)

    if total > 0:
        approval_rate = (approved + modified) / total * 100
        console.print(f"\nApproval rate so far: [bold]{approval_rate:.1f}%[/bold] of reviewed candidates")


if __name__ == "__main__":
    app()
