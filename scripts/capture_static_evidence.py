"""Capture repository and experiment evidence as PNG files."""

import html
import subprocess
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SCREENSHOTS = ROOT / "docs" / "screenshots"
CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
STYLE = """
<style>
@page { size: 1440px 1000px; margin: 0; }
body { margin: 0; color: #202634; background: #f7f9fc;
       font: 17px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
main { max-width: 1220px; margin: 38px auto; padding: 34px 42px;
       background: white; border: 1px solid #dfe5ec; border-radius: 12px; }
h1 { color: #15304a; margin-top: 0; font-size: 34px; }
h2, h3 { color: #15304a; }
pre, code { font: 14px/1.42 Menlo, Consolas, monospace; }
pre { white-space: pre-wrap; padding: 20px; background: #111827; color: #e5e7eb;
      border-radius: 8px; overflow-wrap: anywhere; }
table { border-collapse: collapse; width: 100%; margin: 14px 0 24px; }
th, td { border: 1px solid #ccd4df; padding: 9px 11px; text-align: left; }
th { background: #eaf0f6; }
</style>
"""


def document(title: str, body: str) -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"{STYLE}</head><body><main><h1>{html.escape(title)}</h1>"
        f"{body}</main></body></html>"
    )


def screenshot(page, content: str, filename: str) -> None:
    page.set_content(content, wait_until="load")
    page.screenshot(path=SCREENSHOTS / filename, full_page=True)


def project_structure() -> str:
    result = subprocess.run(
        ["rg", "--files", "-g", "!.venv/**", "-g", "!.pytest_cache/**"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    paths = sorted(result.stdout.splitlines())
    return "CAP_931/\n" + "\n".join(f"  {path}" for path in paths)


def main() -> None:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(CHROME), headless=True, args=["--no-sandbox"]
        )
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        tree = f"<pre>{html.escape(project_structure())}</pre>"
        screenshot(
            page,
            document(
                "CAP 931 Project Structure",
                tree,
            ),
            "01-project-structure.png",
        )
        browser.close()


if __name__ == "__main__":
    main()
