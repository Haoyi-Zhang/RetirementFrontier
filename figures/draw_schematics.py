"""Vector drawing primitives; copied into each independent project generator."""
import argparse
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, Polygon, Circle, FancyArrowPatch
from matplotlib import font_manager

plt.rcParams.update({'font.family':'STIXGeneral', 'mathtext.fontset':'stix',
                     'font.size':9, 'pdf.fonttype':42, 'ps.fonttype':42,
                     'text.usetex':False, 'axes.unicode_minus':False})
CM = 1 / 2.54
FACE = None

class Drawing:
    def __init__(self, bounds):
        x0,y0,x1,y1 = bounds
        self.fig = plt.figure(figsize=((x1-x0)*CM,(y1-y0)*CM))
        self.ax = self.fig.add_axes([0,0,1,1], xlim=(x0,x1), ylim=(y0,y1))
        self.ax.set_aspect('equal'); self.ax.axis('off')
        self.nodes = {}
        self.labels = []

    def text(self,x,y,label,size=9,ha='center',va='center'):
        artist=self.ax.text(x,y,label,fontsize=size,ha=ha,va=va,fontproperties=FACE,
                            linespacing=1.15,clip_on=False,zorder=4)
        self.labels.append([artist,None])
        return artist

    def box(self,name,x,y,w,h,label,dashed=False,shape='rect',size=9):
        style = {'edgecolor':'black','facecolor':'white','linewidth':.55,
                 'linestyle':(0,(4,3)) if dashed else 'solid', 'zorder':2}
        if shape == 'diamond':
            obj = Polygon([(x-w/2,y),(x,y+h/2),(x+w/2,y),(x,y-h/2)],**style)
        elif shape == 'circle': obj = Circle((x,y),w/2,**style)
        else: obj = Rectangle((x-w/2,y-h/2),w,h,**style)
        self.ax.add_patch(obj); self.text(x,y,label,size)
        self.labels[-1][1]=(x-w/2,y-h/2,x+w/2,y+h/2)
        self.nodes[name] = (x,y,w,h,shape)

    def point(self,name,side):
        x,y,w,h,_ = self.nodes[name]
        return {'n':(x,y+h/2),'s':(x,y-h/2),'e':(x+w/2,y),
                'w':(x-w/2,y),'nw':(x-w/2,y+h/2),
                'ne':(x+w/2,y+h/2),'c':(x,y)}[side]

    def line(self,points,dashed=False):
        xs,ys=zip(*points)
        self.ax.plot(xs,ys,color='black',linewidth=.55,
                     linestyle=(0,(4,3)) if dashed else 'solid',zorder=1)

    def arrow(self,p,q,dashed=False,via=()):
        points=[p,*via,q]
        if via:self.line(points[:-1],dashed)
        self.ax.add_patch(FancyArrowPatch(points[-2],points[-1],
                         arrowstyle='-|>',mutation_scale=7.5,linewidth=.55,
                         color='black',shrinkA=0,shrinkB=0,
                         linestyle=(0,(4,3)) if dashed else 'solid',zorder=1))

    def edge(self,a,b,sa='s',sb='n',**kwargs):
        self.arrow(self.point(a,sa),self.point(b,sb),**kwargs)

    def frame(self,x,y,w,h):
        self.ax.add_patch(Rectangle((x,y),w,h,fill=False,edgecolor='black',
                                   linewidth=.55,linestyle=(0,(4,3)),zorder=0))

    def save(self,out,name):
        # Fixed physical canvas: do not enlarge or rasterize the scientific figure.
        self.fig.canvas.draw()
        renderer=self.fig.canvas.get_renderer()
        x0,x1=self.ax.get_xlim();y0,y1=self.ax.get_ylim()
        for artist,node in self.labels:
            b=artist.get_window_extent(renderer).transformed(self.ax.transData.inverted())
            if b.x0<x0-.02 or b.x1>x1+.02 or b.y0<y0-.02 or b.y1>y1+.02:
                raise ValueError(name+': clipped label '+repr(artist.get_text()))
            if node and (b.x0<node[0]-.02 or b.x1>node[2]+.02 or b.y0<node[1]-.02 or b.y1>node[3]+.02):
                raise ValueError(name+': label exceeds node '+repr(artist.get_text()))
        self.fig.savefig(out/(name+'.pdf'),format='pdf',
                         metadata={'Creator':'Python Matplotlib','Producer':None,
                                   'CreationDate':None,'ModDate':None})
        plt.close(self.fig)






def frontier(out):
    d=Drawing((-.3,-1.3,9.7,1.65))
    for name,x,label in [('a',1.,'a\nallocate'),('r',3.4,'r\nuses a'),('q',5.8,'q\nretires a'),('w',8.2,'w\noverwrite')]:
        d.box(name,x,0,1.75,.85,label)
    for a,b in [('a','r'),('r','q'),('q','w')]:d.edge(a,b,'e','w')
    d.arrow(d.point('r','n'),d.point('a','n'),dashed=True,via=[(3.4,1.0),(1,1.0)])
    d.text(2.2,1.35,'reference')
    d.text(3.4,-.95,'T(w) = r')
    d.text(5.8,-.95,'F(w) = q > r')
    d.save(out,'retirement-frontier')

def paths(out):
    d=Drawing((-.9,-4.,14.2,2.7))
    nodes=[('trace',1.4,0,2.2,.9,'admitted\ntrace'),
           ('producer',5.,1.1,2.3,.9,'frontier\nproducer'),
           ('certificate',8.65,1.1,2.3,.9,'certificate'),
           ('consumer',12.4,1.1,2.9,.9,'code-separated\nconsumer'),
           ('cut',5.,-.7,2.3,.9,'legal cut'),
           ('image',8.65,-.7,2.3,.9,'crash image'),
           ('recovery',12.4,-.7,2.9,.9,'byte\nrecovery'),
           ('alternate',5.,-2.,2.3,.8,'pair / SMT')]
    for row in nodes:d.box(*row)
    for a,b in [('trace','producer'),('producer','certificate'),('certificate','consumer'),('trace','cut'),('cut','image'),('image','recovery')]:
        d.edge(a,b,'e','w')
    d.arrow(d.point('trace','n'),d.point('consumer','n'),via=[(1.4,2.35),(12.4,2.35)])
    d.arrow(d.point('trace','s'),d.point('alternate','w'),via=[(1.4,-2.)])
    for row in [('sqlite',1.4,-3.2,2.2,.8,'SQLite\ncommits'),
                ('tables',5.,-3.2,2.3,.8,'retained\ntables'),
                ('native',8.65,-3.2,2.3,.8,'native\nrecovery'),
                ('compare',12.4,-3.2,2.9,.8,'compare prefix\nand object bytes')]:
        d.box(*row)
    for a,b in [('sqlite','tables'),('tables','native'),('native','compare')]:
        d.edge(a,b,'e','w')
    d.edge('recovery','compare','s','n')
    d.arrow(d.point('sqlite','w'),d.point('trace','w'),
            via=[(-.55,-3.2),(-.55,0)],dashed=True)
    d.text(-.35,-1.15,'audit',ha='left',size=8)
    d.save(out,'evidence-paths')

def main():
    global FACE
    p=argparse.ArgumentParser(description='Faithful vector rendering of retirement relations and evidence flow.')
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--font',type=Path,required=True,help='Installed manuscript Libertine OpenType face')
    a=p.parse_args()
    if not a.font.is_file():raise ValueError('Manuscript font is missing')
    font_manager.fontManager.addfont(str(a.font))
    FACE=font_manager.FontProperties(fname=str(a.font))
    a.out.mkdir(parents=True,exist_ok=True)
    frontier(a.out);paths(a.out)
if __name__=='__main__':main()

