#!/usr/bin/env python3
"""
Genera copias de texto plano y una página HTML estática, fáciles de leer por
lectores automáticos que a veces no exponen el contenido de ciertos JSON.

Debe ejecutarse después de generar DXView y HamQSL y antes de subir el
artefacto de GitHub Pages.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

PUBLIC = Path("public")
READER = PUBLIC / "reader"

SOURCES = [
    ("DXView summary diagnostic",
     PUBLIC / "diagnostics/dxview-summary-diagnostic.json",
     READER / "dxview-summary-diagnostic.txt"),
    ("HamQSL summary",
     PUBLIC / "data/hamqsl-summary.json",
     READER / "hamqsl-summary.txt"),
    ("HamQSL diagnostic",
     PUBLIC / "diagnostics/hamqsl-diagnostic.json",
     READER / "hamqsl-diagnostic.txt"),
]


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def main() -> int:
    READER.mkdir(parents=True, exist_ok=True)
    sections: list[str] = []
    index_rows: list[str] = []

    for title, source, output in SOURCES:
        if source.exists():
            try:
                value = load_json(source)
                text = pretty_json(value)
                status = "ok"
            except Exception as exc:  # noqa: BLE001
                text = json.dumps(
                    {
                        "status": "reader_export_error",
                        "source": str(source),
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                    ensure_ascii=False,
                    indent=2,
                ) + "\n"
                status = "error"
        else:
            text = json.dumps(
                {
                    "status": "source_missing",
                    "source": str(source),
                },
                ensure_ascii=False,
                indent=2,
            ) + "\n"
            status = "missing"

        output.write_text(text, encoding="utf-8")

        rel_output = output.relative_to(PUBLIC).as_posix()
        rel_source = source.relative_to(PUBLIC).as_posix()
        index_rows.append(
            f"<tr><td>{html.escape(title)}</td>"
            f"<td>{html.escape(status)}</td>"
            f"<td><a href='../{html.escape(rel_output)}'>texto</a></td>"
            f"<td><a href='../{html.escape(rel_source)}'>json original</a></td></tr>"
        )
        sections.append(
            f"<section><h2>{html.escape(title)}</h2>"
            f"<p>Estado de exportación: <strong>{html.escape(status)}</strong></p>"
            f"<pre>{html.escape(text)}</pre></section>"
        )

    html_page = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>HF reader-friendly diagnostics</title>
<style>
body{font-family:system-ui,sans-serif;max-width:1200px;margin:2rem auto;padding:0 1rem}
table{border-collapse:collapse;width:100%}
th,td{border:1px solid #ccc;padding:.5rem;text-align:left}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f5f5;padding:1rem}
section{margin-top:2rem}
</style>
</head>
<body>
<h1>HF reader-friendly diagnostics</h1>
<p>Copias estáticas en texto plano de los JSON que algunos lectores no muestran correctamente.</p>
<table>
<thead><tr><th>Fuente</th><th>Exportación</th><th>Texto</th><th>JSON original</th></tr></thead>
<tbody>
""" + "\n".join(index_rows) + """
</tbody>
</table>
""" + "\n".join(sections) + """
</body>
</html>
"""
    (READER / "index.html").write_text(html_page, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
