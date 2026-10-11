"""Deterministic vector timing facts; coordinates use PyMuPDF's page space."""
import argparse
import itertools
import json
import math
import re
import statistics
import sys

import fitz
from shapely.geometry import LineString, Point, Polygon, box


def distance(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def primitive(value):
    if isinstance(value, (fitz.Point, fitz.Rect)):
        return list(value)
    if isinstance(value, (tuple, list)):
        return [primitive(v) for v in value]
    return value


def traces(segments, eps):
    """Snap endpoints only: interior crossings must retain both bus traces."""
    nodes, edges = [], []
    for s in segments:
        pair = []
        for p in (s['p'], s['q']):
            near = next((i for i, n in enumerate(nodes) if distance(p, n) <= eps), None)
            if near is None:
                near = len(nodes)
                nodes.append(p)
            pair.append(near)
        edges.append(pair)
    unused, result = set(range(len(edges))), []
    while unused:
        component = {min(unused)}
        vertices = set(edges[min(unused)])
        while True:
            extra = {i for i in unused - component if vertices.intersection(edges[i])}
            if not extra:
                break
            component.update(extra)
            vertices.update(v for i in extra for v in edges[i])
        unused -= component
        pending, polylines, oriented = set(component), [], []
        while pending:
            degree = {v: sum(v in edges[i] for i in pending) for v in vertices}
            ends = [v for v in vertices if degree[v] == 1]
            current = min(ends or [v for v in vertices if degree[v]], key=lambda v: nodes[v])
            chain = [nodes[current]]
            while True:
                available = [i for i in pending if current in edges[i]]
                if not available:
                    break
                i = min(available)
                pending.remove(i)
                nxt = edges[i][1] if edges[i][0] == current else edges[i][0]
                oriented.append((segments[i], nodes[current], nodes[nxt]))
                chain.append(nodes[nxt])
                current = nxt
            polylines.append(chain)
        result.append((polylines, oriented))
    return result


def arrow_geometry(paths, segments, eps):
    heads, used, arrows = [], set(), []
    for path in paths:
        if path['fill'] is None or any(it[0] != 'l' for it in path['items']):
            continue
        vertices = []
        for it in path['items']:
            for p in it[1:]:
                if not any(distance(p, v) <= eps for v in vertices):
                    vertices.append(p)
        if len(vertices) != 3:
            continue
        for axis in (0, 1):
            other = 1 - axis
            for tip in vertices:
                base = [p for p in vertices if p != tip]
                mid = [(base[0][k] + base[1][k]) / 2 for k in (0, 1)]
                if abs(base[0][axis] - base[1][axis]) > eps or abs(mid[other] - tip[other]) > eps:
                    continue
                for s in segments:
                    if s['dashed'] or s['color'] != path['fill']:
                        continue
                    if abs(s['p'][other] - s['q'][other]) > eps:
                        continue
                    if min(distance(mid, p) for p in (s['p'], s['q'])) > 2 * eps:
                        continue
                    heads.append({'path': path['id'], 'tip': tip, 'base': base,
                                  'axis': axis, 'shaft': s['id']})
                    used.add(s['id'])
    # Opposed heads also cover interrupted shafts with a label in the gap.
    pending = set(range(len(heads)))
    for i in range(len(heads)):
        if i not in pending:
            continue
        h, matches = heads[i], []
        for j in pending - {i}:
            g, axis = heads[j], h['axis']
            if axis != g['axis'] or abs(h['tip'][1-axis] - g['tip'][1-axis]) > eps:
                continue
            if paths[h['path']]['fill'] != paths[g['path']]['fill']:
                continue
            dh = h['tip'][axis] - h['base'][0][axis]
            dg = g['tip'][axis] - g['base'][0][axis]
            if dh * dg < 0:
                sh = next(s for s in segments if s['id'] == h['shaft'])
                sg = next(s for s in segments if s['id'] == g['shaft'])
                joined = h['shaft'] == g['shaft']
                gap = abs(max(sh['p'][axis], sh['q'][axis]) - min(sg['p'][axis], sg['q'][axis]))
                matches.append((0 if joined else gap, j))
        partner = min(matches)[1] if matches else None
        group = [h] if partner is None else [h, heads[partner]]
        pending.difference_update([i] if partner is None else [i, partner])
        tips = sorted([g['tip'] for g in group], key=lambda p: p[h['axis']])
        arrows.append({'axis': h['axis'], 'endpoints': tips, 'heads': group,
                       'shaft_ids': sorted({g['shaft'] for g in group})})
    return arrows, used


def extract(pdf, page_index, bbox=None):
    with fitz.open(pdf) as doc:
        page = doc[page_index]
        bounds = list(page.rect) if bbox is None else list(bbox)
        clip = box(*bounds)
        paths, segments, fills = [], [], []
        for drawing in page.get_drawings():
            r = list(drawing['rect'])
            if r[2] < bounds[0] or r[0] > bounds[2] or r[3] < bounds[1] or r[1] > bounds[3]:
                continue
            path = {k: primitive(drawing.get(k)) for k in
                    ('items', 'type', 'width', 'color', 'fill', 'dashes', 'closePath')}
            path.update(id=len(paths), bbox=r)
            paths.append(path)
            polygon = []
            for it in path['items']:
                if it[0] == 're':
                    x0, y0, x1, y1 = it[1]
                    polygon = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
                elif it[0] == 'l':
                    polygon.extend(it[1:])
                if it[0] == 'l' and 's' in path['type']:
                    line = LineString(it[1:]).intersection(clip)
                    if line.geom_type == 'LineString' and not line.is_empty:
                        p, q = map(list, (line.coords[0], line.coords[-1]))
                        segments.append({'id': len(segments), 'path': path['id'], 'p': p, 'q': q,
                                         'width': path['width'], 'color': path['color'],
                                         'dashed': bool(re.search(r'[1-9]', (path['dashes'] or '').split(']')[0]))})
            if path['fill'] is not None and len(polygon) >= 3:
                shape = Polygon(polygon).buffer(0).intersection(clip)
                if not shape.is_empty:
                    fills.append((path['id'], shape, path['fill']))
        words = [{'text': w[4], 'bbox': list(w[:4])} for w in page.get_text('words')
                 if clip.covers(Point((w[0]+w[2])/2, (w[1]+w[3])/2))]
        images = [{k: primitive(info[k]) for k in ('bbox', 'width', 'height')}
                  for info in page.get_image_info() if box(*info['bbox']).intersection(clip).area > 0]
    diagnostics = []
    if not paths and not words:
        diagnostics.append('No vector paths or text in crop.' +
                           (' Embedded raster image content is unsupported.' if images else ''))
    eps = statistics.median(s['width'] for s in segments if s['width']) * 0.8 if segments else 0.5
    arrows, shaft_ids = arrow_geometry(paths, segments, eps)
    markers = [{'id': i, 'x': (s['p'][0]+s['q'][0])/2,
                'y_span': sorted([s['p'][1], s['q'][1]]), 'segment': s['id'], 'events': []}
               for i, s in enumerate(s for s in segments if s['dashed'] and abs(s['p'][0]-s['q'][0]) <= eps)]
    solids = [s for s in segments if not s['dashed'] and s['id'] not in shaft_ids
              and paths[s['path']]['type'] == 's']
    lanes = []
    for chains, oriented in traces(solids, eps):
        points = [p for chain in chains for p in chain]
        r = [min(p[0] for p in points), min(p[1] for p in points),
             max(p[0] for p in points), max(p[1] for p in points)]
        if r[2]-r[0] < (bounds[2]-bounds[0]) * 0.25 or r[3]-r[1] < 4*eps:
            continue
        if not any(abs(p[1]-q[1]) <= eps for _, p, q in oriented):
            continue
        match = next((l for l in lanes if abs(l['bbox'][1]-r[1]) <= 2*eps
                      and abs(l['bbox'][3]-r[3]) <= 2*eps
                      and min(l['bbox'][2], r[2])-max(l['bbox'][0], r[0]) > (r[2]-r[0])*0.5), None)
        if match is None:
            lanes.append({'bbox': r, 'polylines': chains, '_edges': oriented})
        else:
            match['polylines'].extend(chains)
            match['_edges'].extend(oriented)
            match['bbox'] = [min(match['bbox'][0], r[0]), min(match['bbox'][1], r[1]),
                             max(match['bbox'][2], r[2]), max(match['bbox'][3], r[3])]
    lanes.sort(key=lambda l: l['bbox'][1])
    for index, lane in enumerate(lanes):
        lane.update(index=index, transitions=[], highz_regions=[])
        slopes = [(s, p, q) for s, p, q in lane['_edges'] if abs(p[1]-q[1]) > eps]
        crossed = set()
        for (s, p, q), (t, a, b) in itertools.combinations(slopes, 2):
            if (q[1]-p[1])*(b[1]-a[1]) >= 0:
                continue
            cross = LineString([p, q]).intersection(LineString([a, b]))
            if cross.geom_type == 'Point' and not cross.is_empty:
                lane['transitions'].append({'x': cross.x, 'y': cross.y, 'kind': 'transition',
                                            'x_span': [cross.x, cross.x], 'segments': [s['id'], t['id']]})
                crossed.update([s['id'], t['id']])
        lane['bus'] = bool(crossed)
        for s, p, q in slopes:
            if s['id'] not in crossed:
                lane['transitions'].append({'x': (p[0]+q[0])/2, 'y': (p[1]+q[1])/2,
                                            'kind': 'rising' if q[1]<p[1] else 'falling',
                                            'x_span': sorted([p[0], q[0]]), 'segments': [s['id']]})
        for word in words:
            if word['text'].upper() != 'Z':
                continue
            r = word['bbox']; center = Point((r[0]+r[2])/2, (r[1]+r[3])/2)
            if not lane['bbox'][1]-eps <= center.y <= lane['bbox'][3]+eps:
                continue
            regions = [(pid, shape, color) for pid, shape, color in fills if shape.buffer(eps).covers(center)]
            if regions:
                selected = {regions[0][0]}; shape, color = regions[0][1:]
                while True:
                    near = [(pid, g) for pid, g, c in fills if pid not in selected and c == color
                            and shape.distance(g) <= eps]
                    if not near:
                        break
                    for pid, g in near:
                        selected.add(pid); shape = shape.union(g)
                span = [shape.bounds[0], shape.bounds[2]]
            else:
                flat = [(p, q) for _, p, q in lane['_edges'] if abs(p[1]-q[1]) <= eps
                        and LineString([p, q]).distance(center) <= 2*eps]
                if len(flat) != 1:
                    continue
                span = sorted([flat[0][0][0], flat[0][1][0]])
            lane['highz_regions'].append({'x_span': span, 'evidence': word})
        for event in lane['transitions']:
            for region in lane['highz_regions']:
                for side, kind in ((0, 'to_highz'), (1, 'from_highz')):
                    boundary = region['x_span'][side]
                    if min(abs(event['x']-boundary), abs(event['x_span'][1-side]-boundary)) <= 2*eps:
                        event['kind'] = kind
            event['levels'] = []
            edge = next(s for s in segments if s['id'] == event['segments'][0])
            for word in words:
                if not re.fullmatch(r'\d+(?:\.\d+)?%', word['text']):
                    continue
                r = word['bbox']; h = r[3]-r[1]
                for tick in solids:
                    if tick['id'] in {s['id'] for s, _, _ in lane['_edges']}:
                        continue
                    if abs(tick['p'][1]-tick['q'][1]) > eps:
                        continue
                    hit = LineString([tick['p'], tick['q']]).intersection(LineString([edge['p'], edge['q']]))
                    if hit.geom_type == 'Point' and not hit.is_empty and r[1]-eps <= hit.y <= r[3]+eps:
                        if max(r[0]-hit.x, hit.x-r[2], 0) <= 2*h:
                            event['levels'].append({'x': hit.x, 'y': hit.y,
                                                    'level_pct': float(word['text'][:-1]), 'word': word})
        lane['transitions'].sort(key=lambda e: e['x'])
        for marker in markers:
            for event in lane['transitions']:
                lo, hi = event['x_span']; x = marker['x']
                if not lo-2*eps <= x <= hi+2*eps:
                    continue
                edge = next(s for s in segments if s['id'] == event['segments'][0])
                p, q = edge['p'], edge['q']
                if abs(q[0]-p[0]) <= eps:
                    # Vertical steps can touch a marker at either endpoint.
                    low = max(min(p[1], q[1]), marker['y_span'][0])
                    high = min(max(p[1], q[1]), marker['y_span'][1])
                    if low > high + eps:
                        continue
                    y = (low + high) / 2
                else:
                    sx = max(min(p[0], q[0]), min(x, max(p[0], q[0])))
                    y = p[1]+(sx-p[0])*(q[1]-p[1])/(q[0]-p[0])
                if marker['y_span'][0]-eps <= y <= marker['y_span'][1]+eps:
                    levels = [v['level_pct'] for v in event['levels'] if abs(v['x']-x) <= 2*eps
                              and marker['y_span'][0]-eps <= v['y'] <= marker['y_span'][1]+eps]
                    marker['events'].append({'lane': index, 'kind': event['kind'], 'x': x, 'y': y,
                                             'level_pct': levels[0] if len(set(levels)) == 1 else None})
        lane['segment_ids'] = [s['id'] for s, _, _ in lane.pop('_edges')]
    labels = []
    for word in words:
        if not re.fullmatch(r't[A-Za-z][A-Za-z0-9_]*', word['text']):
            continue
        r = word['bbox']; cx, cy = (r[0]+r[2])/2, (r[1]+r[3])/2; h = r[3]-r[1]
        candidates = [(abs(cy-a['endpoints'][0][1]), i) for i, a in enumerate(arrows)
                      if a['axis'] == 0 and len(a['endpoints']) == 2
                      and a['endpoints'][0][0]-eps <= cx <= a['endpoints'][1][0]+eps
                      and abs(cy-a['endpoints'][0][1]) <= 2*h]
        candidates.sort()
        arrow_id = candidates[0][1] if candidates and (len(candidates)<2 or candidates[1][0]-candidates[0][0]>eps) else None
        refs = []
        if arrow_id is not None:
            for x, y in arrows[arrow_id]['endpoints']:
                near = [m['id'] for m in markers if abs(m['x']-x)<=2*eps
                        and m['y_span'][0]-eps <= y <= m['y_span'][1]+eps]
                refs.append(near[0] if len(near)==1 else None)
        labels.append({'text': word['text'], 'bbox': r, 'arrow': arrow_id, 'markers': refs})
    return {'bbox': bounds, 'tolerance': eps, 'paths': paths, 'segments': segments,
            'words': words, 'arrows': arrows, 'markers': markers, 'lanes': lanes, 'labels': labels,
            'images': images, 'diagnostics': diagnostics}


def cli():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pdf')
    parser.add_argument('page_index', type=int)
    parser.add_argument('--bbox', nargs=4, type=float)
    return parser.parse_args()


if __name__ == '__main__':
    args = cli()
    facts = extract(args.pdf, args.page_index, args.bbox)
    for message in facts['diagnostics']:
        print(message, file=sys.stderr)
    print(json.dumps(facts, indent=2))
