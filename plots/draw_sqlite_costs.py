"""Paired-policy ratios from the primary SQLite rows; no experiments."""
import argparse
import json
from pathlib import Path
import statistics

ROOT=Path(__file__).resolve().parents[1]


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


def main():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--font',type=Path,required=True);ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args()
    if not args.font.is_file():raise ValueError('Manuscript font missing')
    font_manager.fontManager.addfont(str(args.font))
    face=font_manager.FontProperties(fname=str(args.font))
    plt.rcParams.update({'font.family':face.get_name(),'font.size':8,'pdf.fonttype':42,'axes.unicode_minus':False})
    rows=[json.loads(s) for s in (ROOT/'results/sqlite-adapter/costs.jsonl').read_text().splitlines()]
    values=ratios(rows)
    fig,axes=plt.subplots(1,2,figsize=(6.3,2.15),sharey=True)
    for ax,field,title in zip(axes,('slots','bytes_after_checkpoint'),('(a) Logical slots','(b) Database and sidecars')):
        for j,(mode,color,hatch) in enumerate((('DELETE','0.82',''),('WAL','0.35','//'))):
            ys=[values[mode][f][field] for f in ('shared','unique','churn','backup')]
            bars=ax.bar([x+(-.18 if j==0 else .18) for x in range(4)],ys,.34,
                        color=color,edgecolor='black',linewidth=.5,hatch=hatch,label=mode)
            for bar,y in zip(bars,ys):
                inside=field=='bytes_after_checkpoint'
                ax.text(bar.get_x()+bar.get_width()/2,y-.035 if inside else y+.025,
                        f'{y:.2f}',ha='center',va='top' if inside else 'bottom',fontsize=6.5,
                        color='white' if inside and mode=='WAL' else 'black',
                        bbox={'facecolor':color,'edgecolor':'none','pad':.3} if inside else None)
        ax.axhline(1.,color='black',linestyle='--',linewidth=.6)
        ax.set_ylim(0,1.12);ax.set_yticks([0,.25,.5,.75,1.])
        ax.set_xticks(range(4),['Shared','Unique','Churn','Backup'])
        ax.set_title(title,fontsize=9);ax.tick_params(length=2,width=.5)
        ax.spines[['top','right']].set_visible(False)
    axes[0].set_ylabel('Reuse / no-reuse')
    axes[1].legend(frameon=False,fontsize=7,ncol=2,loc='lower center',bbox_to_anchor=(.5,1.12))
    fig.subplots_adjust(left=.09,right=.985,bottom=.2,top=.78,wspace=.18)
    fig.canvas.draw()
    renderer=fig.canvas.get_renderer()
    for ax in axes:
        for t in [*ax.texts,*ax.get_xticklabels(),*ax.get_yticklabels(),ax.title,ax.yaxis.label]:
            box=t.get_window_extent(renderer)
            if box.x0<0 or box.y0<0 or box.x1>fig.bbox.width or box.y1>fig.bbox.height:
                raise ValueError('Clipped label: '+t.get_text())
    args.out.mkdir(parents=True,exist_ok=True)
    fig.savefig(args.out/'sqlite-costs.pdf',metadata={'Creator':'Python Matplotlib','CreationDate':None,'ModDate':None})
    plt.close(fig)


if __name__=='__main__':main()
