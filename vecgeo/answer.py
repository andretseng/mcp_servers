"""Answer from vecgeo facts and documented serial timing naming conventions."""
import json
import re
import sys
from vecgeo import cli, extract


def unknown():
    return {'lane': 'UNKNOWN', 'kind': 'UNKNOWN', 'level_pct': None}


def answers(facts):
    lanes, markers = facts['lanes'], facts['markers']
    buses = [l['index'] for l in lanes if l['bus']]
    outputs = [l['index'] for l in lanes if l['bus'] and l['highz_regions']]
    inputs = [i for i in buses if i not in outputs]
    result = {'params': {}}
    for label in facts['labels']:
        suffix = label['text'].rsplit('_', 1)[-1].upper()
        choices = outputs if suffix.endswith('DO') else inputs if suffix.endswith('DI') else []
        target = choices[0] if len(choices) == 1 else None
        pair = []
        for ref in label['markers']:
            events = [] if ref is None else markers[ref]['events']
            preferred = [e for e in events if e['lane'] == target]
            if not preferred:
                # Serial timing to/from a bus references a scalar control edge.
                preferred = [e for e in events if not lanes[e['lane']]['bus']] if target is not None else events
            if len(preferred) == 1:
                e = preferred[0]
            elif len(events) == 1:
                e = events[0]
            else:
                e = unknown()
            pair.append({k: e[k] for k in ('lane', 'kind', 'level_pct')})
        result['params'][label['text']] = dict(zip(('start', 'end'), pair)) if len(pair)==2 else {'start': unknown(), 'end': unknown()}
    sampling = []
    for setup in facts['labels']:
        if not re.match(r'tSU(?:_|$)', setup['text'], re.I) or len(setup['markers']) != 2:
            continue
        for hold in facts['labels']:
            if not re.match(r'tHD(?:_|$)', hold['text'], re.I) or len(hold['markers']) != 2:
                continue
            if setup['text'].split('_')[1:] != hold['text'].split('_')[1:]:
                continue
            shared = setup['markers'][1]
            if shared is None or shared != hold['markers'][0]:
                continue
            edges = [e for e in markers[shared]['events'] if e['kind'] in ('rising', 'falling')]
            if len(edges) == 1:
                sampling.append((edges[0]['lane'], edges[0]['kind']))
    if buses:
        edge = next(iter(set(sampling))) if len(set(sampling)) == 1 else ('UNKNOWN', 'UNKNOWN')
        result['sampling_edge'] = dict(zip(('lane', 'kind'), edge))
    else:
        movers = [(e['x_span'][0], l['index']) for l in lanes for e in l['transitions']]
        movers.sort()
        result['first_mover_lane'] = movers[0][1] if movers and (len(movers)<2 or movers[1][0]-movers[0][0]>facts['tolerance']) else 'UNKNOWN'
    return result


if __name__ == '__main__':
    args = cli()
    facts = extract(args.pdf, args.page_index, args.bbox)
    for message in facts['diagnostics']:
        print(message, file=sys.stderr)
    print(json.dumps(answers(facts), indent=2))
