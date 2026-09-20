"""Console output helpers (rich)."""

from __future__ import annotations

import os
import sys

from rich.console import Console
from rich.table import Table

from .catalog import Catalog, resolve_default_wheel, resolve_wheel_family

# Under `docker run` without -t stderr is not a terminal, so rich falls back to
# 80 columns and squeezes the vehicle table; give it room unless the caller
# pinned COLUMNS (which rich honours itself).
_WIDTH = None if sys.stderr.isatty() or os.environ.get("COLUMNS") else 120
console = Console(stderr=True, width=_WIDTH)


def vehicle_table(cat: Catalog) -> Table:
    t = Table(title="Vehicles in this app bundle", show_lines=False, pad_edge=False, padding=(0, 1))
    # ids are copy-pasted into --models, so they must stay intact on a narrow
    # (80-column, non-tty) console: reserve their full width and let the rest
    # fold rather than hit rich's default ellipsis truncation.
    id_width = max((len(v.id) for v in cat.vehicles), default=2)
    t.add_column("id", style="bold", no_wrap=True, min_width=id_width)
    t.add_column("name", overflow="fold")
    t.add_column("codename", overflow="fold")
    t.add_column("API model / fascia", overflow="fold")
    t.add_column("wheels", overflow="fold")
    t.add_column("raw MB", justify="right", no_wrap=True)
    t.add_column("status", overflow="fold")
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
