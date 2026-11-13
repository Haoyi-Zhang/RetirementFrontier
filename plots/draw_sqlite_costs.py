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
    import draw_write_ratios as v

    c=v.new_pdf('sqlite-costs.pdf',width,184)
    left,right,bottom,top=72.,width-14.,34.,139.
    gap=24.
    span=(right-left-gap)/2
    if span<137:
        raise ValueError('Ratio plot is too narrow')
    panels=(('slots','Logical slot count',left),
            ('bytes_after_checkpoint','Retained bytes',left+span+gap))
    def marker(x,y,mode):
        colour=v.BLUE if mode=='DELETE' else v.SLATE
        c.setStrokeColorRGB(*colour)
        c.setFillColorRGB(*(colour if mode=='DELETE' else (1,1,1)))
        c.setLineWidth(1.05)
        if mode=='DELETE':
            c.rect(x-2.65,y-2.65,5.3,5.3,stroke=1,fill=1)
        else:
            c.circle(x,y,3.05,stroke=1,fill=1)
    for mode,x in (('DELETE',width/2-40),('WAL',width/2+21)):
        marker(x,172,mode)
        v.text(c,x+8,168.8,mode,v.TICK,'left')
    for field,title,origin in panels:
        v.text(c,origin+span/2,150,title,v.LABEL)
        for tick,name in ((0,'0'),(.25,'0.25'),(.5,'0.50'),(.75,'0.75'),(1,'1')):
            x=origin+span*tick
            c.setLineWidth(.68 if tick==1 else .45)
            c.setStrokeColorRGB(*((.48,.51,.54) if tick==1 else (.87,.89,.90)))
            if tick==1:
                c.setDash(2.5,2.5)
            c.line(x,bottom,x,top)
            c.setDash()
            v.text(c,x,bottom-15,name,v.TICK)
        c.setStrokeColorRGB(.30,.35,.40)
        c.setLineWidth(.7)
        c.line(origin,bottom,origin+span,bottom)
        for i,family in enumerate(('shared','unique','churn','backup')):
            centre=126-i*23.5
            if field=='slots':
                v.text(c,origin-13,centre-3.1,family.capitalize(),v.LABEL,'right')
            for mode,delta in (('DELETE',4),('WAL',-4)):
                record=values[mode][family]
                if not 0<record['slots']<record['bytes_after_checkpoint']<=1:
                    raise ValueError('Retained values outside the ratio axis')
                marker(origin+span*record[field],centre+delta,mode)
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
