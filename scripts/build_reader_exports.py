#!/usr/bin/env python3
from __future__ import annotations
import html, json
from pathlib import Path
from typing import Any

PUBLIC = Path("public")
READER = PUBLIC / "reader"
SOURCES = [
    ("DXView summary diagnostic", PUBLIC/"diagnostics/dxview-summary-diagnostic.json", READER/"dxview-summary-diagnostic.txt"),
    ("DXView general diagnostic", PUBLIC/"diagnostics/dxview-diagnostic.json", READER/"dxview-diagnostic.txt"),
    ("HamQSL summary", PUBLIC/"data/hamqsl-summary.json", READER/"hamqsl-summary.txt"),
    ("HamQSL diagnostic", PUBLIC/"diagnostics/hamqsl-diagnostic.json", READER/"hamqsl-diagnostic.txt"),
    ("NOAA summary", PUBLIC/"data/noaa-summary.json", READER/"noaa-summary.txt"),
    ("NOAA diagnostic", PUBLIC/"diagnostics/noaa-diagnostic.json", READER/"noaa-diagnostic.txt"),
    ("QRN Spain summary", PUBLIC/"data/qrn-spain-summary.json", READER/"qrn-spain-summary.txt"),
    ("QRN diagnostic", PUBLIC/"diagnostics/qrn-diagnostic.json", READER/"qrn-diagnostic.txt"),
    ("GIRO Spain summary", PUBLIC/"data/giro-spain-summary.json", READER/"giro-spain-summary.txt"),
    ("GIRO diagnostic", PUBLIC/"diagnostics/giro-diagnostic.json", READER/"giro-diagnostic.txt"),
    ("PSKReporter HF summary", PUBLIC/"data/pskreporter-hf-summary.json", READER/"pskreporter-hf-summary.txt"),
    ("PSKReporter diagnostic", PUBLIC/"diagnostics/pskreporter-diagnostic.json", READER/"pskreporter-diagnostic.txt"),
]

def load(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def main():
    READER.mkdir(parents=True, exist_ok=True)
    rows=[]; sections=[]
    for title, src, dst in SOURCES:
        if src.exists():
            try:
                text=json.dumps(load(src),ensure_ascii=False,indent=2)+"\n"; status="ok"
            except Exception as e:
                text=json.dumps({"status":"reader_export_error","source":str(src),"error":str(e)},ensure_ascii=False,indent=2)+"\n"; status="error"
        else:
            text=json.dumps({"status":"source_missing","source":str(src)},ensure_ascii=False,indent=2)+"\n"; status="missing"
        dst.write_text(text,encoding="utf-8")
        rel_dst=dst.relative_to(PUBLIC).as_posix(); rel_src=src.relative_to(PUBLIC).as_posix()
        rows.append(f"<tr><td>{html.escape(title)}</td><td>{status}</td><td><a href='../{rel_dst}'>texto</a></td><td><a href='../{rel_src}'>json</a></td></tr>")
        sections.append(f"<section><h2>{html.escape(title)}</h2><pre>{html.escape(text)}</pre></section>")
    page="""<!doctype html><html lang='es'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>HF reader</title><style>body{font-family:system-ui;max-width:1200px;margin:2rem auto;padding:0 1rem}table{border-collapse:collapse;width:100%}th,td{border:1px solid #ccc;padding:.5rem;text-align:left}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f5f5;padding:1rem}</style></head><body><h1>HF reader-friendly data</h1><table><thead><tr><th>Fuente</th><th>Estado</th><th>Texto</th><th>JSON</th></tr></thead><tbody>"""+''.join(rows)+"""</tbody></table>"""+''.join(sections)+"""</body></html>"""
    (READER/"index.html").write_text(page,encoding="utf-8")
    return 0
if __name__=="__main__":
    raise SystemExit(main())
