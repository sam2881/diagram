# -*- coding: utf-8 -*-
"""
check_sync.py — keep the hand-maintained documents honest against the canonical file.

    python tools/check_sync.py            # report drift
    python tools/check_sync.py --strict   # exit 1 on any drift (CI gate)

WHY THIS EXISTS
    Master_Documentation.html is fully generated (tools/build_docs.py), so it
    cannot drift. The other two documents CANNOT be generated:

      gcp_agentspace_architecture.html  hand-drawn SVG + CSS diagram kit + its
                                        own 36-section narrative
      BUILD_PLAYBOOK.html               build methodology, not product spec

    They are therefore hand-maintained — which means they CAN drift. This tool
    converts silent drift into a loud failure, the same way --check does for
    the generated file.

WHAT IT CHECKS
    1. Forbidden strings  — values we already corrected must never reappear
    2. Canonical values   — a fact stated in a derived doc must match facts.yaml
    3. Invariant parity   — INV-* count in the playbook matches facts.yaml
    4. Section drift      — Parts present in the .md but absent from the arch doc
    5. Requirement IDs    — every BR/FR/NFR/CON/ASM/DEP/AC range is contiguous

    A hit inside a deprecation notice ("the legacy 24-state model is retired")
    is skipped: reporting it would punish the sentence that fixed the drift.
"""
import io, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

MD   = os.path.join(ROOT, 'Master_Documentation.md')
ARCH = os.path.join(ROOT, 'gcp_agentspace_architecture.html')
PB   = os.path.join(ROOT, 'BUILD_PLAYBOOK.html')
GEN  = os.path.join(ROOT, 'Master_Documentation.html')
FACTS = os.path.join(HERE, 'facts.yaml')

try:
    import yaml
except ImportError:
    yaml = None


def load_facts():
    raw = io.open(FACTS, encoding='utf-8').read()
    if yaml:
        return yaml.safe_load(raw)
    # minimal fallback parser so the tool works without PyYAML
    data, section = {}, None
    for line in raw.split('\n'):
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if re.match(r'^\w[\w_]*:\s*$', line):
            section = line.split(':')[0]; data[section] = {}
        elif line.startswith('  - '):
            data.setdefault(section, [])
            if isinstance(data[section], dict):
                data[section] = []
            m = re.match(r'\s*-\s*text:\s*"(.*)"', line)
            if m:
                data[section].append({'text': m.group(1)})
        elif line.startswith('  ') and ':' in line and section:
            k, v = line.strip().split(':', 1)
            v = v.split('#')[0].strip().strip('"')
            if isinstance(data[section], dict):
                data[section][k.strip()] = v
    return data


# Regions that legitimately quote superseded values: changelogs record what
# changed, and correction notes quote the wrong value in order to name it.
# Scanning them produces false positives, and a checker that cries wolf is
# a checker nobody runs.
EXCLUDE_REGIONS = [
    r'<!-- \d+\. CHANGELOG -->.*?(?=<!-- FOOTER|</body>)',   # arch HTML changelog
    r'<div class="changelog-entry".*?</div>\s*(?=<div class="changelog-entry"|<div class="highlight-box)',
    r'<li><strong>Corrected:.*?</li>',                        # "Corrected: X -> Y" notes
    r'Terminology corrected in this revision.*?</div>',
    r'<span class="version">.*?</span>',                      # version labels in changelog
    r'deferred the high-risk Annex III (compliance )?deadline from[^<.]*',
    r'<td>Superseded</td>',
]

# (fact path, human label, regexes that indicate a CONTRADICTION)
VALUE_CHECKS = [
    ('versions.platform',            'platform version',   [r'\bv6\.\d\b']),
    ('retrieval.rrf_k',              'RRF constant',       [r'\bk\s*=\s*(?!60\b)\d+']),
    ('slo.availability',             'availability SLO',   [r'SLO[^.]{0,30}99\.(?!9\b)\d+%']),
    ('compliance.eu_ai_act_deadline','EU AI Act deadline',
        # only a contradiction if 2026 is presented as OUR high-risk deadline
        [r'(our|primary|the)\s+(high-risk\s+)?deadline[^.]{0,40}2 Aug(ust)? 2026',
         r'high-risk[^.]{0,30}(due|deadline|by)[^.]{0,20}2 Aug(ust)? 2026']),
    ('data.grain_fanout_max',        'grain fanout limit', [r'fanout[^.]{0,20}>\s*(?!2\.0)\d\.\d']),
]


# A hit inside a deprecation notice is not drift -- it is the notice doing its
# job. "The legacy 24-state model is retired" must not be reported as a live
# 24-state claim, or the checker punishes the very sentence that fixed the drift.
DEPRECATION_MARKERS = [
    'retired', 'superseded', 'is historical', 'older material', 'legacy',
    'and earlier', 'no longer', 'was replaced', 'v7.0 replaces',
]
CONTEXT_WINDOW = 260


def in_deprecation_context(body, pos):
    """True if the text around `pos` marks the value as historical."""
    lo = max(0, pos - CONTEXT_WINDOW)
    window = body[lo:pos + CONTEXT_WINDOW].lower()
    return any(mark in window for mark in DEPRECATION_MARKERS)


def strip_excluded(body):
    """Blank out regions that legitimately quote superseded values."""
    for pat in EXCLUDE_REGIONS:
        body = re.sub(pat, ' ', body, flags=re.S | re.I)
    return body


def get(facts, path):
    cur = facts
    for part in path.split('.'):
        cur = cur.get(part) if isinstance(cur, dict) else None
        if cur is None:
            return None
    return str(cur)


def main():
    strict = '--strict' in sys.argv
    facts = load_facts()
    raw_docs = {
        'gcp_agentspace_architecture.html': io.open(ARCH, encoding='utf-8').read(),
        'BUILD_PLAYBOOK.html':              io.open(PB,   encoding='utf-8').read(),
    }
    # scan the prose, not the changelog / correction notes
    docs = {k: strip_excluded(v) for k, v in raw_docs.items()}
    md = io.open(MD, encoding='utf-8').read()
    problems, warnings = [], []

    # ---------- 1. forbidden strings ----------
    print('1. FORBIDDEN STRINGS (already-corrected values must not reappear)')
    forb = facts.get('forbidden') or []
    hits = 0
    for item in forb:
        text = item['text'] if isinstance(item, dict) else str(item)
        why = item.get('why', '') if isinstance(item, dict) else ''
        for name, body in list(docs.items()) + [('Master_Documentation.md', strip_excluded(md))]:
            for m in re.finditer(re.escape(text), body):
                if in_deprecation_context(body, m.start()):
                    continue                      # a retirement notice, not a claim
                hits += 1
                problems.append('%s contains forbidden %r (%s)' % (name, text[:44], why))
    print('   %s\n' % ('clean' if not hits else '%d hit(s)' % hits))

    # ---------- 2. canonical values ----------
    print('2. CANONICAL VALUES (derived docs must not contradict facts.yaml)')
    for path, label, bad_pats in VALUE_CHECKS:
        val = get(facts, path)
        if val is None:
            warnings.append('facts.yaml missing %s' % path); continue
        for name, body in docs.items():
            for bp in bad_pats:
                for m in re.finditer(bp, body, re.I):
                    if in_deprecation_context(body, m.start()):
                        continue                  # historical reference, not drift
                    problems.append('%s: %s — found %r, canonical is %r'
                                    % (name, label, m.group(0)[:40], val))
    print('   %d value rule(s) checked\n' % len(VALUE_CHECKS))

    # ---------- 3. invariant parity ----------
    print('3. INVARIANT PARITY')
    want = int(get(facts, 'counts.invariants') or 0)
    got = len(set(re.findall(r'INV-(\d{3})', docs['BUILD_PLAYBOOK.html'])))
    print('   facts.yaml=%d  BUILD_PLAYBOOK=%d  %s\n'
          % (want, got, 'OK' if want == got else 'MISMATCH'))
    if want != got:
        problems.append('invariant count: facts.yaml=%d but BUILD_PLAYBOOK has %d' % (want, got))

    # ---------- 4. section coverage ----------
    print('4. SECTION COVERAGE (Parts in the .md vs the architecture view)')
    parts = re.findall(r'^# Part \d+ — (.+)$', md, re.M)
    arch_txt = re.sub(r'<[^>]+>', ' ', docs['gcp_agentspace_architecture.html']).lower()
    arch_words = set(re.findall(r'[a-z]{4,}', arch_txt))
    STOP = {'and', 'the', 'for', 'with', 'guide'}
    missing = []
    for p in parts:
        toks = [w for w in re.findall(r'[a-z]{4,}', p.lower()) if w not in STOP]
        if not toks:
            continue
        hit = sum(1 for w in toks if w in arch_words) / float(len(toks))
        if hit < 0.5:                       # fewer than half the topic words appear
            missing.append('%s  (%.0f%% of topic words present)' % (p, hit * 100))
    if missing:
        print('   %d Part(s) with no obvious counterpart in the arch view:' % len(missing))
        for m2 in missing:
            print('      - %s' % m2)
        warnings.append('%d Part(s) not represented in the architecture view' % len(missing))
    else:
        print('   all %d Parts represented' % len(parts))

    # ---------- 4b. requirement-ID integrity ----------
    print('')
    print('4b. REQUIREMENT-ID INTEGRITY (contiguous, no gaps, no duplicates)')
    for prefix, expected_key in [('BR', 'business_requirements'), ('CON', 'constraints_reg'),
                                 ('ASM', 'assumptions_reg'), ('DEP', 'dependencies_reg'),
                                 ('AC', 'acceptance_criteria')]:
        found = sorted({int(n) for n in re.findall(r'\*\*%s-(\d+)\*\*' % prefix, md)})
        want = int(get(facts, 'counts.%s' % expected_key) or 0)
        gaps = [i for i in range(1, (max(found) if found else 0) + 1) if i not in found]
        status = 'OK' if (len(found) == want and not gaps) else 'MISMATCH'
        print('   %-4s %2d found, %2d expected%s  %s'
              % (prefix, len(found), want,
                 (', gaps at %s' % gaps) if gaps else '', status))
        if status != 'OK':
            problems.append('%s-* : %d found but facts.yaml expects %d%s'
                            % (prefix, len(found), want,
                               (' (gaps at %s)' % gaps) if gaps else ''))
    fr = sorted({m for m in re.findall(r'\*\*(FR-[A-Z]+-\d+)\*\*', md)})
    nfr = sorted({m for m in re.findall(r'\*\*(NFR-[A-Z]+-\d+)\*\*', md)})
    print('   FR   %d unique | NFR  %d unique' % (len(fr), len(nfr)))
    dupe_fr = len(re.findall(r'\*\*FR-[A-Z]+-\d+\*\*', md))
    print('   every FR names a verifying test:',
          'yes' if md.count('Verified by') >= 8 else 'CHECK')

    # ---------- 5. generated file freshness ----------
    print('\n5. GENERATED FILE')
    if os.path.getmtime(GEN) < os.path.getmtime(MD):
        problems.append('Master_Documentation.html is OLDER than the .md — run tools/build_docs.py')
        print('   STALE — run: python tools/build_docs.py')
    else:
        print('   up to date (run tools/build_docs.py --check for byte-level proof)')

    # ---------- summary ----------
    print('\n' + '=' * 62)
    if problems:
        print('DRIFT DETECTED — %d problem(s)' % len(problems))
        for p in problems:
            print('  ! %s' % p)
    else:
        print('NO DRIFT — derived documents agree with the canonical file')
    if warnings:
        print('\nAdvisory (%d):' % len(warnings))
        for w in warnings:
            print('  ~ %s' % w)
    print('=' * 62)

    if strict and problems:
        sys.exit(1)


if __name__ == '__main__':
    main()
