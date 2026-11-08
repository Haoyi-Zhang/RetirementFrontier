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
    height = 247.
    c = v.new_pdf('analysis-cpu.pdf',width,height)
    left,right,gap=49.,width-8.,12.
    span=(right-left-2*gap)/3
    if span<104:
        raise ValueError('Insufficient plot width')
    # Each row shares a log scale across its three workload facets. Markers
    # are offset only within their category, never along the measured axis.
    for k,(title,methods,lo,hi,ticks,bottom,top,heading) in enumerate((
        ('(a) Input and verification', METHODS[:2], 3, 450, (5,20,100,400), 140, 198, 231),
        ('(b) Retirement analysis', METHODS[2:], .016, 20, (.03,.1,1,10), 45, 105, 119),
    )):
        def ymap(y):
            if not lo < y < hi:
                raise ValueError(f'CPU median outside visible axis: {y}')
            return bottom+(top-bottom)*math.log(y/lo)/math.log(hi/lo)
        v.text(c,left,heading,title,11.5,'left')
        for i,(_,label,col,kind) in enumerate(methods):
            x=right-202+i*86
            c.setLineWidth(1.05)
            mark(c,x,heading+3.5,kind,col)
            v.text(c,x+8,heading,label,10.5,'left')
        for i,family in enumerate(('shared','unique','churn')):
            x0=left+i*(span+gap)
            if not k:
                v.text(c,x0+span/2,209,family.capitalize(),11)
            for val in ticks:
                y=ymap(val)
                c.setStrokeColorRGB(.81,.85,.88)
                c.setLineWidth(.52)
                c.line(x0,y,x0+span,y)
                if i==0:
                    v.text(c,x0-6,y-3.3,str(val),10.5,'right')
            c.setStrokeColorRGB(.32,.37,.41)
            c.setLineWidth(.72)
            c.line(x0,bottom,x0+span,bottom)
            if i==0:
                c.line(x0,bottom,x0,top)
            for j,n in enumerate((32,128,512)):
                x=x0+(j+.5)*span/3
                c.setStrokeColorRGB(.39,.43,.47)
                c.setLineWidth(.65)
                c.line(x,bottom,x,bottom-3.2)
                if k:
                    v.text(c,x,bottom-15,str(n),10.5)
                for offset,(method,_,col,kind) in zip((-5.7,5.7),methods):
                    c.setLineWidth(1.05)
                    mark(c,x+offset,ymap(cells[family,n][method+'_ms']),kind,col)
    v.text(c,(left+right)/2,7,'Client operations',11)
    c.saveState()
    c.translate(12,121)
    c.rotate(90)
    v.text(c,0,0,'CPU time (ms, log scale)',11)
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
