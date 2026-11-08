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
    ('admission', 'Admission', (.32,.35,.39), 'square'),
    ('consumer', 'Certificate consumption', (.13,.38,.34), 'diamond'),
    ('pair', 'Pair analysis', v.ORANGE, 'triangle'),
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
    height = 267.
    c = v.new_pdf('analysis-cpu.pdf',width,height)
    left, right = 51., width-8.
    span=(right-left)/3
    if span<110:
        raise ValueError('Insufficient plot width')
    # Adjacent, disjoint point locations keep near-equal measurements visible.
    # The categorical spacing is unchanged across the two independent panels.
    for k,(title,methods,lo,hi,ticks,bottom,top) in enumerate((
        ('(a) Admission and verification', METHODS[:2], 3, 420, (5,10,30,100,300), 160, 232),
        ('(b) Analysis and certificate production', METHODS[2:], .017, 20, (.03,.1,.3,1,3,10), 44, 116),
    )):
        def ymap(y):
            if not lo < y < hi:
                raise ValueError(f'CPU median outside visible axis: {y}')
            return bottom+(top-bottom)*math.log(y/lo)/math.log(hi/lo)
        v.text(c,left,top+22,title,11.5,'left')
        for i,(_,label,col,kind) in enumerate(methods):
            x=right-202+i*91
            mark(c,x,top+7,kind,col)
            v.text(c,x+7,top+4,label,10,'left')
        for val in ticks:
            y=ymap(val)
            c.setStrokeColorRGB(.88,.90,.92)
            c.setLineWidth(.45)
            c.line(left,y,right,y)
            v.text(c,left-7,y-3.0,str(val),10.25,'right')
        c.setStrokeColorRGB(.46,.49,.53)
        c.setLineWidth(.65)
        c.line(left,bottom,right,bottom)
        c.line(left,bottom,left,top)
        for i,family in enumerate(('shared','unique','churn')):
            x0=left+i*span
            if i:
                c.setLineWidth(.50)
                c.setStrokeColorRGB(.68,.73,.77)
                c.line(x0,bottom,x0,top)
            if k:
                    v.text(c,x0+span/2,bottom-26,family.capitalize(),10.5)
            for j,n in enumerate((32,128,512)):
                x=x0+(j+.5)*span/3
                c.setStrokeColorRGB(.39,.43,.47)
                c.setLineWidth(.65)
                c.line(x,bottom,x,bottom-3.2)
                if k:
                    v.text(c,x,bottom-14,str(n),10.25)
                for offset,(method,_,col,kind) in zip((-5.7,5.7),methods):
                    c.setLineWidth(1.05)
                    mark(c,x+offset,ymap(cells[family,n][method+'_ms']),kind,col)
    v.text(c,(left+right)/2,5,'Client operations',10.5)
    c.saveState()
    c.translate(12,134)
    c.rotate(90)
    v.text(c,0,0,'CPU time (ms, log scale)',10.5)
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
