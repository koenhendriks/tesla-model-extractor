"""Console output helpers (rich)."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from .catalog import Catalog, resolve_default_wheel, resolve_wheel_family

console = Console(stderr=True)


def vehicle_table(cat: Catalog) -> Table:
    t = Table(title="Vehicles in this app bundle", show_lines=False)
    t.add_column("id", style="bold")
    t.add_column("name")
    t.add_column("codename")
    t.add_column("API model / fascia")
    t.add_column("wheels")
    t.add_column("raw MB", justify="right")
    t.add_column("status")
    for v in cat.vehicles:
        fam = resolve_wheel_family(cat, v) if v.present else None
        n_wheels = len(cat.wheels_in_family(fam)) if fam else 0
        api = ", ".join(v.model_keys) + (f" ({', '.join(v.fascia_types)})" if v.fascia_types else "")
        dw = resolve_default_wheel(cat, v, fam) if v.present else None
        t.add_row(
            v.id,
            v.name,
            v.codename,
            api,
            f"{n_wheels} ({dw})" if fam else "-",
            f"{v.size_bytes / 1e6:.1f}" if v.present else "-",
            "[green]ok" if v.present else "[dim]not in bundle",
        )
    return t


def print_warnings(warnings: list[str], title: str = "warnings") -> None:
    if not warnings:
        return
    console.print(f"[yellow]{title}:[/]")
    for w in warnings:
        console.print(f"  [yellow]•[/] {w}")
