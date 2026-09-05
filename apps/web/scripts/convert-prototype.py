#!/usr/bin/env python3
"""Regenerate the design mockup from the Claude Design prototype export.

    python3 apps/web/scripts/convert-prototype.py [Lumina-Web.html]

Reads the self-contained prototype, and writes:

    src/assets/lumina-disc.png     the bundled logo, byte-exact
    src/mock/data.ts               LINES, MY, SCREENS, TITLES
    src/mock/useLuminaMock.ts      the prototype's behaviour, as a hook
    src/mock/PrototypeShell.tsx    the prototype's markup, as JSX

Doing this by script rather than by hand is what makes "exactly as the design" checkable:
every CSS string passes through byte-identical, so fidelity is a property of this converter
instead of a thousand hand-transcriptions. Re-run it whenever the design export changes.
"""
from __future__ import annotations

import base64
import gzip
import json
import pathlib
import re
import sys
from html.parser import HTMLParser

ROOT = pathlib.Path(__file__).resolve().parents[1]  # apps/web
SRC = ROOT / "src"

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
        "source", "track", "wbr"}

ATTR_MAP = {
    "class": "className", "for": "htmlFor", "tabindex": "tabIndex", "readonly": "readOnly",
    "maxlength": "maxLength", "autocomplete": "autoComplete", "autofocus": "autoFocus",
    "stroke-width": "strokeWidth", "stroke-linecap": "strokeLinecap",
    "stroke-linejoin": "strokeLinejoin", "stroke-dasharray": "strokeDasharray",
    "stroke-dashoffset": "strokeDashoffset", "fill-rule": "fillRule", "clip-rule": "clipRule",
    "clip-path": "clipPath", "stop-color": "stopColor", "stop-opacity": "stopOpacity",
    "fill-opacity": "fillOpacity", "stroke-opacity": "strokeOpacity",
    "text-anchor": "textAnchor", "dominant-baseline": "dominantBaseline",
    "font-size": "fontSize", "font-weight": "fontWeight", "font-family": "fontFamily",
    "letter-spacing": "letterSpacing", "gradientunits": "gradientUnits",
    "gradienttransform": "gradientTransform", "patternunits": "patternUnits",
    "viewbox": "viewBox", "preserveaspectratio": "preserveAspectRatio",
    "xlink:href": "xlinkHref", "srcset": "srcSet", "colspan": "colSpan", "rowspan": "rowSpan",
    "datetime": "dateTime", "novalidate": "noValidate", "enterkeyhint": "enterKeyHint",
    "inputmode": "inputMode", "spellcheck": "spellCheck", "contenteditable": "contentEditable",
    "crossorigin": "crossOrigin",
}
BOOL_ATTRS = {"disabled", "checked", "selected", "readonly", "autofocus", "multiple",
              "required", "hidden"}

#: Landmarks tagged with `data-lum` during conversion, keyed by a unique substring of the
#: element's inline style. They give the app a stable handle on the parts of the export that
#: are presentation scaffolding rather than product UI, so those can be dropped without
#: brittle positional CSS like `div > div:first-child`.
#:
#: Each signature MUST match exactly once. A design change that renames or restyles one of
#: these fails the build loudly (see `check_landmarks`) instead of silently un-hiding the
#: scaffolding, which is the whole reason for matching on a signature rather than a position.
LANDMARKS: dict[str, tuple[str, int]] = {
    # signature in the inline style      -> (data-lum value, exact expected count)
    "max-width:1420px": ("harness", 1),                    # screen nav + variant switchers
    "background:rgba(12,16,32,.9)": ("titlebar", 1),       # fake browser title bar
    "background:#070a16;border-radius:14px": ("window", 1),  # fake browser window frame
    "width:232px;flex:none": ("sidebar", 1),               # the product's own nav
    # Every screen body is a fixed-width centred column, sized for the export's 1300px
    # artboard. Tagging them lets the app make that width responsive without hand-editing
    # generated markup.
    "max-width:680px;margin:0 auto": ("col", 8),
    "max-width:620px;margin:0 auto": ("col", 1),
}

_seen: dict[str, int] = {}


def landmark_for(style: str) -> str | None:
    for sig, (name, _) in LANDMARKS.items():
        if sig in style:
            _seen[sig] = _seen.get(sig, 0) + 1
            return name
    return None


def check_landmarks() -> None:
    wrong = [
        f"{name!r} via {sig!r}: expected {want}, matched {_seen.get(sig, 0)}"
        for sig, (name, want) in LANDMARKS.items()
        if _seen.get(sig, 0) != want
    ]
    if wrong:
        raise SystemExit(
            "landmark signatures no longer match the design export:\n  "
            + "\n  ".join(wrong)
            + "\nUpdate LANDMARKS in this script to match the new markup."
        )


# ----------------------------------------------------------------- unpacking

def unpack(path: pathlib.Path) -> tuple[str, bytes | None]:
    """Return (template html, logo png bytes) from the bundled prototype."""
    raw = path.read_text(errors="replace")
    manifest = json.loads(
        re.search(r'<script type="__bundler/manifest"[^>]*>(.*?)</script>', raw, re.S).group(1)
    )
    template = json.loads(
        re.search(r'<script type="__bundler/template"[^>]*>(.*?)</script>', raw, re.S).group(1)
    )
    logo = None
    for entry in manifest.values():
        if entry.get("mime") == "image/png":
            data = base64.b64decode(entry["data"])
            logo = gzip.decompress(data) if entry.get("compressed") else data
            break
    return template, logo


def brace_span(s: str, open_idx: int) -> tuple[int, int]:
    """Span of the {...} beginning at open_idx, skipping strings and comments."""
    depth, i, n, quote = 0, open_idx, len(s), None
    while i < n:
        c = s[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c == "/" and i + 1 < n and s[i + 1] == "/":
            j = s.find("\n", i)
            i = n if j < 0 else j
            continue
        elif c == "/" and i + 1 < n and s[i + 1] == "*":
            j = s.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        elif c in "\"'`":
            quote = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return open_idx, i + 1
        i += 1
    raise ValueError("unbalanced braces")


# ------------------------------------------------------------- html -> jsx

class Node:
    def __init__(self, tag=None, attrs=None):
        self.tag, self.attrs, self.kids, self.text = tag, dict(attrs or {}), [], None


class Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        n = Node(tag, attrs)
        self.stack[-1].kids.append(n)
        if tag not in VOID:
            self.stack.append(n)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].kids.append(Node(tag, attrs))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        if data.strip():
            t = Node()
            t.text = data
            self.stack[-1].kids.append(t)


def jsstr(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def esc_literal(s: str) -> str:
    s = re.sub(r"\s+", " ", s)
    if not s.strip():
        return " " if s else ""
    return "{" + jsstr(s) + "}" if any(c in s for c in "{}<>") else s


def jsx_text(s: str) -> str:
    """Emit a text run, wrapping each interpolation the way the prototype runtime does.

    The design runtime renders every `{{ expr }}` as `<span class="sc-interp">`, not as a
    bare text node. That span is a real box, so in a flex container it becomes its own flex
    item and `gap` applies between it and any adjacent literal text. A bare text node merges
    with its neighbours into a single anonymous flex item and the gap silently disappears —
    which is why "Continue →" rendered with a narrower gap than the design until this
    matched the runtime.
    """
    out, pos = [], 0
    for m in re.finditer(r"\{\{(.*?)\}\}", s, re.S):
        lit = s[pos:m.start()]
        if lit:
            out.append(esc_literal(lit))
        out.append('<span className="sc-interp">{' + m.group(1).strip() + "}</span>")
        pos = m.end()
    if s[pos:]:
        out.append(esc_literal(s[pos:]))
    return "".join(out)


def expr_of(v: str) -> str | None:
    m = re.fullmatch(r"\s*\{\{(.*?)\}\}\s*", v or "", re.S)
    return m.group(1).strip() if m else None


def value_expr(v: str) -> str:
    """The JS expression for an attribute value, without JSX braces.

    Handles all three shapes the template uses: a whole-value binding, a plain literal, and
    a *mixture* of the two — `style="{{ f.btnBase }};background:rgba(...)"`. The mixed case
    is easy to miss and fails silently: treating it as a literal drops the bound half, which
    is where padding, height and radius live.
    """
    e = expr_of(v)
    if e is not None:
        return e
    if "{{" not in v:
        return jsstr(v)
    parts, pos = [], 0
    for m in re.finditer(r"\{\{(.*?)\}\}", v, re.S):
        if v[pos:m.start()]:
            parts.append(jsstr(v[pos:m.start()]))
        parts.append("(" + m.group(1).strip() + ")")
        pos = m.end()
    if v[pos:]:
        parts.append(jsstr(v[pos:]))
    return " + ".join(parts)


def attr_value(v: str) -> str:
    return "{" + value_expr(v) + "}"


def camel_event(name: str) -> str:
    head, *tail = name[len("sc-camel-"):].split("-")
    return head + "".join(t.capitalize() for t in tail)


def emit(n: Node, ind: int, out: list[str], depth: int = 0) -> None:
    pad = "  " * ind
    if n.text is not None:
        t = jsx_text(n.text)
        if t.strip():
            out.append(pad + t)
        return

    if n.tag == "sc-if":
        out.append(f"{pad}{{({expr_of(n.attrs.get('value', '')) or 'true'}) ? (")
        out.append(f"{pad}  <>")
        for k in n.kids:
            emit(k, ind + 2, out, depth)
        out.append(f"{pad}  </>")
        out.append(f"{pad}) : null}}")
        return

    if n.tag == "sc-for":
        lst = expr_of(n.attrs.get("list", "")) or "[]"
        var, idx = n.attrs.get("as", "it"), f"i{depth}"
        out.append(f"{pad}{{({lst} ?? []).map(({var}: any, {idx}: number) => (")
        out.append(f"{pad}  <React.Fragment key={{{idx}}}>")
        for k in n.kids:
            emit(k, ind + 2, out, depth + 1)
        out.append(f"{pad}  </React.Fragment>")
        out.append(f"{pad}))}}")
        return

    parts, hover = [], None
    for k, v in n.attrs.items():
        if k.startswith("hint-"):
            continue
        if k == "style-hover":
            hover = v
            continue
        if k.startswith("sc-camel-"):
            e = expr_of(v)
            parts.append(f"{camel_event(k)}={{{e}}}" if e else f"{camel_event(k)}={attr_value(v)}")
            continue
        if k == "style":
            parts.append("style={sx(" + value_expr(v) + ")}")
            name = landmark_for(v)
            if name:
                parts.append(f'data-lum="{name}"')
            continue
        if k == "src" and re.fullmatch(r"[0-9a-f-]{36}", v or ""):
            parts.append("src={logoUrl}")
            continue
        name = ATTR_MAP.get(k, k)
        if v is None or (k in BOOL_ATTRS and v in ("", k)):
            parts.append(name)
        else:
            parts.append(f"{name}={attr_value(v)}")
    if hover is not None:
        parts.append("className={hv(" + value_expr(hover) + ")}")

    attrs = (" " + " ".join(parts)) if parts else ""
    if not n.kids:
        out.append(f"{pad}<{n.tag}{attrs} />")
        return
    out.append(f"{pad}<{n.tag}{attrs}>")
    for k in n.kids:
        emit(k, ind + 1, out, depth)
    out.append(f"{pad}</{n.tag}>")


def convert(html: str, indent: int = 3) -> str:
    b = Builder()
    b.feed(html)
    b.close()
    out: list[str] = []
    for k in b.root.kids:
        emit(k, indent, out)
    return "\n".join(out)


# ---------------------------------------------------------------- bindings

def loop_spans(clean: str, var: str) -> list[tuple[int, int]]:
    out = []
    for m in re.finditer(rf'<sc-for[^>]*as="{re.escape(var)}"[^>]*>', clean):
        depth = 0
        for t in re.finditer(r"</?sc-for\b", clean[m.start():]):
            depth += 1 if not t.group(0).startswith("</") else -1
            if depth == 0:
                out.append((m.start(), m.start() + t.end()))
                break
    return out


def binding_roots(body: str) -> list[str]:
    """Names the template reads from the hook.

    A loop variable is excluded unless it is *also* used outside every loop that binds it —
    which `s` is, being both the shared style table and the scene loop variable. The emitted
    `.map((s) => ...)` parameter shadows the destructured binding inside the loop, exactly
    reproducing the prototype's own template scoping.
    """
    clean = re.sub(r'hint-placeholder-(val|count)="[^"]*"', "", body)
    roots = {e.strip().split(".")[0] for e in re.findall(r"\{\{(.*?)\}\}", clean, re.S)}
    roots -= {"true", "false"}

    for var in set(re.findall(r'<sc-for[^>]*as="([^"]+)"', clean)):
        spans = loop_spans(clean, var)
        uses = [m.start() for m in re.finditer(rf"\{{\{{\s*{re.escape(var)}\b", clean)]
        if not any(not any(a <= u < b for a, b in spans) for u in uses):
            roots.discard(var)

    return sorted(r for r in roots if re.fullmatch(r"[A-Za-z_$][\w$]*", r))


# ------------------------------------------------------------------- write

BANNER = "GENERATED by apps/web/scripts/convert-prototype.py — do not hand-edit."


def write_data(js: str) -> None:
    start = js.index("const LINES")
    end = js.index("class Component")
    src = js[start:end].rstrip()
    src = src.replace(
        "const LINES =",
        "export const LINES: readonly (readonly [string, string, string, string])[] =",
    )
    src = src.replace("const MY = {", "export const MY: Record<string, string> = {")
    src = src.replace("const SCREENS =", "export const SCREENS =")
    src = src.replace("const TITLES = {", "export const TITLES: Record<string, string> = {")
    (SRC / "mock/data.ts").write_text(
        f"/**\n * Content and copy tables from the design prototype.\n *\n"
        f" * {BANNER}\n *\n"
        " * Lifted verbatim, including the Burmese table — the prototype is already bilingual,\n"
        " * which is what the backend language-pack work is being built against. This is a\n"
        " * mirror of the design, not a source of product strings.\n */\n" + src + "\n"
    )


METHODS = ["toggleRec", "regen", "toggleSel", "move", "del", "addScene", "runUpgrade",
           "render1", "renderAll"]


def annotate_arrows(s: str) -> str:
    """Give every bare arrow parameter an explicit `any`.

    The prototype is JavaScript, so its callbacks (`x => ...`, `(l, i) => ...`) carry no
    types and would trip `noImplicitAny`. Annotating mechanically keeps the app's strict
    settings intact everywhere else rather than blanket-disabling checks on these files.
    """
    # ([a, b]) => ...  /  ({ a }) => ...
    s = re.sub(r"\((\[[^()\[\]]*\]|\{[^{}]*\})\)\s*=>", r"(\1: any) =>", s)
    # (a, b) => ...  with no annotations already present
    def pair(m: re.Match[str]) -> str:
        names = [n.strip() for n in m.group(1).split(",")]
        return "(" + ", ".join(f"{n}: any" for n in names) + ") =>"
    s = re.sub(r"\(([a-zA-Z_$][\w$]*(?:\s*,\s*[a-zA-Z_$][\w$]*)+)\)\s*=>", pair, s)
    # (a) => ...
    s = re.sub(r"\(([a-zA-Z_$][\w$]*)\)\s*=>", r"(\1: any) =>", s)
    # bare  a => ...   (preceded by ( , = : or start of line)
    s = re.sub(r"(?<=[(,=:\s])([a-zA-Z_$][\w$]*)\s*=>", r"(\1: any) =>", s)
    return s


def port_this(s: str) -> str:
    s = s.replace("this.setState", "set")
    # The raw handle is nullable; route every clear through the guarded helper.
    s = s.replace("clearInterval(this.recTimer)", "stopRec()")
    s = s.replace("this.recTimer && clearInterval(this.recTimer)", "stopRec()")
    s = s.replace("this.recTimer", "recTimer.current")
    s = s.replace("this.timers.push", "track")
    s = s.replace("this.state", "state").replace("this.props", "props")
    s = s.replace("this.go(", "go(").replace("this.set(", "set(")
    for n in METHODS + ["seg", "segSm"]:
        s = s.replace(f"this.{n}(", f"{n}(")
    return annotate_arrows(s)


def write_hook(js: str) -> None:
    a, b = brace_span(js, js.index("{", js.index("state = {")))
    init = re.sub(r"this\.props\.(\w+)\s*\|\|\s*", r"props.\1 ?? ", js[a:b])

    a, b = brace_span(js, js.index("{", js.index("renderVals()")))
    rv = port_this(js[a + 1:b - 1])

    bodies = []
    for name in METHODS:
        m = re.search(rf"\n  {re.escape(name)}\(([^)]*)\)\s*\{{", js)
        a2, b2 = brace_span(js, js.index("{", m.end() - 1))
        typed = ", ".join(f"{x.strip()}: any" for x in m.group(1).split(",") if x.strip())
        bodies.append(f"  const {name} = ({typed}) => {{{js[a2 + 1:b2 - 1]}}}")
    methods = port_this("\n\n".join(bodies))

    seg = (
        '  const seg = (a: boolean) =>\n'
        '    "padding:8px 12px;border:none;border-radius:8px;cursor:pointer;white-space:nowrap;'
        "font:500 12.5px/1.65 'IBM Plex Sans','Noto Sans Myanmar',sans-serif;transition:.15s;\" +\n"
        '    (a ? "background:linear-gradient(135deg,#ffc36b,#ff8f3d);color:#1a1103"'
        ' : "background:transparent;color:#8b95b8")\n\n'
        '  const segSm = (a: boolean) =>\n'
        '    "padding:6px 10px;border:none;border-radius:7px;cursor:pointer;white-space:nowrap;'
        "font:500 11.5px/1.65 'IBM Plex Sans','Noto Sans Myanmar',sans-serif;transition:.15s;\" +\n"
        '    (a ? "background:rgba(150,180,255,.22);color:#e9efff"'
        ' : "background:transparent;color:#7d88ad")\n'
    )

    (SRC / "mock/useLuminaMock.ts").write_text(f'''// @ts-nocheck
/* eslint-disable @typescript-eslint/no-explicit-any */
/**
 * Design-prototype behaviour, as a hook.
 *
 * {BANNER}
 *
 * Fake scenes, fake credits, fake render progress — the mock the design was drawn against.
 * It lets the UI be checked against the design before anything is wired to the API, and it
 * is NOT the product's data shape: the real storyboard comes from the backend Plan/Scene
 * model. The prototype's class component becomes a hook here; its derived bindings are
 * carried over unchanged so the rendered output matches the design.
 *
 * `@ts-nocheck` is deliberate and scoped to this one generated file. It is a port of design
 * tool JavaScript — optional arguments, dynamically indexed literals — and type-checking it
 * buys nothing: nothing imports its internal types, and its correctness is established by
 * comparing the rendered result against the design. PrototypeShell.tsx, which is where a
 * missing binding would actually break, stays fully type-checked under the app's strict
 * settings, as does every hand-written file.
 */
import {{ useCallback, useEffect, useRef, useState }} from 'react'

import {{ LINES, MY, SCREENS, TITLES }} from './data'

export type MockProps = {{
  screen?: string
  startVariant?: string
  cardVariant?: string
  costVariant?: string
  backdrop?: string
  lang?: string
}}

export function useLuminaMock(props: MockProps = {{}}) {{
  const [state, setState] = useState<any>(() => ({init}))

  const timers = useRef<ReturnType<typeof setTimeout>[]>([])
  const recTimer = useRef<ReturnType<typeof setInterval> | null>(null)

  const track = (t: ReturnType<typeof setTimeout>) => {{
    timers.current.push(t)
    return t
  }}

  const stopRec = () => {{
    if (recTimer.current !== null) {{
      clearInterval(recTimer.current)
      recTimer.current = null
    }}
  }}

  /** Mirrors React class setState: an updater returning null means "no change". */
  const set = useCallback((patch: any) => {{
    setState((s: any) => {{
      const next = typeof patch === 'function' ? patch(s) : patch
      return next ? {{ ...s, ...next }} : s
    }})
  }}, [])

  const go = useCallback((screen: string) => set({{ screen }}), [set])

  // The prototype reveals scene 5's media after a beat, to show the loading state.
  useEffect(() => {{
    const running = timers
    running.current.push(
      setTimeout(
        () => set((s: any) => ({{
          scenes: s.scenes.map((x: any) => (x.id === 4 ? {{ ...x, loaded: true }} : x)),
        }})),
        2600,
      ),
    )
    return () => {{
      running.current.forEach(clearTimeout)
      stopRec()
    }}
  }}, [set])

{methods}

{seg}
{rv}
}}

export {{ LINES, MY, SCREENS, TITLES }}
''')


SLOT_RE = re.compile(
    r'<div style="display:none">\s*<div id="lum-content">(.*)</div>\s*</div>\s*$', re.S
)


def split_chrome_content(body: str) -> tuple[str, str]:
    """Separate the app chrome from the screen bodies.

    The prototype renders every screen body inside a `display:none` container and then
    physically moves that node into `#lum-slot-desktop` / `#lum-slot-phone` from JavaScript.
    Its own markup calls this "portaled into the active shell", which is exactly what it is,
    so the port renders the bodies through a real React portal rather than reproducing the
    DOM surgery — which React would fight on every re-render.
    """
    m = SLOT_RE.search(body.rstrip())
    if not m:
        raise SystemExit("could not find the #lum-content slot wrapper in the template")
    return body[: m.start()], m.group(1)


def write_shell(body: str) -> None:
    chrome_html, content_html = split_chrome_content(body)
    # Per-component binding sets: each destructures only what it actually reads, so an
    # unused binding is a compile error rather than dead weight.
    chrome_roots = binding_roots(chrome_html)
    content_roots = binding_roots(content_html)
    roots = sorted(set(chrome_roots) | set(content_roots))
    chrome_destructure = "\n".join(f"    {r}," for r in chrome_roots)
    content_destructure = "\n".join(f"    {r}," for r in content_roots)
    (SRC / "mock/PrototypeShell.tsx").write_text(f'''/* eslint-disable @typescript-eslint/no-explicit-any */
/**
 * The design prototype's markup, as JSX.
 *
 * {BANNER}
 *
 * Every CSS string here is byte-identical to the design export. That is the point: it makes
 * "does this match the design" a property of the converter rather than of a thousand hand
 * transcriptions. Re-run the script instead of editing this file.
 *
 * Scoping note: `s` is both the shared style table and the scene loop variable, as in the
 * prototype. The emitted `.map((s: any) => ...)` parameter shadows the destructured binding
 * inside the storyboard, reproducing the prototype's own template scoping.
 */
import React from 'react'
import {{ createPortal }} from 'react-dom'

import {{ hv, sx }} from '../lib/sx'
import logoUrl from '../assets/lumina-disc.png'

/** Top bar, variant switcher, and the device shell with its (empty) content slots. */
function PrototypeChrome({{ v }}: {{ v: any }}) {{
  const {{
{chrome_destructure}
  }} = v
  return (
    <>
{convert(chrome_html)}
    </>
  )
}}

/** The screen bodies, portaled into whichever slot the active device exposes. */
function PrototypeContent({{ v }}: {{ v: any }}) {{
  const {{
{content_destructure}
  }} = v
  return (
    <>
{convert(content_html)}
    </>
  )
}}

export function PrototypeShell({{ v }}: {{ v: any }}) {{
  const [slot, setSlot] = React.useState<HTMLElement | null>(null)

  // The chrome has to be in the DOM before its slot can be found, so this runs after
  // layout, and again whenever the device switches between the phone and desktop shells.
  React.useLayoutEffect(() => {{
    setSlot(document.getElementById(v.isPhone ? 'lum-slot-phone' : 'lum-slot-desktop'))
  }}, [v.isPhone])

  return (
    <>
      <PrototypeChrome v={{v}} />
      {{slot ? createPortal(<PrototypeContent v={{v}} />, slot) : null}}
    </>
  )
}}
''')
    print(f"  PrototypeShell.tsx  ({len(chrome_roots)} chrome + "
          f"{len(content_roots)} content bindings, {len(roots)} distinct)")


def main() -> None:
    src = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parents[1] / "Lumina-Web.html"
    print(f"reading {src}")
    template, logo = unpack(src)

    if logo:
        (SRC / "assets").mkdir(parents=True, exist_ok=True)
        (SRC / "assets/lumina-disc.png").write_bytes(logo)
        print(f"  lumina-disc.png     ({len(logo)} bytes)")

    body = template[template.index("</helmet>") + 9: template.rindex("</x-dc>")]
    if "<script" in body:  # the logic block sits outside <x-dc> in some exports
        body = body[: body.index("<script")]
    js = re.findall(r"<script[^>]*>(.*?)</script>", template, re.S)[1]

    (SRC / "mock").mkdir(parents=True, exist_ok=True)
    write_data(js)
    print("  data.ts")
    write_hook(js)
    print("  useLuminaMock.ts")
    write_shell(body)
    check_landmarks()
    tagged = {name: sum(c for g, (n, _) in LANDMARKS.items() if n == name
                        for c in [_seen.get(g, 0)])
              for _, (name, _) in LANDMARKS.items()}
    print("  landmarks: " + ", ".join(f"{k}x{v}" for k, v in sorted(tagged.items())))
    print("done")


if __name__ == "__main__":
    main()
