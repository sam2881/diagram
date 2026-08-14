# -*- coding: utf-8 -*-
"""One-off: extract the page shell from the existing Master_Documentation.html
into tools/template.html so the generator no longer depends on the old file.

Run once. After this, build_docs.py is the only thing that writes the HTML.
"""
import io, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, 'Master_Documentation.html')
OUT = os.path.join(HERE, 'template.html')
FIG = os.path.join(HERE, 'figure_template.html')

s = io.open(SRC, encoding='utf-8').read()

# --- head + topbar + shell open, up to the nav content ---
i_nav = s.index('<nav class="sidebar"')
i_nav_open_end = s.index('>', i_nav) + 1
head = s[:i_nav_open_end]

# --- between nav close and content start ---
i_nav_close = s.index('</nav>')
i_article = s.index('<article class="content">') + len('<article class="content">')
mid = s[i_nav_close:i_article]

# --- tail: from the end of the last section to EOF ---
i_last = s.rindex('</section>') + len('</section>')
tail = s[i_last:]

template = head + '\n{{NAV}}\n' + mid + '\n{{CONTENT}}\n' + tail
io.open(OUT, 'w', encoding='utf-8').write(template)

# --- capture one complete mermaid <figure> to use as the diagram template ---
m = re.search(r'<figure class="diagram" id="fig-1".*?</figure>', s, re.S)
if not m:
    print('WARNING: no mermaid figure found; diagrams will use a minimal wrapper')
    fig = ''
else:
    fig = m.group(0)
    # blank out the instance-specific bits so build_docs can fill them
    fig = re.sub(r'id="fig-1"', 'id="fig-{{N}}"', fig)
    fig = re.sub(r'data-fig="1"', 'data-fig="{{N}}"', fig)
    fig = re.sub(r'>Figure 1<', '>Figure {{N}}<', fig)
    fig = re.sub(r'(<pre class="mermaid"[^>]*>).*?(</pre>)', r'\1{{SRC}}\2', fig, flags=re.S)
    fig = re.sub(r'(<span class="dia-tag">)[^<]*(</span>)', r'\1{{TAG}}\2', fig)
io.open(FIG, 'w', encoding='utf-8').write(fig)

print('template.html       %7d bytes  (head %d + mid %d + tail %d)' %
      (len(template), len(head), len(mid), len(tail)))
print('figure_template.html %6d bytes' % len(fig))
print('placeholders:', '{{NAV}}' in template, '{{CONTENT}}' in template,
      '{{SRC}}' in fig, '{{N}}' in fig)
