"""Scan all processor classes for _hook, _order, _singleton_group, slot r/w."""
import os, re

proc_dir = 'harnessx/processors'
results = []

for root, dirs, files in os.walk(proc_dir):
    for f in files:
        if not f.endswith('.py') or f.startswith('_'):
            continue
        path = os.path.join(root, f)
        try:
            with open(path, encoding='utf-8') as fh:
                content = fh.read()
        except Exception:
            continue
        classes = re.findall(r'class\s+(\w*(?:Processor|Policy|Provider)\w*)\s*', content)
        for cls_name in classes:
            hook_m = re.search(r'_hook\s*=\s*"([^"]*)"', content) or re.search(r"_hook\s*=\s*'([^']*)'", content)
            order_m = re.search(r'_order\s*=\s*(\d+)', content)
            sg_m = re.search(r'_singleton_group\s*=\s*"([^"]*)"', content) or re.search(r"_singleton_group\s*=\s*'([^']*)'", content)
            after_m = re.search(r'_after\s*=\s*\[(.*?)\]', content)
            writes = re.findall(r'set_slot\([^,]*,\s*"(\w+)"', content)
            reads = re.findall(r'get_slot\([^,]*,\s*"(\w+)"', content)

            module = os.path.relpath(path, '.').replace(os.sep, '.')[:-3]
            target = f'{module}.{cls_name}'
            hook = hook_m.group(1) if hook_m else ''
            order = int(order_m.group(1)) if order_m else None
            sg = sg_m.group(1) if sg_m else ''
            after_raw = after_m.group(1) if after_m else ''
            after = [x.strip().strip('"').strip("'") for x in after_raw.split(',') if x.strip()]
            w = list(set(writes))
            r = list(set(reads))
            results.append((target, hook, order, sg, after, w, r))

has_meta = [x for x in results if x[3] or x[4] or x[5] or x[6]]
no_meta = [x for x in results if not (x[3] or x[4] or x[5] or x[6])]

print(f'Scanned: {len(results)} processor classes')
print(f'With metadata (sg/after/writes/reads): {len(has_meta)}')
print(f'No metadata: {len(no_meta)}')
print()

for t, h, o, sg, after, w, r in sorted(has_meta, key=lambda x: len(x[3])+len(x[4])+len(x[5])+len(x[6]), reverse=True):
    name = t.rsplit('.', 1)[-1]
    parts = []
    if h: parts.append(f'hook={h}')
    if o is not None: parts.append(f'order={o}')
    if sg: parts.append(f'sg={sg}')
    if after: parts.append(f'after={after}')
    if w: parts.append(f'writes={w}')
    if r: parts.append(f'reads={r}')
    print(f'  HAS: {name}: {" | ".join(parts)}')

print()
print('No metadata:')
for t, h, o, sg, after, w, r in no_meta:
    print(f'  EMPTY: {t.rsplit(".",1)[-1]}')
