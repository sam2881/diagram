# -*- coding: utf-8 -*-
"""
build_docs.py — regenerate Master_Documentation.html from Master_Documentation.md

    python tools/build_docs.py             # generate (renderer embedded)
    python tools/build_docs.py --no-embed  # generate, load the renderer externally
    python tools/build_docs.py --check     # verify the committed HTML matches (CI gate)

Master_Documentation.md is the ONLY source of truth.
Master_Documentation.html is a build artefact and must never be hand-edited.

By default the Mermaid renderer is embedded in the page. Without it the HTML has
to fetch mermaid.min.js from a sibling file or from jsdelivr, so a copy that has
been emailed, downloaded or opened from SharePoint behind a proxy shows diagram
*source* instead of diagrams and its zoom buttons go dead. Embedding costs ~3.3 MB
and buys a file that renders anywhere. --check must be passed the same flag the
committed artefact was built with.
"""
import io, os, re, sys, html, hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MD = os.path.join(ROOT, 'Master_Documentation.md')
OUT = os.path.join(ROOT, 'Master_Documentation.html')
TPL = os.path.join(HERE, 'template.html')
FIGTPL = os.path.join(HERE, 'figure_template.html')
MERMAID = os.path.join(HERE, 'mermaid.min.js')

EMBED_SLOT = '<!--MERMAID_EMBED-->'

try:
    import markdown
except ImportError:
    sys.exit('ERROR: pip install markdown')

ANCHOR = '<a class="anchor" href="#%s" aria-label="Link to this section">#</a>'

# GitHub alert type -> (visible title, CSS modifier defined in template.html).
# The stylesheet defines callout-note / -tip / -warning / -important / -success only,
# so CAUTION maps onto the warning styling rather than inventing an unstyled class.
ALERTS = {'NOTE':      ('Note',      'note'),
          'TIP':       ('Tip',       'tip'),
          'IMPORTANT': ('Important', 'important'),
          'WARNING':   ('Warning',   'warning'),
          'CAUTION':   ('Caution',   'warning')}

# Mermaid diagram-type -> the label shown in the figure toolbar
DIA_TAG = [('sequenceDiagram', 'Sequence'), ('stateDiagram', 'State'),
           ('erDiagram', 'ER'), ('classDiagram', 'Class'),
           ('mindmap', 'Mind map'), ('gantt', 'Gantt'),
           ('journey', 'Journey'), ('flowchart', 'Flow'), ('graph', 'Flow')]


def slugify(text):
    """GitHub-compatible heading slug."""
    t = re.sub(r'`([^`]*)`', r'\1', text)
    t = re.sub(r'\*\*|\*|__|_', '', t)
    t = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', t)   # [label](url) -> label
    t = t.lower()
    t = re.sub(r'[^\w\s-]', '', t)
    return t.replace(' ', '-')


def protect_mermaid(md_text):
    """Pull mermaid fences out before markdown conversion; return text + blocks."""
    blocks = []

    def grab(m):
        blocks.append(m.group(1))
        return '\n\nMERMAIDBLOCK%dENDMERMAID\n\n' % (len(blocks) - 1)

    md_text = re.sub(r'```mermaid\n(.*?)```', grab, md_text, flags=re.S)
    return md_text, blocks


def render_alerts(md_text):
    """Convert GitHub alerts (> [!NOTE] ...) into fenced div blocks markdown can keep."""
    out, i, lines = [], 0, md_text.split('\n')
    while i < len(lines):
        m = re.match(r'^>\s*\[!(' + '|'.join(ALERTS) + r')\]\s*$', lines[i])
        if not m:
            out.append(lines[i]); i += 1; continue
        kind = m.group(1)
        i += 1
        body = []
        while i < len(lines) and lines[i].startswith('>'):
            body.append(re.sub(r'^>\s?', '', lines[i])); i += 1
        out.append('<!--ADM:%s-->' % kind)
        out.append('')
        out.extend(body)
        out.append('')
        out.append('<!--/ADM-->')
        out.append('')
    return '\n'.join(out)


def wrap_alerts(html_text):
    """Turn the ADM markers into the stylesheet's callout structure.

    Must match template.html exactly:
      <div class="callout callout-X"><div class="callout-title">T</div>
      <div class="callout-body">...</div></div>
    """
    def open_tag(m):
        title, mod = ALERTS[m.group(1).upper()]
        return ('<div class="callout callout-%s"><div class="callout-title">%s</div>'
                '<div class="callout-body">' % (mod, title))
    html_text = re.sub(r'<p><!--ADM:(\w+)--></p>', open_tag, html_text)
    html_text = re.sub(r'<!--ADM:(\w+)-->', open_tag, html_text)
    html_text = re.sub(r'<p><!--/ADM--></p>', '</div></div>', html_text)
    html_text = html_text.replace('<!--/ADM-->', '</div></div>')
    return html_text


def add_heading_ids(html_text):
    """Add id + class + anchor to every h1..h4, and collect the nav entries."""
    nav, seen = [], {}

    def fix(m):
        lvl, inner = int(m.group(1)), m.group(2)
        plain = re.sub(r'<[^>]+>', '', inner)
        sid = slugify(plain)
        if sid in seen:
            seen[sid] += 1
            sid = '%s-%d' % (sid, seen[sid])
        else:
            seen[sid] = 0
        if lvl == 1:
            nav.append(('part', sid, plain))
        elif lvl == 2:
            nav.append(('sec', sid, plain))
        return '<h%d id="%s" class="hd hd%d">%s%s</h%d>' % (lvl, sid, lvl, inner, ANCHOR % sid, lvl)

    html_text = re.sub(r'<h([1-4])>(.*?)</h\1>', fix, html_text, flags=re.S)
    return html_text, nav


def wrap_tables(html_text):
    return re.sub(r'<table>', '<div class="table-wrap"><table>', html_text) \
             .replace('</table>', '</table></div>')


def restore_mermaid(html_text, blocks, figtpl):
    """Swap the placeholders back in as interactive <figure> diagrams."""
    def put(m):
        idx = int(m.group(1))
        src = blocks[idx]
        tag = 'Diagram'
        head = src.strip().split('\n')[0]
        for key, label in DIA_TAG:
            if head.startswith(key):
                tag = label; break
        if not figtpl:
            return '<figure class="diagram"><pre class="mermaid">%s</pre></figure>' % html.escape(src)
        out = figtpl.replace('{{N}}', str(idx + 1)) \
                    .replace('{{TAG}}', tag) \
                    .replace('{{SRC}}', html.escape(src))
        return out

    html_text = re.sub(r'<p>MERMAIDBLOCK(\d+)ENDMERMAID</p>', put, html_text)
    html_text = re.sub(r'MERMAIDBLOCK(\d+)ENDMERMAID', put, html_text)
    return html_text


def sectionise(html_text):
    """Wrap each <h1> and the content that follows it in <section class="part">."""
    parts = re.split(r'(?=<h1 id=")', html_text)
    out = []
    for chunk in parts:
        if not chunk.strip():
            continue
        m = re.match(r'<h1 id="([^"]+)"', chunk)
        if m:
            out.append('<section class="part" id="sec-%s">\n%s\n</section>' % (m.group(1), chunk.strip()))
        else:
            out.append(chunk)
    return '\n'.join(out)


def build_nav(nav):
    rows = []
    for kind, sid, text in nav:
        rows.append('<a class="nav-%s" href="#%s" data-target="%s">%s</a>'
                    % (kind, sid, sid, html.escape(text)))
    return '\n'.join(rows)


def mermaid_embed():
    """Return a <script> block carrying the whole Mermaid bundle, or '' if absent.

    The bundle is inserted verbatim -- no escaping. That is only safe because the
    HTML tokenizer ends a <script> element on '</script', and can be tricked into
    *not* ending it by an inner '<script'. Neither appears in the vendored
    mermaid@10 bundle, so we assert rather than mangle minified JS: a future
    bundle that does contain them must be handled deliberately, not silently
    corrupted.
    """
    if not os.path.exists(MERMAID):
        sys.stderr.write('WARNING: %s not found - building without an embedded '
                         'renderer.\n         The HTML will need a sibling '
                         'mermaid.min.js or the CDN.\n' % MERMAID)
        return ''

    js = io.open(MERMAID, encoding='utf-8').read()
    lowered = js.lower()
    for bad in ('</script', '<script'):
        if bad in lowered:
            sys.exit("ERROR: tools/mermaid.min.js contains %r, which cannot be "
                     "inlined safely.\n       Rebuild with --no-embed, or add "
                     "explicit escaping to mermaid_embed()." % bad)

    return ('<!-- Mermaid renderer, embedded so this file renders standalone. '
            'Source: tools/mermaid.min.js -->\n'
            '<script>\n%s\n</script>' % js.strip())


def build(embed=True):
    md_text = io.open(MD, encoding='utf-8').read()
    template = io.open(TPL, encoding='utf-8').read()
    figtpl = io.open(FIGTPL, encoding='utf-8').read() if os.path.exists(FIGTPL) else ''

    md_text, blocks = protect_mermaid(md_text)
    md_text = render_alerts(md_text)

    body = markdown.markdown(
        md_text,
        extensions=['tables', 'fenced_code', 'sane_lists', 'attr_list', 'md_in_html'],
        output_format='html5',
    )

    body = wrap_alerts(body)
    body, nav = add_heading_ids(body)
    body = wrap_tables(body)
    body = restore_mermaid(body, blocks, figtpl)
    body = sectionise(body)

    if template.count(EMBED_SLOT) != 1:
        sys.exit('ERROR: tools/template.html must contain exactly one %s marker '
                 '(found %d).' % (EMBED_SLOT, template.count(EMBED_SLOT)))
    if EMBED_SLOT in body:
        sys.exit('ERROR: Master_Documentation.md produced a literal %s, which '
                 'collides with the renderer slot.' % EMBED_SLOT)

    page = template.replace('{{NAV}}', build_nav(nav)).replace('{{CONTENT}}', body)
    banner = ('\n<!-- ============================================================\n'
              '     GENERATED FILE - DO NOT EDIT\n'
              '     Source : Master_Documentation.md\n'
              '     Build  : python tools/build_docs.py\n'
              '     Any manual edit will be overwritten on the next build.\n'
              '     ============================================================ -->\n')
    page = page.replace('<head>', '<head>' + banner, 1)

    # Last, so the renderer's minified source is never scanned for {{...}} slots.
    embed_block = mermaid_embed() if embed else ''
    page = page.replace(EMBED_SLOT, embed_block, 1)
    return page, len(blocks), len(nav), len(embed_block)


def main():
    embed = '--no-embed' not in sys.argv
    page, n_dia, n_nav, n_embed = build(embed=embed)
    check = '--check' in sys.argv

    if check:
        if not os.path.exists(OUT):
            sys.exit('FAIL: %s does not exist' % OUT)
        current = io.open(OUT, encoding='utf-8').read()
        if current != page:
            a = hashlib.sha256(current.encode()).hexdigest()[:12]
            b = hashlib.sha256(page.encode()).hexdigest()[:12]
            sys.exit('FAIL: Master_Documentation.html is out of date or was hand-edited.\n'
                     '      committed=%s generated=%s\n'
                     '      Run: python tools/build_docs.py%s'
                     % (a, b, '' if embed else ' --no-embed'))
        print('OK: Master_Documentation.html matches Master_Documentation.md')
        return

    io.open(OUT, 'w', encoding='utf-8').write(page)
    print('Generated Master_Documentation.html')
    print('  %d bytes | %d diagrams | %d nav entries' % (len(page), n_dia, n_nav))
    if n_embed:
        print('  renderer embedded (%d bytes) — file renders standalone' % n_embed)
    else:
        print('  renderer NOT embedded — needs a sibling mermaid.min.js or the CDN')


if __name__ == '__main__':
    main()
