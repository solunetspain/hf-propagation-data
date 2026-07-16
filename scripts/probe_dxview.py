from __future__ import annotations

import asyncio
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

from playwright.async_api import async_playwright

TARGET = "https://hf.dxview.org/perspective/IN91PO"
OUTPUT = Path("dxview-probe")
INTERESTING = re.compile(
    r"(?i)(api|json|geojson|websocket|socket|tile|vector|pbf|mvt|wms|wmts|"
    r"activity|spot|report|muf|perspective|band|grid|heat|layer)"
)
URL_RE = re.compile(
    r"""(?ix)(wss?://[^\s"'<>]+|https?://[^\s"'<>]+|/[a-z0-9_./{}?=&%:+-]+)"""
)


async def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    requests_seen = []
    responses_seen = []
    websockets_seen = []
    scripts = {}

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1600, "height": 1000},
            user_agent="solunetspain-hf-propagation-data/1.0",
            record_har_path=str(OUTPUT / "dxview.har"),
            record_har_content="embed",
        )
        page = await context.new_page()

        page.on(
            "request",
            lambda request: requests_seen.append(
                {
                    "method": request.method,
                    "url": request.url,
                    "resource_type": request.resource_type,
                    "post_data": request.post_data,
                }
            ),
        )

        response_tasks = []

        async def capture_response(response):
            headers = await response.all_headers()
            record = {
                "status": response.status,
                "url": response.url,
                "content_type": headers.get("content-type", ""),
                "resource_type": response.request.resource_type,
            }
            responses_seen.append(record)

            if response.request.resource_type == "script":
                try:
                    scripts[response.url] = await response.text()
                except Exception:
                    pass

        page.on(
            "response",
            lambda response: response_tasks.append(
                asyncio.create_task(capture_response(response))
            ),
        )

        def websocket_handler(ws):
            record = {"url": ws.url, "sent": [], "received": []}
            websockets_seen.append(record)
            ws.on(
                "framesent",
                lambda event: record["sent"].append(str(event)[:2000]),
            )
            ws.on(
                "framereceived",
                lambda event: record["received"].append(str(event)[:2000]),
            )

        page.on("websocket", websocket_handler)

        await page.goto(TARGET, wait_until="networkidle", timeout=90000)
        await page.screenshot(path=str(OUTPUT / "in91po.png"), full_page=True)
        (OUTPUT / "page.html").write_text(
            await page.content(), encoding="utf-8"
        )

        inventory = await page.evaluate(
            """
            () => ({
              scripts: [...document.scripts].map(s => s.src).filter(Boolean),
              links: [...document.querySelectorAll('a')].map(a => ({
                text: (a.textContent || '').trim(),
                href: a.href
              })),
              selects: [...document.querySelectorAll('select')].map((s, i) => ({
                index: i,
                id: s.id,
                name: s.name,
                options: [...s.options].map(o => ({
                  text: o.text,
                  value: o.value
                }))
              })),
              canvases: [...document.querySelectorAll('canvas')].map((c, i) => ({
                index: i,
                width: c.width,
                height: c.height,
                className: c.className
              }))
            })
            """
        )
        (OUTPUT / "dom-inventory.json").write_text(
            json.dumps(inventory, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # Prueba rutas de banda públicas para comprobar cambios de red.
        for band in ("7", "14", "18", "21", "28"):
            try:
                await page.goto(
                    urljoin(TARGET, f"/band/{band}"),
                    wait_until="networkidle",
                    timeout=60000,
                )
                await page.wait_for_timeout(1500)
            except Exception:
                pass

        await page.goto(TARGET, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(15000)

        if response_tasks:
            await asyncio.gather(*response_tasks, return_exceptions=True)

        await context.close()
        await browser.close()

    candidates = {}

    def add_candidate(url, source, bonus=0):
        if not INTERESTING.search(url):
            return
        entry = candidates.setdefault(
            url, {"url": url, "score": 0, "sources": set()}
        )
        entry["sources"].add(source)
        entry["score"] += 1 + bonus

    for item in requests_seen:
        add_candidate(item["url"], "network-request", 5)
    for item in responses_seen:
        add_candidate(item["url"], "network-response", 5)
    for ws in websockets_seen:
        add_candidate(ws["url"], "websocket", 20)

    for script_url, body in scripts.items():
        for match in URL_RE.findall(body):
            add_candidate(urljoin(script_url, match.rstrip("),;]")), script_url)

    ranked = sorted(
        (
            {
                "url": entry["url"],
                "score": entry["score"],
                "sources": sorted(entry["sources"]),
            }
            for entry in candidates.values()
        ),
        key=lambda item: (-item["score"], item["url"]),
    )

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target": TARGET,
        "request_count": len(requests_seen),
        "response_count": len(responses_seen),
        "resource_types": dict(
            Counter(item["resource_type"] for item in requests_seen)
        ),
        "websockets": websockets_seen,
        "candidate_endpoints": ranked,
        "validation_rule": (
            "Un candidato no se considera funcional hasta repetirlo fuera "
            "del navegador y demostrar que cambia por banda o perspectiva."
        ),
    }

    (OUTPUT / "requests.json").write_text(
        json.dumps(requests_seen, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (OUTPUT / "responses.json").write_text(
        json.dumps(responses_seen, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (OUTPUT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
