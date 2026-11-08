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
    height = 208.
    c = vector.new_pdf('sqlite-costs.pdf', width, height)
    label_margin, panel_gap, value_gutter, right_margin = 53., 20., 41., 5.
    plot_width = (width-label_margin-panel_gap-2*value_gutter-right_margin)/2
    if plot_width < 105:
        raise ValueError('Figure width is too small for two readable panels')
    colours = (vector.BLUE, vector.ORANGE)
    families = ('shared', 'unique', 'churn', 'backup')
    first_y, last_y = 146.,52.
    axis_bottom,axis_top = 36.,161.
    row_step=(first_y-last_y)/3
    boxes = []
    def point(x,y,index):
        c.setStrokeColorRGB(*colours[index])
        c.setFillColorRGB(*(colours[index] if index==0 else (1,1,1)))
        c.setLineWidth(1.05)
        if index==0:
            c.rect(x-2.85,y-2.85,5.7,5.7,stroke=0,fill=1)
        else:
            c.circle(x,y,3.2,stroke=1,fill=1)
    for index, (field, title) in enumerate((
            ('slots', '(a) Logical slots'),
            ('bytes_after_checkpoint', '(b) Retained files'))):
        left = label_margin+index*(plot_width+value_gutter+panel_gap)
        scale = plot_width
        end=left+scale
        value_right=end+value_gutter
        vector.text(c,left+scale/2,173,title,11.5)
        c.setStrokeColorRGB(.33,.38,.42)
        c.setLineWidth(.8)
        c.line(left,axis_bottom,end,axis_bottom)
        for value,label in ((0,'0'),(.25,'0.25'),(.5,'0.50'),(.75,'0.75'),(1,'1')):
            x=left+value*scale
            c.line(x,axis_bottom,x,axis_bottom-3.5)
            vector.text(c,x,axis_bottom-15,label,10.5)
        c.setStrokeColorRGB(.46,.51,.55)
        c.setDash(3,2)
        c.line(end,axis_bottom,end,axis_top)
        c.setDash()
        for i,family in enumerate(families):
            centre_y=first_y-i*row_step
            if index==0:
                vector.text(c,left-9,centre_y-3,family.capitalize(),11,'right')
            for mode_index,mode in enumerate(('DELETE','WAL')):
                value = values[mode][family][field]
                if not 0<value<=1.:
                    raise ValueError('Retained workload ratio is outside the fixed display range')
                y=centre_y+(6. if mode_index==0 else -6.)
                c.setStrokeColorRGB(.80,.84,.87)
                c.setLineWidth(.5)
                c.line(left,y,end,y)
                point(left+value*scale,y,mode_index)
                label,size=f'{value:.3f}',10.5
                ascent, descent = pdfmetrics.getAscentDescent(vector.FONT, size)
                label_width = pdfmetrics.stringWidth(label, vector.FONT, size)
                label_x=value_right-label_width
                label_y=y-(ascent+descent)/2
                box=(label_x,label_y+descent,label_x+label_width,label_y+ascent)
                if box[0]<end+7 or box[2]>width or box[1]<axis_bottom or box[3]>axis_top:
                    raise ValueError('Clipped value label')
                vector.text(c,label_x,label_y,label,size,'left')
                boxes.append(box)
    for i, a in enumerate(boxes):
        for b in boxes[i+1:]:
            if min(a[2],b[2]) > max(a[0],b[0]) and min(a[3],b[3]) > max(a[1],b[1]):
                raise ValueError('Overlapping value labels')
    vector.text(c,width/2,5,'Reuse / no-reuse',11)
    for i,label in enumerate(('DELETE','WAL')):
        x=width/2-53+i*66
        point(x,195,i)
        vector.text(c,x+9,191.7,label,10.5,'left')
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
