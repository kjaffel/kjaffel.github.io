#!/usr/bin/env python3
"""
status_server.py -- preview kjaffel.github.io on your Mac BEFORE you push.

WHY
    GitHub Pages builds the site with Jekyll (blog.html, freetime.html and the
    posts contain Liquid code such as {% for post in site.posts %}). Opening the
    .html files directly, or `python -m http.server`, shows raw {% ... %} tags.
    The Mac's system Ruby (2.6) is too old to run Jekyll, so this script renders
    the same pages in pure Python instead.

HOW
    * On first run it creates a small virtual env in ~/.cache/kjaffel-preview
      and installs `python-liquid` and `markdown` there (nothing is installed
      system-wide, nothing is written into the website folder).
    * Every browser request re-renders the page from the files on disk, so
      just edit a file and press reload -- no restart needed.
    * Pages with a front-matter block (--- / ---) and everything in _posts/ are
      rendered; _layouts/default.html wraps the posts, as on GitHub.
    * /__status lists every page, post and photo and flags problems:
      unrendered {% %} tags, broken internal links, missing images.

RUN
    cd /Users/ustccmsmacpro01/kjaffel/kjaffel.github.io
    python3 scripts/status_server.py            # then open http://127.0.0.1:4000
    python3 scripts/status_server.py --port 8000 --open

    Stop with Ctrl-C.

NOTE
    This is a close approximation of Jekyll, not Jekyll itself. Only the Liquid
    features this site uses are supported. The final check is always the live
    site after the push.
"""
import argparse
import datetime
import html
import http.server
import mimetypes
import os
import re
import subprocess
import sys
import urllib.parse
import venv
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = Path.home() / ".cache" / "kjaffel-preview"
EXCLUDE_DIRS = {"scripts", "netlify", ".git", ".github", "node_modules", "_site"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".JPG", ".JPEG", ".PNG"}


# --------------------------------------------------------------------------
# bootstrap: make sure python-liquid and markdown are importable
# --------------------------------------------------------------------------
def bootstrap():
    try:
        import liquid  # noqa: F401
        import markdown  # noqa: F401
        return
    except ImportError:
        pass
    py = VENV / "bin" / "python"
    if not py.exists():
        print(f"First run: creating {VENV} ...")
        venv.create(VENV, with_pip=True)
        subprocess.check_call([str(py), "-m", "pip", "install", "-q", "python-liquid", "markdown"])
    if Path(sys.prefix).resolve() == VENV.resolve():
        sys.exit("Could not import python-liquid even inside the venv; delete " + str(VENV) + " and retry.")
    os.execv(str(py), [str(py)] + sys.argv)


bootstrap()

import markdown as md  # noqa: E402
from liquid import Environment  # noqa: E402


# --------------------------------------------------------------------------
# Jekyll-ish helpers
# --------------------------------------------------------------------------
FRONT = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n?---[ \t]*\r?\n?", re.S)


def split_front_matter(text):
    """Return (dict, body, has_front_matter)."""
    m = FRONT.match(text)
    if not m:
        return {}, text, False
    data = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            data[k.strip()] = v.strip().strip("'\"")
    return data, text[m.end():], True


def _wrap(fn):
    """python-liquid 3 passes a FilterContext as the first argument."""
    return lambda _ctx, *a: fn(*a)


def make_env(site_url):
    env = Environment()

    def relative_url(v):
        return str(v)

    def absolute_url(v):
        return site_url.rstrip("/") + "/" + str(v).lstrip("/")

    def uri_escape(v):
        return urllib.parse.quote(str(v), safe="/:?=&%")

    def number_of_words(v):
        return len(re.findall(r"\w+", re.sub(r"<[^>]+>", " ", str(v))))

    env.filters["relative_url"] = _wrap(relative_url)
    env.filters["absolute_url"] = _wrap(absolute_url)
    env.filters["uri_escape"] = _wrap(uri_escape)
    env.filters["number_of_words"] = _wrap(number_of_words)
    return env


class Site:
    """Re-reads the folder on every request (cheap for a site this size)."""

    def __init__(self, root):
        self.root = root
        self.config = {"title": "Khawla Jaffel", "url": "http://127.0.0.1"}
        cfg = root / "_config.yml"
        if cfg.exists():
            for line in cfg.read_text().splitlines():
                if re.match(r"^[A-Za-z_]+:\s*\S", line):
                    k, v = line.split(":", 1)
                    self.config[k.strip()] = v.strip().strip("'\"")
        self.env = make_env(self.config["url"])

    # ---- content discovery -------------------------------------------------
    def posts(self):
        out = []
        for p in sorted((self.root / "_posts").glob("*.md")):
            m = re.match(r"(\d{4})-(\d{2})-(\d{2})-(.+)\.md$", p.name)
            if not m:
                continue
            fm, body, _ = split_front_matter(p.read_text())
            date = datetime.datetime(int(m[1]), int(m[2]), int(m[3]))
            if fm.get("date"):
                try:
                    date = datetime.datetime.fromisoformat(fm["date"])
                except ValueError:
                    pass
            content = md.markdown(body, extensions=["extra", "sane_lists"])
            out.append({
                "title": fm.get("title", m[4]),
                "date": date,
                "image": fm.get("image", ""),
                "url": f"/{m[1]}/{m[2]}/{m[3]}/{m[4]}.html",
                "content": content,
                "id": f"/{m[1]}/{m[2]}/{m[3]}/{m[4]}",
                "path": p,
            })
        out.sort(key=lambda x: x["date"], reverse=True)
        return out

    def static_files(self):
        out = []
        for p in sorted(self.root.rglob("*")):
            rel = p.relative_to(self.root)
            if not p.is_file() or rel.parts[0] in EXCLUDE_DIRS or rel.parts[0].startswith("_"):
                continue
            if any(part.startswith(".") for part in rel.parts):
                continue
            if p.suffix in {".html", ".md"}:
                fm, _, has = split_front_matter(p.read_text(errors="ignore"))
                if has or p.suffix == ".md":
                    continue
            out.append({
                "path": "/" + rel.as_posix(),
                "name": p.name,
                "basename": p.stem,
                "extname": p.suffix,
            })
        return out

    def pages(self):
        """Top-level .html files (rendered if they carry front matter)."""
        return sorted(p for p in self.root.glob("*.html"))

    # ---- rendering ---------------------------------------------------------
    def render_string(self, template, page, posts=None):
        ctx = {
            "site": {**self.config, "posts": posts if posts is not None else self.posts(),
                     "static_files": self.static_files()},
            "page": page,
        }
        return self.env.parse(template).render(ctx)

    def render_post(self, post):
        layout = (self.root / "_layouts" / "default.html").read_text()
        page = {"title": post["title"], "date": post["date"], "url": post["url"], "image": post["image"]}
        content = post["content"]
        # `{{ content }}` in the layout receives the rendered post body.
        ctx = {"site": {**self.config, "posts": self.posts(), "static_files": self.static_files()},
               "page": page, "content": content}
        return self.env.parse(layout).render(ctx)

    def render_page(self, path):
        text = path.read_text()
        fm, body, has = split_front_matter(text)
        if not has:
            return text
        return self.render_string(body, {**fm, "url": "/" + path.name})

    # ---- status report -----------------------------------------------------
    def status(self):
        rows, problems = [], []
        posts = self.posts()
        rendered = {}
        for p in self.pages():
            try:
                rendered["/" + p.name] = self.render_page(p)
            except Exception as e:  # noqa: BLE001
                problems.append(f"{p.name}: render error: {e}")
        for post in posts:
            try:
                rendered[post["url"]] = self.render_post(post)
            except Exception as e:  # noqa: BLE001
                problems.append(f"{post['path'].name}: render error: {e}")

        for url, text in rendered.items():
            if re.search(r"\{%|\{\{", text):
                problems.append(f"{url}: unrendered Liquid left in output")
            for m in re.finditer(r'(?:href|src)="([^"#?]+)', text):
                target = m.group(1)
                if re.match(r"(https?:|mailto:|//|data:|javascript:)", target):
                    continue
                if target.startswith("/"):
                    resolved = target
                else:
                    base = os.path.dirname(url)
                    resolved = os.path.normpath(os.path.join(base, target))
                if resolved in rendered:
                    continue
                if not (self.root / urllib.parse.unquote(resolved).lstrip("/")).exists():
                    problems.append(f"{url}: missing target {target}")
            rows.append(url)
        photos = [f for f in self.static_files()
                  if f["path"].startswith("/images/photography/") and f["extname"] in IMAGE_EXT]
        big = [f for f in photos if (self.root / f["path"].lstrip("/")).stat().st_size > 3_000_000]
        for f in big:
            problems.append(f"{f['path']}: over 3 MB, resize to ~1600 px wide for faster loading")
        return rows, posts, photos, sorted(set(problems))


# --------------------------------------------------------------------------
# HTTP server
# --------------------------------------------------------------------------
class Handler(http.server.BaseHTTPRequestHandler):
    site: Site = None

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s\n" % (fmt % args))

    def send(self, code, body, ctype="text/html; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        site = Site(ROOT)  # fresh read: edits show up on reload
        path = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
        try:
            if path == "/__status":
                return self.send(200, self.status_page(site))
            if path in ("/", ""):
                path = "/index.html"
            for post in site.posts():
                if path in (post["url"], post["url"][:-5], post["url"][:-5] + "/"):
                    return self.send(200, site.render_post(post))
            rel = path.lstrip("/")
            target = (ROOT / rel).resolve()
            if ROOT not in target.parents and target != ROOT:
                return self.send(403, "forbidden")
            if target.is_dir():
                target = target / "index.html"
            if not target.exists() and (ROOT / (rel + ".html")).exists():
                target = ROOT / (rel + ".html")
            if not target.exists():
                return self.send(404, f"<h1>404</h1><p>{html.escape(path)} not found. <a href='/__status'>status</a></p>")
            if target.suffix == ".html" and target.parent == ROOT:
                return self.send(200, site.render_page(target))
            ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            return self.send(200, target.read_bytes(), ctype)
        except Exception as e:  # noqa: BLE001
            import traceback
            return self.send(500, "<h1>Render error</h1><pre>%s</pre>" % html.escape(traceback.format_exc()))

    def status_page(self, site):
        rows, posts, photos, problems = site.status()
        li = lambda items: "".join(f"<li>{items_}</li>" for items_ in items)  # noqa: E731
        link = lambda u: f'<a href="{html.escape(u)}">{html.escape(u)}</a>'  # noqa: E731
        ok = "<p style='color:#2a7'>&#10003; No problems found.</p>" if not problems else \
             "<ul style='color:#c33'>" + li(html.escape(p) for p in problems) + "</ul>"
        return f"""<!doctype html><meta charset=utf-8><title>Site status</title>
<style>body{{font:16px/1.5 system-ui;max-width:760px;margin:2rem auto;padding:0 1rem}}h2{{margin-top:1.8rem}}</style>
<h1>Preview status</h1>
<p>Serving <code>{ROOT}</code>. Reload any page after editing; nothing to restart.</p>
<h2>Problems ({len(problems)})</h2>{ok}
<h2>Pages and posts ({len(rows)})</h2><ul>{li(link(r) for r in rows)}</ul>
<h2>Blog posts found ({len(posts)})</h2><ul>{li(html.escape(p['title']) + ' <small>' + p['date'].strftime('%Y-%m-%d') + '</small>' for p in posts)}</ul>
<h2>Photos in images/photography ({len(photos)})</h2><ul>{li(html.escape(f['path']) for f in photos) or '<li>none yet</li>'}</ul>
"""


def main():
    ap = argparse.ArgumentParser(description="Preview the Jekyll site locally before pushing.")
    ap.add_argument("--port", type=int, default=4000)
    ap.add_argument("--open", action="store_true", help="open the browser automatically")
    args = ap.parse_args()
    Handler.site = Site(ROOT)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    base = f"http://127.0.0.1:{args.port}"
    print(f"Previewing {ROOT}\n  site:   {base}/\n  status: {base}/__status\n  (Ctrl-C to stop)")
    if args.open:
        webbrowser.open(base + "/__status")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
