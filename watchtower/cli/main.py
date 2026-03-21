"""Watchtower CLI -- click-based command-line interface."""

from __future__ import annotations

import json
from pathlib import Path

import click

from watchtower.config import load_config
from watchtower.store.journal import Journal
from watchtower.store.findings import FindingsStore
from watchtower.store.events import EventStore
from watchtower.store.topology import TopologyStore
from watchtower.store.baselines import BaselineStore
from watchtower.governor import ResourceGovernor


def _get_journal(config_path: str | None) -> Journal:
    config = load_config(config_path)
    db_path = config.journal.path
    if not Path(db_path).parent.exists():
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return Journal(db_path)


@click.group()
@click.option("--config", "-c", default=None, help="Path to watchtower.yml")
@click.pass_context
def cli(ctx, config):
    """Watchtower: Distributed network observer for SONiC switches."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config
    ctx.obj["config"] = load_config(config)


@cli.group()
@click.pass_context
def show(ctx):
    """Show Watchtower status and data."""
    pass


@show.command()
@click.option("--history", is_flag=True, help="Show resolved findings from last 7 days")
@click.option("--severity", type=click.Choice(["info", "warning", "critical"]), default=None)
@click.pass_context
def findings(ctx, history, severity):
    """Show active or historical findings."""
    config = ctx.obj["config"]
    journal = Journal(config.journal.path)
    store = FindingsStore(journal)

    if history:
        items = store.get_history(days=config.journal.retention_detail_days)
        if not items:
            click.echo("No resolved findings in the last 7 days.")
            return
        click.echo(f"Resolved findings ({len(items)}):\n")
        for f in items:
            click.echo(f"  [{f['severity'].upper():8s}] {f['summary']}")
            click.echo(f"             Resolved: {f['resolved_at']}")
    else:
        items = store.get_active(severity=severity)
        if not items:
            click.echo("No active findings.")
            return

        counts = store.count_active()
        click.echo(f"Active findings: {counts['total']} "
                   f"({counts['critical']} critical, {counts['warning']} warning, "
                   f"{counts['info']} info)\n")
        for f in items:
            click.echo(f"  [{f['severity'].upper():8s}] {f['summary']}")
            click.echo(f"             ID: {f['finding_id']}  Time: {f['timestamp']}")
            if f.get("detail"):
                click.echo(f"             {f['detail']}")
            click.echo()

    journal.close()


@show.command()
@click.option("--fabric", is_flag=True, help="Include peer topology data")
@click.pass_context
def topology(ctx, fabric):
    """Show current LLDP topology."""
    config = ctx.obj["config"]
    journal = Journal(config.journal.path)
    store = TopologyStore(journal)

    entries = store.get_all()
    if not entries:
        click.echo("No topology data available.")
        journal.close()
        return

    click.echo(f"Local topology ({len(entries)} neighbors):\n")
    click.echo(f"  {'Local Port':<16s} {'Neighbor':<20s} {'Remote Port':<16s} {'Last Seen'}")
    click.echo(f"  {'-'*15:<16s} {'-'*19:<20s} {'-'*15:<16s} {'-'*19}")
    for e in entries:
        click.echo(
            f"  {e['local_port']:<16s} {e['neighbor_hostname']:<20s} "
            f"{e['neighbor_port']:<16s} {e['last_seen']}"
        )

    journal.close()


@show.command()
@click.option("--last", "seconds", default=3600, help="Show events from last N seconds")
@click.option("--severity", type=click.Choice(["info", "warning", "critical"]), default=None)
@click.pass_context
def events(ctx, seconds, severity):
    """Show recent events from the journal."""
    config = ctx.obj["config"]
    journal = Journal(config.journal.path)
    store = EventStore(journal)

    items = store.get_recent(seconds=seconds, severity=severity)
    if not items:
        click.echo("No recent events.")
        journal.close()
        return

    counts = store.count_recent(seconds=seconds)
    click.echo(f"Events in last {seconds}s: {counts['total']} "
               f"({counts['critical']} critical, {counts['warning']} warning, "
               f"{counts['info']} info)\n")

    for e in items:
        port_str = f" port={e['port']}" if e.get("port") else ""
        click.echo(f"  [{e['severity'].upper():8s}] {e['timestamp']} "
                   f"{e['category']}{port_str} ({e['source']})")

    journal.close()


@show.command()
@click.pass_context
def resources(ctx):
    """Show current resource usage and governor state."""
    governor = ResourceGovernor(ctx.obj["config"].resources)
    governor.update()
    status = governor.get_status()

    click.echo("Resource Governor Status:\n")
    click.echo(f"  State:                  {status['state']}")
    click.echo(f"  Watchtower CPU:         {status['watchtower_cpu_percent']:.1f}%")
    click.echo(f"  Watchtower Memory:      {status['watchtower_memory_mb']:.1f} MB")
    click.echo(f"  System CPU:             {status['system_cpu_percent']:.1f}%")
    click.echo(f"  System Memory:          {status['system_memory_percent']:.1f}%")
    click.echo(f"  Poll Interval Mult:     {status['poll_interval_multiplier']}x")
    click.echo(f"  Investigations Deferred: {status['investigations_deferred']}")


@show.command()
@click.argument("port")
@click.pass_context
def baselines(ctx, port):
    """Show baseline statistics for a port."""
    config = ctx.obj["config"]
    journal = Journal(config.journal.path)
    store = BaselineStore(journal)

    entries = store.get_all_for_port(port)
    if not entries:
        click.echo(f"No baseline data for {port}.")
        journal.close()
        return

    click.echo(f"Baselines for {port}:\n")
    current_metric = None
    for e in entries:
        if e["metric"] != current_metric:
            current_metric = e["metric"]
            click.echo(f"  {current_metric}:")
        click.echo(
            f"    hour={e['hour_of_week']:>3d}  "
            f"p50={e['p50']:.1f}  p95={e['p95']:.1f}  p99={e['p99']:.1f}  "
            f"samples={e['sample_count']}"
        )

    journal.close()
