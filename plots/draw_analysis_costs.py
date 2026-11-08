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
    ('consumer', 'Certificate check', (.13,.38,.34), 'diamond'),
    ('pair', 'Pairs', v.ORANGE, 'triangle'),
    ('frontier', 'Frontier + cert.', v.BLUE, 'circle'),
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
    height=277.
    c = v.new_pdf('analysis-cpu.pdf',width,height)
    left,right,gap=107.,width-8.,20.
    span=(right-left-gap)/2
    if span<142:
        raise ValueError('Insufficient plot width')
    # Horizontal positions encode observed time. The vertical displacement
    # separates the two routines measured for the same input category.
    bottom,top=32.,217.
    ink,grid=(.27,.32,.36),(.83,.86,.88)
    for k,(title,methods,lo,hi,ticks) in enumerate((
        ('(a) Admission and verification',METHODS[:2],3,450,(5,20,100,400)),
        ('(b) Retirement analysis',METHODS[2:],.016,20,(.03,.1,1,10)),
    )):
        x0=left+k*(span+gap)
        def xpos(value):
            if not lo<value<hi:
                raise ValueError(f'CPU median outside visible axis: {value}')
            return x0+span*math.log(value/lo)/math.log(hi/lo)
        v.text(c,x0,257,title,11.5,'left')
        for i,(_,label,col,kind) in enumerate(methods):
            x=x0+3+i*64
            c.setLineWidth(1.05)
            mark(c,x,240,kind,col)
            v.text(c,x+8,237,label,10.5,'left')
        for tick in ticks:
            x=xpos(tick)
            c.setStrokeColorRGB(*grid)
            c.setLineWidth(.52)
            c.line(x,bottom,x,top)
            v.text(c,x,bottom-14,f'{tick:g}',10.5)
        c.setStrokeColorRGB(*ink)
        c.setLineWidth(.82)
        c.line(x0,bottom,x0+span,bottom)
        if k==0:
            v.text(c,5,225,'Workload',10.5,'left')
            v.text(c,95,225,'Ops',10.5,'right')
        for i,family in enumerate(('shared','unique','churn')):
            first=203-i*60
            if i:
                c.setStrokeColorRGB(.68,.73,.76)
                c.setLineWidth(.6)
                c.line(x0,first+12,x0+span,first+12)
            if not k:
                v.text(c,5,first-21,family.capitalize(),11,'left')
            for j,n in enumerate((32,128,512)):
                y=first-j*18
                if not k:
                    v.text(c,95,y-3.2,str(n),10.5,'right')
                points=[(xpos(cells[family,n][method+'_ms']),y+off,col,kind)
                        for off,(method,_,col,kind) in zip((4.2,-4.2),methods)]
                c.setStrokeColorRGB(.68,.72,.74)
                c.setLineWidth(.64)
                c.line(points[0][0],points[0][1],points[1][0],points[1][1])
                for x,y,col,kind in points:
                    c.setLineWidth(1.05)
                    mark(c,x,y,kind,col)
    v.text(c,(left+right)/2,4,'CPU time (ms, log scale)',11)
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
