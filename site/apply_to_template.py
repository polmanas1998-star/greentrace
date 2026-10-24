"""Copy the GreenTrace site files into a Higgsfield website template checkout and patch its shell.

    python3 apply_to_template.py <checkout>/app

The template (React 19 + TanStack Start on a Cloudflare Worker) belongs to Higgsfield and is not
in this repository; only the files GreenTrace wrote are. This script is how they meet.
"""
import json
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
COVER_URL = "https://d2ol7oe51mr4n9.cloudfront.net/user_3L8gjUj2lTtb47gRJIU0QEolkkO/a31c2dac-d4b6-4fe9-9319-d7356faeeccc.jpg"


def main(app: Path) -> None:
    for folder in ("src", "public"):
        for path in (HERE / folder).rglob("*"):
            if path.is_file():
                target = app / path.relative_to(HERE)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
    shutil.copy2(HERE / "design-brief.md", app / "design-brief.md")

    styles = app / "src" / "styles.css"
    css = styles.read_text(encoding="utf-8")
    if '@import "./site.css";' not in css:
        css = css.replace('@import "tw-animate-css";', '@import "tw-animate-css";\n@import "./site.css";', 1)
        styles.write_text(css, encoding="utf-8")

    root = app / "src" / "routes" / "__root.tsx"
    tsx = root.read_text(encoding="utf-8")
    tsx = tsx.replace('import { button } from "@higgsfield/quanta/button";\n', "")
    tsx = tsx.replace('import { NotFound } from "@higgsfield/quanta/not-found";\n', "")
    tsx = tsx.replace('const DEFAULT_TITLE = "Higgsfield App";', 'const DEFAULT_TITLE = "GreenTrace";')
    tsx = tsx.replace('const DEFAULT_DESCRIPTION = "Higgsfield Generated Project";',
                      'const DEFAULT_DESCRIPTION = "Explainable EUDR checks, plot by plot.";')
    tsx = tsx.replace('{ name: "author", content: "Higgsfield" },', '{ name: "author", content: "GreenTrace" },')
    tsx = tsx.replace('      { name: "twitter:site", content: "@Higgsfield" },\n', "")
    tsx = re.sub(r"function NotFoundComponent\(\) \{.*?\n\}\n", NOT_FOUND, tsx, count=1, flags=re.S)
    tsx = re.sub(r"function ErrorComponent\(.*?\n\}\n", ERROR, tsx, count=1, flags=re.S)
    tsx = tsx.replace('<html lang="en" data-theme="default-dark" style={{ colorScheme: "dark" }}>',
                      '<html lang="en" className="gt" data-theme="default-dark" style={{ colorScheme: "dark" }}>')
    tsx = tsx.replace('<body className="bg-q-background-primary text-q-text-primary">',
                      '<body className="bg-gt-ground text-gt-text">')
    if '{ rel: "preload", href: "/fonts/Geist-Variable.woff2"' not in tsx:
        tsx = tsx.replace('{ rel: "stylesheet", href: appCss },',
                          '{ rel: "stylesheet", href: appCss },\n'
                          '      { rel: "preload", href: "/fonts/Geist-Variable.woff2", as: "font", type: "font/woff2", crossOrigin: "anonymous" as const },')
    root.write_text(tsx, encoding="utf-8")
    assert "@higgsfield/quanta" not in tsx, "the shell still imports Quanta"

    meta = app / "src" / "app-meta.json"
    data = json.loads(meta.read_text(encoding="utf-8"))
    data.update({
        "og_title": "GreenTrace",
        "og_description": "Proof, plot by plot: EUDR checks against the EU forest map, with every step shown.",
        "favicon_url": "/favicon.svg",
        # a real capture of the home page (the free plan generates no cover), uploaded to Higgsfield
        "og_image_url": COVER_URL,
        "marketplace_cover_url": COVER_URL,
    })
    meta.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


NOT_FOUND = '''function NotFoundComponent() {
  return (
    <div className="flex min-h-dvh items-center justify-center bg-gt-ground px-6 text-gt-text">
      <div className="max-w-md text-center">
        <div className="gt-mono text-[13px] text-gt-muted">404</div>
        <h1 className="gt-display mt-3 text-4xl">No plot here.</h1>
        <p className="mt-3 text-gt-text/70">This page does not exist.</p>
        <Link to="/" className="mt-8 inline-block bg-gt-signal px-5 py-3 text-[15px] font-semibold text-gt-ground">
          Back to GreenTrace
        </Link>
      </div>
    </div>
  );
}
'''

ERROR = '''function ErrorComponent({ error, reset }: { error: Error; reset: () => void }) {
  console.error(error);
  const router = useRouter();
  useEffect(() => {
    reportHiggsfieldError(error, { boundary: "tanstack_root_error_component" });
  }, [error]);

  return (
    <div className="flex min-h-dvh items-center justify-center bg-gt-ground px-6 text-gt-text">
      <div className="max-w-md text-center">
        <h1 className="gt-display text-4xl">This page did not load.</h1>
        <p className="mt-3 text-gt-text/70">Something went wrong on our side. Try again, or go back to the start.</p>
        <div className="mt-8 flex flex-wrap justify-center gap-3">
          <button
            onClick={() => {
              router.invalidate();
              reset();
            }}
            className="bg-gt-signal px-5 py-3 text-[15px] font-semibold text-gt-ground"
          >
            Try again
          </button>
          <a href="/" className="rounded-full border border-gt-line-strong px-5 py-3 text-[15px]">
            Back to GreenTrace
          </a>
        </div>
      </div>
    </div>
  );
}
'''

if __name__ == "__main__":
    main(Path(sys.argv[1]))
