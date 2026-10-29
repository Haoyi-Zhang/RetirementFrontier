"""Python vector ratios from primary SQLite rows; no experiments."""
import argparse
import json
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[1]

def ratios(rows):
    result={}
    for mode in ('DELETE','WAL'):
        result[mode]={}
        for family in ('shared','unique','churn','backup'):
            group={p:[r for r in rows if (r['mode'],r['family'],r['policy'])==(mode,family,p)]
                   for p in ('frontier-reuse','no-reuse')}
            if any(len(v)!=3 for v in group.values()):raise ValueError('Expected three runs per policy')
            if len({r['seed'] for r in group['frontier-reuse']})!=3:raise ValueError('Repeated seed')
            if {r['seed'] for r in group['frontier-reuse']}!={r['seed'] for r in group['no-reuse']}:
                raise ValueError('Unmatched comparison')
            result[mode][family]={}
            for field in ('slots','bytes_after_checkpoint'):
                a,b=(statistics.median(r[field] for r in group[p]) for p in ('frontier-reuse','no-reuse'))
                if a<=0 or b<=0:raise ValueError('Invalid cost')
                result[mode][family][field]=a/b
    return result


def hatch(c, x, y, width, height):
    c.saveState()
    clip = c.beginPath()
    clip.rect(x, y, width, height)
    c.clipPath(clip, stroke=0, fill=0)
    c.setStrokeColorRGB(0, 0, 0)
    c.setLineWidth(.5)
    at = x-height
    while at < x+width:
        c.line(at, y, at+height, y+height)
        at += 6
    c.restoreState()


def draw(width, values):
    from reportlab.pdfbase import pdfmetrics
    import draw_write_ratios as vector
    height = 6.3*vector.CM
    c = vector.new_pdf('sqlite-costs.pdf', width, height)
    panel_width = (width-41-8-26)/2
    colours = ((.82, .82, .82), (.35, .35, .35))
    families = ('shared', 'unique', 'churn', 'backup')
    for index, (field, title) in enumerate((
            ('slots', '(a) Logical slots'),
            ('bytes_after_checkpoint', '(b) Database and sidecars'))):
        left, bottom = 41+index*(panel_width+26), 35
        ticks = [(n, f'{n:.2f}' if index == 0 else '') for n in (0, .25, .5, .75, 1.)]
        xy = vector.axes(c, (left, bottom, panel_width, height-81),
                         (-.5, 3.5), (0, 1.14),
                         list(enumerate(('Shared', 'Unique', 'Churn', 'Backup'))),
                         ticks, '', 'Reuse / no-reuse' if index == 0 else '',
                         ticksize=8, labelsize=9)
        vector.text(c, left+panel_width/2, height-28, title, 9)
        reference = xy(0, 1.)[1]
        c.setDash(3, 2)
        c.setLineWidth(.55)
        c.line(left, reference, left+panel_width, reference)
        c.setDash()
        boxes = []
        for mode_index, mode in enumerate(('DELETE', 'WAL')):
            colour = colours[mode_index]
            for i, family in enumerate(families):
                value = values[mode][family][field]
                centre, baseline = xy(i+(-.21 if mode_index == 0 else .21), 0)
                top = xy(i, value)[1]
                bar_width = panel_width/4*.36
                x = centre-bar_width/2
                c.setFillColorRGB(*colour)
                c.setStrokeColorRGB(0, 0, 0)
                c.setLineWidth(.5)
                c.rect(x, baseline, bar_width, top-baseline, fill=1, stroke=1)
                if mode_index:
                    hatch(c, x, baseline, bar_width, top-baseline)
                    c.rect(x, baseline, bar_width, top-baseline, fill=0, stroke=1)
                label, size = f'{value:.2f}', 7.
                ascent, descent = pdfmetrics.getAscentDescent(vector.FONT, size)
                label_width = pdfmetrics.stringWidth(label, vector.FONT, size)
                if label_width > bar_width-1:
                    raise ValueError('Value label exceeds bar width')
                label_y = top-3-ascent if index else top+4
                if index:
                    c.setFillColorRGB(*colour)
                    c.rect(centre-label_width/2-.4, label_y+descent-.4,
                           label_width+.8, ascent-descent+.8, fill=1, stroke=0)
                c.setFillColorRGB(*( (1,1,1) if index and mode_index else (0,0,0) ))
                c.setFont(vector.FONT, size)
                c.drawCentredString(centre, label_y, label)
                box = (centre-label_width/2, label_y+descent,
                       centre+label_width/2, label_y+ascent)
                if box[0] < left or box[2] > left+panel_width or box[1] < bottom or box[3] > height-46:
                    raise ValueError('Clipped value label')
                boxes.append(box)
        for i, a in enumerate(boxes):
            for b in boxes[i+1:]:
                if min(a[2],b[2]) > max(a[0],b[0]) and min(a[3],b[3]) > max(a[1],b[1]):
                    raise ValueError('Overlapping value labels')
    items = list(zip(('DELETE', 'WAL'), colours))
    vector.legend(c, items, width, height-10, 8.5)
    lengths = [14+pdfmetrics.stringWidth(label, vector.FONT, 8.5) for label,_ in items]
    legend_left = (width-sum(lengths)-14)/2
    hatch(c, legend_left+lengths[0]+14, height-10, 8, 5)
    c.save()


def main():
    import draw_write_ratios as vector
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--font-root', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--column-width-pt', type=float, default=430.00462)
    args = parser.parse_args()
    if args.column_width_pt <= 100:
        raise ValueError('Invalid figure width')
    vector.register_font(args.font_root)
    vector.OUTPUT = args.out
    args.out.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(line) for line in
            (ROOT/'results/sqlite-adapter/costs.jsonl').read_text(encoding='utf-8').splitlines()]
    draw(args.column_width_pt*vector.PT, ratios(rows))


if __name__ == '__main__':
    main()
