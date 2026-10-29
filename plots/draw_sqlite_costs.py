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
    height = 8.4*vector.CM
    c = vector.new_pdf('sqlite-costs.pdf', width, height)
    panel_width = (width-57-18-46)/2
    colours = (vector.BLUE, vector.ORANGE)
    families = ('shared', 'unique', 'churn', 'backup')
    bottom, top = 53., height-55.
    row_step = (top-bottom)/3
    for index, (field, title) in enumerate((
            ('slots', '(a) Logical slots'),
            ('bytes_after_checkpoint', '(b) Retained files'))):
        left = 57+index*(panel_width+46)
        scale = panel_width/1.19
        vector.text(c, left+panel_width/2, height-33, title, 10)
        for value in (0., .25, .5, .75, 1.):
            x=left+value*scale
            c.setStrokeColorRGB(*((.42,.46,.50) if value==1. else (.88,.90,.92)))
            c.setLineWidth(.55 if value==1. else .35)
            c.setDash(3,2) if value==1. else c.setDash()
            c.line(x,bottom-16,x,top+16)
            c.setDash()
            vector.text(c,x,bottom-29,f'{value:.2f}',8)
        c.setStrokeColorRGB(.25,.29,.33)
        c.line(left,bottom-16,left+scale,bottom-16)
        vector.text(c,left+scale/2,9,'Reuse / no-reuse',9)
        boxes = []
        for i,family in enumerate(families):
            centre_y=top-i*row_step
            if index==0:
                vector.text(c,left-8,centre_y-2,family.capitalize(),9,'right')
            for mode_index,mode in enumerate(('DELETE','WAL')):
                colour=colours[mode_index]
                value = values[mode][family][field]
                if not 0<value<=1.:
                    raise ValueError('Retained workload ratio is outside the fixed display range')
                y=centre_y+(2 if mode_index==0 else -11)
                bar_height=9
                c.setFillColorRGB(*colour)
                c.setStrokeColorRGB(.18,.23,.28)
                c.setLineWidth(.4)
                c.rect(left,y,value*scale,bar_height,fill=1,stroke=1)
                if mode_index:
                    hatch(c,left,y,value*scale,bar_height)
                label,size=f'{value:.3f}',8.
                ascent, descent = pdfmetrics.getAscentDescent(vector.FONT, size)
                label_width = pdfmetrics.stringWidth(label, vector.FONT, size)
                label_x=left+value*scale+4
                label_y=y+(bar_height-ascent-descent)/2
                box=(label_x,label_y+descent,label_x+label_width,label_y+ascent)
                if box[0]<left or box[2]>left+panel_width or box[1]<bottom-16 or box[3]>top+16:
                    raise ValueError('Clipped value label')
                # Keep reference/grid lines clear of the directly labeled values.
                c.setFillColorRGB(1,1,1)
                c.rect(box[0]-1,box[1]-.6,box[2]-box[0]+2,box[3]-box[1]+1.2,fill=1,stroke=0)
                vector.text(c,label_x,label_y,label,size,'left')
                boxes.append(box)
        for i, a in enumerate(boxes):
            for b in boxes[i+1:]:
                if min(a[2],b[2]) > max(a[0],b[0]) and min(a[3],b[3]) > max(a[1],b[1]):
                    raise ValueError('Overlapping value labels')
    items = list(zip(('DELETE', 'WAL'), colours))
    vector.legend(c, items, width, height-11, 9)
    lengths = [14+pdfmetrics.stringWidth(label, vector.FONT, 9) for label,_ in items]
    legend_left = (width-sum(lengths)-14)/2
    hatch(c, legend_left+lengths[0]+14, height-11, 8, 5)
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
