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

    c=v.new_pdf('sqlite-costs.pdf',width,179)
    left,right,bottom,top=101.,width-28.,33.,146.
    if right-left<250:
        raise ValueError('Ratio plot is too narrow')
    def xpos(x):
        return left+(right-left)*x
    def marker(x,y,kind,mode=None):
        colour=v.BLUE if mode=='DELETE' else v.SLATE
        c.setStrokeColorRGB(*colour)
        c.setFillColorRGB(*(colour if mode=='DELETE' else (1,1,1)))
        c.setLineWidth(1.05)
        if kind=='slots':
            c.rect(x-2.9,y-2.9,5.8,5.8,stroke=1,fill=1)
        elif kind=='files':
            c.circle(x,y,3.2,stroke=1,fill=1)
        else:
            # The journal key uses a narrow swatch, not either data glyph.
            c.rect(x-3.7,y-1.75,7.4,3.5,stroke=1,fill=1)
    key=(('Measure:',None,None),('Logical slots','slots',None),
         ('Retained files','files',None),('Journal:',None,None),
         ('DELETE','journal','DELETE'),('WAL','journal','WAL'))
    key_widths=[pdfmetrics.stringWidth(label,v.FONT,v.TICK)+(13 if shape else 0)
                for label,shape,_ in key]
    gaps=(8.,14.,28.,8.,13.,0.)
    key_x=(width-sum(key_widths)-sum(gaps))/2
    if key_x<6:
        raise ValueError('SQLite legend exceeds available width')
    for (label,shape,mode),span,gap in zip(key,key_widths,gaps):
        if shape:
            marker(key_x+4,169,shape,mode)
        v.text(c,key_x+(13 if shape else 0),165.8,label,v.TICK,'left')
        key_x+=span+gap
    for tick,name in ((0,'0'),(.25,'0.25'),(.5,'0.50'),(.75,'0.75'),(1,'1')):
        x=xpos(tick)
        c.setLineWidth(.65 if tick==1 else .48)
        c.setStrokeColorRGB(*((.46,.51,.55) if tick==1 else (.86,.89,.91)))
        if tick==1:
            c.setDash(2.5,2.5)
        if tick:
            c.line(x,bottom,x,top)
        c.setDash()
        v.text(c,x,bottom-15,name,v.TICK)
    c.setStrokeColorRGB(.30,.35,.40)
    c.setLineWidth(.8)
    c.line(left,bottom,right,bottom)
    for i,family in enumerate(('shared','unique','churn','backup')):
        centre=133-i*27.5
        v.text(c,6,centre-3.1,family.capitalize(),v.LABEL,'left')
        for mode,delta in (('DELETE',5.8),('WAL',-5.8)):
            record=values[mode][family]
            a,b=record['slots'],record['bytes_after_checkpoint']
            if not 0<a<b<=1:
                raise ValueError('Retained values outside the ratio axis')
            y=centre+delta
            marker(xpos(a),y,'slots',mode)
            marker(xpos(b),y,'files',mode)
            a_text,b_text=f'{a:.3f}',f'{b:.3f}'
            if (xpos(a)-8-pdfmetrics.stringWidth(a_text,v.FONT,v.TICK)<59 or
                    xpos(b)-8-pdfmetrics.stringWidth(b_text,v.FONT,v.TICK)<xpos(.75)+2):
                raise ValueError('Insufficient numeric label margin')
            v.text(c,xpos(a)-8,y-3.1,a_text,v.TICK,'right')
            v.text(c,xpos(b)-8,y-3.1,b_text,v.TICK,'right')
    v.text(c,(left+right)/2,4.5,'Reuse / no-reuse',v.LABEL)
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
