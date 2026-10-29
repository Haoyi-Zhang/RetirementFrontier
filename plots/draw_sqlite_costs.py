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


def draw(width, values):
    from reportlab.pdfbase import pdfmetrics
    import draw_write_ratios as vector
    height = 8.4*vector.CM
    c = vector.new_pdf('sqlite-costs.pdf', width, height)
    # Values occupy their own aligned column, clear of the bars and grid.
    label_margin, panel_gap, value_gutter, right_margin = 48., 22., 31., 4.
    plot_width = (width-label_margin-panel_gap-2*value_gutter-right_margin)/2
    if plot_width < 85:
        raise ValueError('Figure width is too small for two readable panels')
    colours = (vector.BLUE, vector.ORANGE)
    families = ('shared', 'unique', 'churn', 'backup')
    bottom, top = 51., height-61.
    row_step = (top-bottom)/3
    boxes = []
    for index, (field, title) in enumerate((
            ('slots', '(a) Logical slots'),
            ('bytes_after_checkpoint', '(b) Retained files'))):
        left = label_margin+index*(plot_width+value_gutter+panel_gap)
        scale = plot_width
        value_right = left+plot_width+value_gutter
        vector.text(c, left+plot_width/2, height-36, title, 10)
        for value in (0., .25, .5, .75, 1.):
            x=left+value*scale
            c.setStrokeColorRGB(*((.48,.52,.56) if value==1. else (.90,.92,.93)))
            c.setLineWidth(.6 if value==1. else .35)
            c.setDash(3,2) if value==1. else c.setDash()
            c.line(x,bottom-16,x,top+16)
            c.setDash()
            vector.text(c,x,bottom-29,f'{value:.2f}',8)
        c.setStrokeColorRGB(.67,.71,.74)
        c.setLineWidth(.5)
        c.line(left,bottom-16,left+scale,bottom-16)
        for i,family in enumerate(families):
            centre_y=top-i*row_step
            if index==0:
                vector.text(c,left-8,centre_y-2,family.capitalize(),9,'right')
            for mode_index,mode in enumerate(('DELETE','WAL')):
                colour=colours[mode_index]
                value = values[mode][family][field]
                if not 0<value<=1.:
                    raise ValueError('Retained workload ratio is outside the fixed display range')
                y=centre_y+(2.5 if mode_index==0 else -9.5)
                bar_height=7
                c.setFillColorRGB(*(colour if mode_index==0 else (1.,1.,1.)))
                c.setStrokeColorRGB(*colour)
                c.setLineWidth(.8)
                c.rect(left,y,value*scale,bar_height,fill=1,stroke=1)
                label,size=f'{value:.3f}',8.5
                ascent, descent = pdfmetrics.getAscentDescent(vector.FONT, size)
                label_width = pdfmetrics.stringWidth(label, vector.FONT, size)
                label_x=value_right-label_width
                label_y=y+(bar_height-ascent-descent)/2
                box=(label_x,label_y+descent,label_x+label_width,label_y+ascent)
                if box[0]<left+plot_width+5 or box[2]>width or box[1]<bottom-16 or box[3]>top+16:
                    raise ValueError('Clipped value label')
                vector.text(c,label_x,label_y,label,size,'left')
                boxes.append(box)
    for i, a in enumerate(boxes):
        for b in boxes[i+1:]:
            if min(a[2],b[2]) > max(a[0],b[0]) and min(a[3],b[3]) > max(a[1],b[1]):
                raise ValueError('Overlapping value labels')
    vector.text(c, width/2, 5, 'Reuse / no-reuse', 9)
    lengths = [14+pdfmetrics.stringWidth(label, vector.FONT, 9) for label in ('DELETE','WAL')]
    legend_left = (width-sum(lengths)-14)/2
    for i, (label, colour) in enumerate(zip(('DELETE', 'WAL'), colours)):
        x=legend_left+(lengths[0]+14 if i else 0)
        c.setStrokeColorRGB(*colour)
        c.setFillColorRGB(*(colour if i==0 else (1.,1.,1.)))
        c.setLineWidth(.8)
        c.rect(x,height-13,8,5,fill=1,stroke=1)
        vector.text(c,x+12,height-13.1,label,9,'left')
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
