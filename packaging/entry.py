"""Entry point of the desktop binaries built by PyInstaller (see tesla-model-extractor.spec).

No arguments, or a bundle path (dropped on the app icon): open the window. A CLI subcommand or flag: run the CLI.
`--smoke`: build the window offscreen and exit, for the release workflow.
"""

from __future__ import annotations

import os
import sys

CLI_COMMANDS = {"extract", "list", "inspect", "unreal", "dae", "validate", "compare-legacy"}


def _ensure_streams() -> None:
    # A windowed build on Windows starts with sys.stdout / sys.stderr set to None; rich and logging write there.
    if sys.stdout is not None and sys.stderr is not None:
        return
    from tesla_model_extractor.gdre import cache_dir

    path = cache_dir().parent / "desktop.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = open(path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115 – lives as long as the process
    sys.stdout = sys.stdout or stream
    sys.stderr = sys.stderr or stream


def _smoke() -> int:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtGui import QFontDatabase, QFontInfo
    from PySide6.QtWidgets import QApplication

    from tesla_model_extractor import GDRE_VERSION, __version__
    from tesla_model_extractor.gui.window import MainWindow
    from tesla_model_extractor.rules.loader import all_codename_rules, default_rules

    app = QApplication([])
    win = MainWindow()
    win.show()
    app.processEvents()
    assert default_rules(), "bundled rules/_default.yaml missing"
    families = QFontDatabase.families()
    # Linux reads fonts through the system's fontconfig; a bundled copy that cannot parse its config finds none
    assert families or not sys.platform.startswith("linux"), "Qt found no fonts (fontconfig)"
    print(
        f"tesla-model-extractor {__version__} (GDRE Tools {GDRE_VERSION}): {len(all_codename_rules())} rules files, "
        f"{len(families)} font families, UI font {QFontInfo(app.font()).family()!r}"
    )
    win.close()
    return 0


def main() -> int:
    _ensure_streams()
    args = [a for a in sys.argv[1:] if not a.startswith("-psn_")]  # macOS Finder process serial number
    if args == ["--smoke"]:
        return _smoke()
    if args and (args[0] in CLI_COMMANDS or args[0].startswith("-")):
        from tesla_model_extractor.cli import main as cli_main

        return cli_main(args)
    from tesla_model_extractor.gui.window import run

    return run(source=args[0] if args else None)


if __name__ == "__main__":
    sys.exit(main())
