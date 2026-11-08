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
    import draw_write_ratios as v

    height=222.
    c=v.new_pdf('sqlite-costs.pdf',width,height)
    left,right,bottom,top=109.,width-59.,36.,179.
    if right-left<240:
        raise ValueError('Insufficient width for a common ratio axis')
    def xpos(x):
        return left+(right-left)*x
    def line(x0,x1,y,mode):
        c.setLineWidth(1.02)
        c.setStrokeColorRGB(*(v.BLUE if mode=='DELETE' else v.ORANGE))
        c.setDash(4,2.4) if mode=='WAL' else c.setDash()
        c.line(x0,y,x1,y)
        c.setDash()
    def square(x,y,colour):
        c.setFillColorRGB(*colour)
        c.rect(x-2.8,y-2.8,5.6,5.6,stroke=0,fill=1)
    def circle(x,y,colour):
        c.setLineWidth(1.05)
        c.setStrokeColorRGB(*colour)
        c.setFillColorRGB(1,1,1)
        c.circle(x,y,3.2,stroke=1,fill=1)
    square(48,204,(.20,.27,.31))
    v.text(c,57,201,'Logical slots',10.5,'left')
    circle(124,204,(.20,.27,.31))
    v.text(c,133,201,'Retained files',10.5,'left')
    for x,mode in ((232,'DELETE'),(323,'WAL')):
        line(x,x+24,204,mode)
        v.text(c,x+30,201,mode,10.5,'left')
    for t,label in ((0,'0'),(.25,'0.25'),(.50,'0.50'),(.75,'0.75'),(1,'1')):
        x=xpos(t)
        c.setStrokeColorRGB(*((.49,.54,.58) if t==1 else (.84,.87,.89)))
        c.setLineWidth(.65 if t==1 else .48)
        if t==1:
            c.setDash(2.5,2.5)
        if t:
            c.line(x,bottom,x,top)
        c.setDash()
        v.text(c,x,bottom-15,label,10.5)
    c.setStrokeColorRGB(.36,.41,.45)
    c.setLineWidth(.8)
    c.line(left,bottom,right,bottom)
    for i,family in enumerate(('shared','unique','churn','backup')):
        cy=164-i*35
        v.text(c,7,cy-3.3,family.capitalize(),11,'left')
        for mode,offset,colour in (('DELETE',6.5,v.BLUE),('WAL',-6.5,v.ORANGE)):
            y=cy+offset
            record=values[mode][family]
            slots,files=record['slots'],record['bytes_after_checkpoint']
            if not 0<slots<files<=1:
                raise ValueError('Retained metrics outside the fixed paired-axis order')
            a,b=xpos(slots),xpos(files)
            line(a,b,y,mode)
            square(a,y,colour)
            circle(b,y,colour)
            a_label,b_label=f'{slots:.3f}',f'{files:.3f}'
            aw=pdfmetrics.stringWidth(a_label,v.FONT,10.5)
            bw=pdfmetrics.stringWidth(b_label,v.FONT,10.5)
            value_column=right+12
            if a-9-aw<56 or value_column+bw>width-4:
                raise ValueError('Insufficient numeric label margin')
            c.setFillColorRGB(1,1,1)
            c.rect(a-10-aw,y-5.5,aw+2,11,stroke=0,fill=1)
            v.text(c,a-9,y-3.1,a_label,10.5,'right')
            v.text(c,value_column,y-3.1,b_label,10.5,'left')
    v.text(c,(left+right)/2,5,'Reuse / no-reuse',11)
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
