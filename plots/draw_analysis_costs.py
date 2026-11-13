"""Plot retained analysis timings using the table's aggregation; no experiments."""
import argparse
import json
import math
from pathlib import Path
import sys

import draw_write_ratios as v

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from analysis_costs import aggregates

METHODS = (
    ('admission', 'Admission', v.SLATE, 'square'),
    ('consumer', 'Certificate check', v.BLUE, 'diamond'),
    ('pair', 'Pair analysis', v.SLATE, 'triangle'),
    ('frontier', 'Frontier + certificate', v.BLUE, 'circle'),
)


def series():
    source = ROOT/'results/analysis-costs/observations.jsonl'
    return {(row['family'],row['operations']):row for row in aggregates(
        [json.loads(line) for line in source.read_text(encoding='utf-8').splitlines()])}


def mark(c, x, y, kind, color):
    c.setDash()
    c.setStrokeColorRGB(*color)
    c.setFillColorRGB(*(color if kind=='circle' else (1,1,1)))
    if kind == 'circle':
        c.circle(x,y,2.45,fill=1,stroke=0)
    elif kind == 'square':
        c.rect(x-2.2,y-2.2,4.4,4.4,fill=1,stroke=1)
    else:
        d = c.beginPath()
        points = [(0,3.8),(-3.8,-2.9),(3.8,-2.9)] if kind == 'triangle' else [
            (0,3),(-3,0),(0,-3),(3,0)]
        d.moveTo(x+points[0][0],y+points[0][1])
        for dx,dy in points[1:]:
            d.lineTo(x+dx,y+dy)
        d.close()
        c.drawPath(d,fill=1,stroke=1)


def draw(width, cells):
    c=v.new_pdf('analysis-cpu.pdf',width,194)
    left,right,gap=47.,width-8.,14.
    panel=(right-left-2*gap)/3
    if panel<107:
        raise ValueError('Plot needs three legible facets')
    values = [row[key+'_ms'] for row in cells.values() for key, *_ in METHODS]
    if any(not math.isfinite(y) or y <= 0 for y in values):
        raise ValueError('Log axis requires positive finite observations')
    first = math.floor(math.log10(min(values)))
    last = math.floor(math.log10(max(values)))
    lo = min(10.**first * .85, min(values) * .77)
    hi = max(10.**last * 1.18, max(values) * 1.30)
    ticks = [10.**i for i in range(first, last + 1)]
    bottom,top=35.,146.
    def ypos(y):
        if not lo<y<hi:
            raise ValueError(f'CPU time outside the plot: {y}')
        return bottom+(top-bottom)*math.log(y/lo)/math.log(hi/lo)
    from reportlab.pdfbase import pdfmetrics
    key_widths=[22+pdfmetrics.stringWidth(method[1],v.FONT,v.TICK) for method in METHODS]
    key_gap=13.
    key_x=(width-sum(key_widths)-(len(key_widths)-1)*key_gap)/2
    if key_x<5:
        raise ValueError('CPU legend exceeds available width')
    for method,key_width in zip(METHODS,key_widths):
        c.setLineWidth(.58)
        c.setStrokeColorRGB(*method[2])
        if method[0] in ('admission','pair'):
            c.setDash(2,1.65)
        c.line(key_x,181,key_x+17,181)
        c.setDash()
        c.setLineWidth(1.04)
        mark(c,key_x+8.5,181,method[3],method[2])
        v.text(c,key_x+22,177.8,method[1],v.TICK,'left')
        key_x+=key_width+key_gap
    for i,family in enumerate(('shared','unique','churn')):
        l=left+i*(panel+gap)
        v.text(c,l+panel/2,155,family.capitalize(),v.LABEL)
        for tick in ticks:
            y=ypos(tick)
            c.setLineWidth(.49)
            c.setStrokeColorRGB(.88,.89,.90)
            c.line(l,y,l+panel,y)
            if i==0:
                v.text(c,l-5,y-3.1,f'{tick:g}',v.TICK,'right')
        c.setLineWidth(.75)
        c.setStrokeColorRGB(.28,.34,.39)
        c.line(l,bottom,l+panel,bottom)
        if not i:
            c.line(l,bottom,l,top)
        counts=(32,128,512)
        offsets=(-8.,-2.7,2.7,8.)
        for offset,(key,_,colour,shape) in zip(offsets,METHODS):
            points=[(l+(j+.5)*panel/3+offset,ypos(cells[family,count][key+'_ms']))
                    for j,count in enumerate(counts)]
            path=c.beginPath()
            path.moveTo(*points[0])
            for point in points[1:]:
                path.lineTo(*point)
            c.setStrokeColorRGB(*colour)
            c.setLineWidth(.58)
            if key in ('admission','pair'):
                c.setDash(2,1.65)
            c.drawPath(path)
            c.setDash()
        for j,count in enumerate(counts):
            x=l+(j+.5)*panel/3
            v.text(c,x,19,str(count),v.TICK)
            # The mark order matches the legend. The four positions within
            # each category are distinct even when two medians are equal.
            for offset,(key,_,colour,shape) in zip(offsets,METHODS):
                y=ypos(cells[family,count][key+'_ms'])
                c.setLineWidth(1.08)
                mark(c,x+offset,y,shape,colour)
    v.text(c,(left+right)/2,4,'Client operations',v.LABEL)
    c.saveState()
    c.translate(11,(bottom+top)/2)
    c.rotate(90)
    v.text(c,0,0,'CPU time (ms, log scale)',v.LABEL)
    c.restoreState()
    c.save()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--font-root',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--column-width-pt',type=float,default=430.00462)
    args=parser.parse_args()
    v.register_font(args.font_root)
    v.OUTPUT=args.out
    v.OUTPUT.mkdir(parents=True,exist_ok=True)
    draw(args.column_width_pt*v.PT,series())


if __name__ == '__main__':
    main()
