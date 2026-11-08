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
INK = (.10, .16, .21)
MUTED = (.31, .37, .42)
RULE = (.77, .81, .84)
BLUE = (.11, .34, .49)
TEAL = (.10, .40, .35)
RUST = (.60, .25, .12)
PAPER = (1, 1, 1)
GREY = (.965, .972, .977)
PALE_BLUE = (.928, .963, .982)
PALE_RUST = (.989, .950, .918)


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
            left = x-advance/2 if align=='center' else x
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
    d=Figure(out,'reuse-cut',111)
    for left, heading, root, verdict, colour in (
        (0,'(a) Older root selected','Older root','Recovery fails: old allocation missing',RUST),
        (224,'(b) Retiring root selected','Retiring root','Old allocation is not referenced',TEAL),
    ):
        d.label(left+3,13,heading,12,'left')
        d.line((left+3,25),(left+199,25),colour=RULE,weight=.65)
        d.label(left+47,40,'Selected publication',10.5,colour=MUTED)
        d.label(left+155,40,'Overwritten slot',10.5,colour=MUTED)
        d.node(f'root{left}',left+47,63,83,25,root)
        d.node(f'slot{left}',left+155,63,88,25,'New incarnation',RUST,PALE_RUST)
        d.label(left+101,99,verdict,11,colour=colour)
    d.line((88.5,63),(111,63),colour=RUST,dashed=True,arrow=True,weight=1.1)
    d.save()


def frontier(out):
    d=Figure(out,'retirement-frontier',145)
    xs=(44,148,265,380)
    d.line((15,65),(411,65),colour=RULE)
    for x,title,detail,colour in zip(xs,('Allocation a','Root r','Root q','Writer w'),
            ('Old incarnation','Last root using a','Retires a','Overwrites the slot'),
            (MUTED,INK,BLUE,RUST)):
        d.dot(x,65,colour)
        d.label(x,84,title,11.5,colour=colour)
        d.label(x,103,detail,10.5,colour=MUTED)
    d.label(96,13,'Root reference',11)
    d.label(322.5,13,'Persistence dependency',11,colour=BLUE)
    d.line((148,61),(148,33),(44,33),(44,61),colour=MUTED,dashed=True,arrow=True)
    d.line((265,61),(265,33),(380,33),(380,61),colour=BLUE,arrow=True,weight=1.2)
    d.label(214,133,'T(w) = r   <   F(w) = q',13)
    d.save()


def certificate(out):
    d=Figure(out,'certificate-check',125)
    d.node('history',53,48,95,35,'Admitted\nhistory',MUTED,GREY)
    d.node('producer',189,48,94,35,'Frontier\nanalysis')
    d.node('verifier',369,48,107,35,'Separate\nverifier',BLUE,PALE_BLUE)
    d.line((100.5,48),(142,48),arrow=True)
    d.line((236,48),(315.5,48),arrow=True,colour=BLUE,weight=1.1)
    d.label(275,21,'Positive certificate',10.5,colour=BLUE)
    d.line((53,65.5),(53,92),(369,92),(369,65.5),arrow=True)
    d.label(210,111,'Original history',11)
    d.save()


def selector(out):
    d=Figure(out,'selector-order',177)
    xs=(50,160,270,380)
    for heading,y,steps,first,last,shade in (
        ('(a) Ordered protocol',30,('Write B to\na fresh slot','Prepare B\nmanifest','Select B','Reuse old slot'),
         'A remains recoverable','B is selected before reuse',TEAL),
        ('(b) Unsafe control',120,('Overwrite A\nwith B','Prepare B\nmanifest','Select B'),
         'Recovery of A fails','B is recoverable',RUST),
    ):
        d.label(4,y-18,heading,12,'left')
        for i,(x,step) in enumerate(zip(xs,steps)):
            colour=BLUE if i==2 else shade if i==0 and y>100 else MUTED
            d.dot(x,y,colour)
            d.label(x,y+21,step,11)
            if i:
                d.line((xs[i-1]+4,y),(x-4,y),arrow=True,colour=MUTED)
        d.label(105,y+49,first,11,colour=shade)
        d.label(325 if len(steps)==4 else 270,y+49,last,11,colour=TEAL)
    d.line((4,89),(424,89),colour=RULE,weight=.6)
    d.save()


def sqlite(out):
    d=Figure(out,'evidence-paths',162)
    d.label(231,9,'Event-model path',11.5,colour=BLUE)
    d.label(231,150,'Native SQLite path',11.5,colour=TEAL)
    d.node('commit',52,77,94,37,'Committed\nSQLite state',MUTED,GREY)
    d.node('audit',174,38,89,34,'Audit\nprojection')
    d.node('model',277,38,85,34,'Model\nrecovery',BLUE,PALE_BLUE)
    d.node('records',174,116,89,34,'Retained\nrecords')
    d.node('native',277,116,85,34,'Native\nrecovery',TEAL,GREY)
    d.node('compare',380,77,91,38,'Compare\nrecovery results')
    d.line((99,77),(116,77))
    d.line((116,77),(116,38),(129.5,38),arrow=True,colour=BLUE)
    d.line((116,77),(116,116),(129.5,116),arrow=True,colour=TEAL)
    d.line((218.5,38),(234.5,38),arrow=True,colour=BLUE)
    d.line((218.5,116),(234.5,116),arrow=True,colour=TEAL)
    d.line((319.5,38),(380,38),(380,58),arrow=True,colour=BLUE)
    d.line((319.5,116),(380,116),(380,96),arrow=True,colour=TEAL)
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
