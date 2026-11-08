"""Draw storage relationships as text and native PDF vectors at column size."""
import argparse
import math
from pathlib import Path
import sys

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen.canvas import Canvas

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plots'))
import draw_write_ratios as v

W = 430.00462 * v.PT
INK = (.09, .13, .17)
MUTED = (.29, .34, .38)
RULE = (.73, .77, .80)
BLUE = (.10, .29, .43)
TEAL = (.13, .35, .32)
RUST = (.58, .24, .12)
PAPER = (1, 1, 1)
GREY = (.965, .972, .977)
PALE_BLUE = (.928, .963, .982)


def grow(box, amount):
    l, t, r, b = box
    return l-amount, t-amount, r+amount, b+amount


def overlap(a, b):
    return max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3])


def intersects_segment(p, q, box):
    """Open-box clipping; intentional endpoints may touch a node border."""
    lo, hi = 0., 1.
    for x, y, lower, upper in ((p[0],q[0],box[0],box[2]),
                                (p[1],q[1],box[1],box[3])):
        if x == y:
            if not lower < x < upper:
                return False
        else:
            a, b = sorted(((lower-x)/(y-x), (upper-x)/(y-x)))
            lo, hi = max(a, lo), min(b, hi)
            if lo >= hi:
                return False
    return lo < hi


class Figure:
    def __init__(self, out, name, height):
        self.height = height
        self.name = name
        self.c = Canvas(str(out / f'{name}.pdf'), pagesize=(W,height),
                        invariant=1, initialFontName=v.FONT, initialFontSize=11)
        self.c.setCreator('Python ReportLab')
        self.words, self.nodes, self.lines = [], {}, []

    def label(self, x, y, text, size=11, align='center', colour=INK, owner=None):
        if not all(32 <= ord(ch) <= 126 for ch in text.replace('\n','')):
            raise ValueError('Unsupported figure glyph')
        rows = text.split('\n')
        ascent, descent = pdfmetrics.getAscentDescent(v.FONT, size)
        leading = size * 1.13
        first = self.height-y+(len(rows)-1)*leading/2-(ascent+descent)/2
        self.c.setFont(v.FONT,size)
        self.c.setFillColorRGB(*colour)
        for i, row in enumerate(rows):
            advance = pdfmetrics.stringWidth(row,v.FONT,size)
            left = x-advance/2 if align=='center' else x-advance if align=='right' else x
            base = first-i*leading
            self.words.append((row,(left,self.height-base-ascent,
                                    left+advance,self.height-base-descent),owner))
            self.c.drawString(left,base,row)

    def node(self, key, x, y, width, height, text, edge=MUTED, fill=PAPER):
        if key in self.nodes:
            raise ValueError(f'Duplicate node {key}')
        self.nodes[key] = (x-width/2,y-height/2,x+width/2,y+height/2)
        self.c.setStrokeColorRGB(*edge)
        self.c.setFillColorRGB(*fill)
        self.c.setLineWidth(.85)
        self.c.rect(x-width/2,self.height-y-height/2,width,height,fill=1,stroke=1)
        self.label(x,y,text,owner=key)

    def line(self, *points, colour=MUTED, weight=.9, dashed=False, arrow=False):
        if len(points)<2:
            raise ValueError('Line requires two points')
        self.lines.extend(zip(points,points[1:]))
        self.c.setLineWidth(weight)
        self.c.setStrokeColorRGB(*colour)
        self.c.setDash(3.2,2.5) if dashed else self.c.setDash()
        def path(coords):
            p=self.c.beginPath()
            p.moveTo(coords[0][0],self.height-coords[0][1])
            for x,y in coords[1:]:
                p.lineTo(x,self.height-y)
            return p
        self.c.drawPath(path(points))
        self.c.setDash()
        if arrow:
            p,q=points[-2:]
            d=math.hypot(q[0]-p[0],q[1]-p[1])
            if d<8:
                raise ValueError(f'Arrow has insufficient run in {self.name}')
            ux,uy=(q[0]-p[0])/d,(q[1]-p[1])/d
            x,y=q[0]-6*ux,q[1]-6*uy
            tri=(q,(x-2.5*uy,y+2.5*ux),(x+2.5*uy,y-2.5*ux),q)
            self.lines.extend(zip(tri,tri[1:]))
            self.c.setFillColorRGB(*colour)
            self.c.drawPath(path(tri),fill=1,stroke=0)

    def dot(self,x,y,colour=INK,radius=3.1):
        self.c.setFillColorRGB(*colour)
        self.c.circle(x,self.height-y,radius,fill=1,stroke=0)

    def save(self):
        inside = (0,0,W,self.height)
        def contains(a,b):
            return a[0] <= b[0] and a[1] <= b[1] and b[2] <= a[2] and b[3] <= a[3]
        for i,(key,box) in enumerate(self.nodes.items()):
            if not contains(inside,box) or any(overlap(box,b) for b in list(self.nodes.values())[i+1:]):
                raise ValueError(f'{self.name}: intersecting or clipped node {key}')
        for i,(word,b,key) in enumerate(self.words):
            if not contains(inside,b):
                raise ValueError(f'{self.name}: clipped label {word}')
            if key and not contains(grow(self.nodes[key],-3.6),b):
                raise ValueError(f'{self.name}: insufficient padding for {word}')
            if any(overlap(grow(b,.65), bb) for _,bb,_ in self.words[i+1:]):
                raise ValueError(f'{self.name}: overlapping labels near {word}')
            if any(k!=key and overlap(grow(b,1),bb) for k,bb in self.nodes.items()):
                raise ValueError(f'{self.name}: label overlaps an unrelated node: {word}')
        obstacles=[(w,grow(b,1.8)) for w,b,_ in self.words]
        obstacles += [(k,grow(b,-.38)) for k,b in self.nodes.items()]
        for p,q in self.lines:
            for label,box in obstacles:
                if intersects_segment(p,q,box):
                    raise ValueError(f'{self.name}: connector crosses {label}')
        self.c.save()


def motivation(out):
    d=Figure(out,'reuse-cut',145)
    for x,head in ((6,'Selected publication'),(151,'Same overwritten slot'),(291,'Effect of reuse')):
        d.label(x,13,head,11,'left')
    d.line((6,25),(422,25),colour=MUTED,weight=.75)
    d.line((6,87),(422,87),colour=RULE,weight=.6)
    for y,root,need,result,detail,colour in (
        (54,'(a) Older root','Still names the old allocation','Recovery fails','Required bytes are absent',RUST),
        (114,'(b) Retiring root','Does not name the allocation','No stale reference','The old bytes are not needed',TEAL),
    ):
        d.label(6,y-6,root,12,'left')
        d.label(6,y+11,need,10.5,'left',MUTED)
        d.node(f'slot{y}',202,y,102,30,'New incarnation',MUTED,GREY)
        d.label(291,y-6,result,12,'left',colour)
        d.label(291,y+11,detail,10.5,'left',MUTED)
    d.line((110,53),(151,53),colour=RUST,dashed=True,arrow=True,weight=1.15)
    d.save()


def frontier(out):
    d=Figure(out,'retirement-frontier',147)
    xs=(43,145,266,381)
    d.line((15,63),(410,63),colour=RULE)
    for x,title,detail,colour in zip(xs,('Allocation a','Root r','Root q','Writer w'),
            ('Old incarnation','Last root using a','Retires a','Overwrites the slot'),
            (MUTED,INK,BLUE,RUST)):
        d.dot(x,63,colour)
        d.label(x,80,title,12,colour=colour)
        d.label(x,97,detail,10.5,colour=MUTED)
    d.label(94,13,'r refers to a',11)
    d.label(323.5,13,'Durable w forces durable q',11,colour=BLUE)
    d.line((145,59),(145,33),(43,33),(43,59),colour=MUTED,dashed=True,arrow=True)
    d.line((266,59),(266,33),(381,33),(381,59),colour=BLUE,arrow=True,weight=1.15)
    for x,expr,colour in ((145,'T(w) = r',INK),(266,'F(w) = q',BLUE)):
        d.line((x,108),(x,118),colour=RULE,weight=.75)
        d.label(x,132,expr,13,colour=colour)
    d.label(205.5,132,'<',13)
    d.save()


def certificate(out):
    d=Figure(out,'certificate-check',146)
    d.node('history',53,77,100,39,'Admitted\nhistory',MUTED,GREY)
    d.node('producer',199,37,92,35,'Frontier\nanalysis')
    d.node('verifier',384,78.5,76,55,'Separate\nverifier',BLUE,PALE_BLUE)
    d.line((103,77),(123,77))
    d.line((123,77),(123,37),(153,37),arrow=True)
    d.line((245,37),(331,37),(331,66),(346,66),arrow=True,colour=BLUE,weight=1.15)
    d.label(288,11,'Positive certificate',10.5,colour=BLUE)
    d.line((123,77),(123,114),(331,114),(331,91),(346,91),arrow=True)
    d.label(228,132,'Original history',11)
    d.save()


def selector(out):
    d=Figure(out,'selector-order',188)
    xs=(124,213,302,390)
    for i,x in enumerate(xs,1):
        d.label(x,14,f'Step {i}',11,colour=MUTED)
    d.line((5,26),(423,26),colour=MUTED,weight=.75)
    d.line((5,108),(423,108),colour=RULE,weight=.6)
    for heading,y,steps,first,last,shade in (
        ('(a) Ordered',52,('Write B to\nfresh slot','Prepare B\nmanifest','Select B','Reuse old slot'),
         'A recoverable','B recoverable',TEAL),
        ('(b) Unsafe',133,('Overwrite A\nwith B','Prepare B\nmanifest','Select B','No further step'),
         'A unrecoverable','B recoverable',RUST),
    ):
        d.label(5,y,heading,11.5,'left')
        for i,(x,step) in enumerate(zip(xs,steps)):
            d.label(x,y,step,11,colour=(BLUE if i==2 else MUTED if y>100 and i==3 else INK))
        for l,r,label,colour in (
            (89,247,first,shade),
            (267,425,last,TEAL) if y<100 else (267,337,last,TEAL),
        ):
            d.line((l,y+22),(r,y+22),colour=colour,weight=1.1)
            d.label((l+r)/2,y+39,label,11,colour=colour)
    d.save()


def sqlite(out):
    d=Figure(out,'evidence-paths',177)
    d.node('commit',53,93,100,38,'Committed\nSQLite state',MUTED,GREY)
    d.node('compare',384,93,84,47,'Compare\nrecovery results')
    d.line((103,93),(123,93))
    d.label(128,16,'Event-model path',11.5,'left',BLUE)
    d.label(128,164,'Native SQLite path',11.5,'left',TEAL)
    for y,ty,port,first,second,colour in (
        (57,39,78,'Audit projection','Model recovery',BLUE),
        (128,144,108,'Retained records','Native recovery',TEAL),
    ):
        d.label(178,ty,first,11)
        d.label(269,ty,second,11)
        for x in (178,269):
            d.dot(x,y,colour)
        d.line((123,93),(123,y),(174,y),arrow=True,colour=colour)
        d.line((182,y),(265,y),arrow=True,colour=colour)
        d.line((273,y),(330,y),(330,port),(342,port),arrow=True,colour=colour)
    d.save()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--font-root',type=Path,required=True)
    args=parser.parse_args()
    v.register_font(args.font_root)
    args.out.mkdir(parents=True,exist_ok=True)
    for draw in (motivation,frontier,certificate,selector,sqlite):
        draw(args.out)


if __name__=='__main__':
    main()
