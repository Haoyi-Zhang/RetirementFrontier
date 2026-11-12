"""Draw storage relationships as text and native PDF vectors at column size."""
import argparse
import json
import math
from pathlib import Path
import sys

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.utils import ImageReader

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plots'))
import draw_write_ratios as v

W = 430.00462 * v.PT
INK = (.09, .13, .17)
MUTED = v.SLATE
RULE = (.75, .78, .81)
BLUE = v.BLUE
RUST = (.53, .26, .19)
PAPER = (1, 1, 1)
GREY = (.95, .96, .965)
PALE_BLUE = (.89, .93, .958)
ART = Path(__file__).with_name('artwork')


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
        self.illustrations = []

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

    def node(self, key, x, y, width, height, text, edge=MUTED, fill=PAPER,
             dashed=False):
        if key in self.nodes:
            raise ValueError(f'Duplicate node {key}')
        self.nodes[key] = (x-width/2,y-height/2,x+width/2,y+height/2)
        self.c.setStrokeColorRGB(*edge)
        self.c.setFillColorRGB(*fill)
        self.c.setLineWidth(.85)
        if dashed:
            self.c.setDash(3.2,2.5)
        self.c.roundRect(x-width/2,self.height-y-height/2,width,height,3,fill=1,stroke=1)
        if dashed:
            self.c.setDash()
        if text:
            self.label(x,y,text,11.5,colour=edge if dashed else INK,owner=key)

    def frame(self, key, x, y, width, height, paint, stroke=MUTED, fill=PAPER):
        """A structural glyph with reserved space, not a caption or connector."""
        self.node(key,x,y,width,height,None,stroke,fill)
        self.c.saveState()
        self.c.translate(x,self.height-y)
        paint(self.c)
        self.c.restoreState()

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

    def art(self,plate,key,x,y,w,h):
        image=ImageReader(str(ART/f'{plate}.png'))
        iw,ih=image.getSize()
        l,t,r,b=ASSETS[plate][key]
        if not (0<=l<r<=iw and 0<=t<b<=ih):
            raise ValueError(f'Illustration crop outside image: {plate}/{key}')
        factor=min(w/(r-l),h/(b-t))
        if 72/factor<430:
            raise ValueError(f'Insufficient effective resolution: {plate}/{key}')
        bw,bh=(r-l)*factor,(b-t)*factor
        left,upper=x+(w-bw)/2,y+(h-bh)/2
        self.illustrations.append((key,(left,upper,left+bw,upper+bh)))
        self.c.saveState()
        lower=self.height-upper-bh
        clip=self.c.beginPath()
        clip.rect(left,lower,bw,bh)
        self.c.clipPath(clip,stroke=0,fill=0)
        self.c.drawImage(image,left-l*factor,lower-(ih-b)*factor,
                         iw*factor,ih*factor,mask='auto')
        self.c.restoreState()

    def cross(self,x,y):
        self.line((x-3.8,y-3.8),(x+3.8,y+3.8),colour=RUST,weight=1.25)
        self.line((x+3.8,y-3.8),(x-3.8,y+3.8),colour=RUST,weight=1.25)

    def switch(self,key,x,y,direction):
        def contacts(c):
            c.setStrokeColorRGB(*BLUE)
            c.setLineWidth(1.15)
            c.line(0,7,12*direction,-4)
            for dx,dy,fill in ((0,7,PAPER),(-12,-4,BLUE if direction<0 else PAPER),
                               (12,-4,BLUE if direction>0 else PAPER)):
                c.setFillColorRGB(*fill)
                c.circle(dx,dy,2.65,stroke=1,fill=1)
        self.frame(key,x,y,47,28,contacts,BLUE,GREY)

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
        for i,(key,box) in enumerate(self.illustrations):
            if not contains(inside,box) or any(overlap(box,b) for _,b in self.illustrations[i+1:]):
                raise ValueError(f'{self.name}: overlapping or clipped artwork: {key}')
            if any(overlap(grow(box,1),b) for _,b,_ in self.words):
                raise ValueError(f'{self.name}: label overlaps artwork: {key}')
            obstacles.append((key,grow(box,-.25)))
        for p,q in self.lines:
            for label,box in obstacles:
                if intersects_segment(p,q,box):
                    raise ValueError(f'{self.name}: connector crosses {label}')
        self.c.save()


def native_range_case():
    source = Path(__file__).resolve().parents[1] / 'results/rocksdb-lifecycle/epochs.json'
    case, = (row for row in json.loads(source.read_text(encoding='utf-8'))['cases']
             if row['scope'] == 'range')
    before, after = case['before'], case['after']
    previous, current = set(before['files']), set(after['files'])
    edit, = case['edits']
    if (set(edit['add']) != current - previous or set(edit['remove']) != previous - current
            or edit['sequence'] != [before['sequence']] or before['sequence'] != after['sequence']
            or len(previous - current) != 3 or len(current - previous) != 1
            or len(previous & current) != 1):
        raise ValueError('Native overview no longer matches its decoded edit')
    return case


def native_listing(case):
    def ids(names):
        return ', '.join(str(int(Path(name).stem)) for name in sorted(names))
    before, after = case['before'], case['after']
    edit, = case['edits']
    return '\n'.join((
        f'Before   sequence={before["sequence"]}   live={{{ids(before["files"])}}}',
        f'Edit     remove={{{ids(edit["remove"])}}}   add={{{ids(edit["add"])}}}',
        f'After    sequence={after["sequence"]}   live={{{ids(after["files"])}}}',
        '',
    ))


def motivation(out):
    case = native_range_case()
    (out / 'native-compaction.txt').write_text(native_listing(case), encoding='ascii', newline='\n')
    before, after = case['before'], case['after']
    previous, current = set(before['files']), set(after['files'])
    old = ', '.join(str(int(Path(n).stem)) for n in sorted(previous - current))
    new, = (int(Path(n).stem) for n in current - previous)
    shared, = (int(Path(n).stem) for n in previous & current)
    ordered = case['outcomes']['ordered']
    bad = case['outcomes']['unguarded-retirement']['minimum']
    lost, = (int(Path(n).stem) for n in bad['missing'])
    if (bad['view'] != 'epoch:before' or ordered['bad_cuts'] != 0
            or {a for a, b in ordered['certificate_guards'] if b == 'epoch:after'}
               != {f'retire:{name}' for name in previous - current}):
        raise ValueError('Native overview does not match the guard or failure evidence')

    # Exact set membership and predecessor arrows; the graphic is not a time measurement.
    d = Figure(out, 'reuse-cut', 218)
    d.label(15, 15, '(a) Decoded RocksDB edit', 12.2, 'left')
    d.label(226, 15, '(b) Projected persistence order', 12.2, 'left')
    d.label(15, 39, f'Same client sequence: {before["sequence"]}', 10.8, 'left', MUTED)
    d.label(15, 65, 'SST', 11, 'left')
    d.label(126, 65, 'Before', 11)
    d.label(180, 65, 'After', 11)
    d.line((15, 76), (204, 76), colour=RULE, weight=.7)
    for y, name, flags in ((94, old, (True, False)),
                           (117, str(shared), (True, True)),
                           (140, str(new), (False, True))):
        d.label(15, y, name, 12, 'left')
        for x, present in zip((126, 180), flags):
            if present:
                d.dot(x, y, BLUE, 3.4)
            else:
                d.line((x-3, y), (x+3, y), colour=MUTED, weight=.8)
    d.dot(20, 166, BLUE, 3.4)
    d.label(31, 166, 'Required by the view', 10.5, 'left', MUTED)
    d.line((214, 37), (214, 190), colour=RULE, weight=.6)

    for key, y, label in (('create', 41, f'SST {new} present'),
                          ('publish', 94, 'New view durable'),
                          ('retire', 147, f'Unlink {old}')):
        d.node(key, 313, y, 147, 23, label, BLUE, PALE_BLUE if key == 'publish' else PAPER)
    for lo, hi, label in ((54, 80, 'P: publish'), (107, 133, 'D: retire')):
        d.line((313, lo), (313, hi), arrow=True, colour=BLUE, weight=1.15)
        d.label(327, (lo+hi)/2, label, 10.2, 'left', BLUE)
    d.label(313, 169, 'Arrows: durability prerequisites', 10.2, colour=MUTED)
    d.label(313, 188, 'For each unlink: T = before < F = after', 10.8, colour=BLUE)
    d.line((15, 198), (415, 198), colour=RULE, weight=.7)
    d.label(15, 210, f'Both edges: {ordered["legal_cuts"]} cuts, no missing SST', 10.2, 'left', BLUE)
    d.label(230, 210, f'Without D: old view can miss SST {lost}', 10.2, 'left', RUST)
    d.save()


def frontier(out):
    d=Figure(out,'retirement-frontier',170)
    xs=(49,157,273,381)
    d.frame('allocation',49,58,33,22,lambda c: None,BLUE,PALE_BLUE)
    d.node('root-r',157,58,38,25,'r',MUTED,GREY)
    d.node('root-q',273,58,38,25,'q',BLUE,PALE_BLUE)
    d.node('writer-w',381,58,40,25,'w',MUTED,GREY)
    for x,caption in zip(xs,('Allocation a','Root r','Root q','Writer w')):
        d.label(x,103,caption,11.5,colour=BLUE if x==273 else INK)
    d.line((15,89),(414,89),colour=RULE,weight=.75)
    for x in xs:
        d.dot(x,89,BLUE if x==273 else INK,2.35)
    d.line((157,42),(157,18),(49,18),(49,41),dashed=True,arrow=True)
    d.line((273,42),(273,18),(381,18),(381,42),arrow=True,colour=BLUE,weight=1.1)
    d.label(215,121,'T(w) = r  <  F(w) = q',12)
    d.label(215,138,'Publication order is strict even if client prefixes tie',11.5)
    d.line((87,160),(110,160),dashed=True,arrow=True)
    d.label(118,160,'Allocation reference',11.5,'left')
    d.line((243,160),(266,160),arrow=True,colour=BLUE,weight=1.1)
    d.label(274,160,'Persistence order',11.5,'left')
    d.save()


def certificate(out):
    d=Figure(out,'certificate-check',144)
    d.art('evidence-symbols','trace',13,71,75,43)
    d.label(50.5,126,'Admitted history',11.5)
    d.node('analysis',173,35,77,34,'Frontier\nanalysis',BLUE,PALE_BLUE)
    d.art('evidence-symbols','packet',237,10,53,49)
    d.label(263.5,71,'Positive certificate',11.5)
    def inspect(c):
        c.setStrokeColorRGB(*BLUE)
        c.setLineWidth(1.05)
        c.rect(-17,-7,9,9,fill=0,stroke=1)
        c.rect(-8,-7,9,9,fill=0,stroke=1)
        c.setFillColorRGB(*PAPER)
        c.circle(8,5,6.4,fill=1,stroke=1)
        c.line(12.5,.5,18,-5)
    d.frame('verifier',371,94,54,35,inspect,BLUE,GREY)
    d.label(371,132,'Independent verifier',11.5)
    d.line((91,94),(331,94),arrow=True)
    d.line((111,94),(111,35),(131,35),arrow=True,colour=BLUE)
    d.line((213,35),(232,35),arrow=True,colour=BLUE)
    d.line((295,35),(371,35),(371,65),arrow=True,colour=BLUE)
    d.dot(111,94,INK,2.1)
    d.save()


def selector(out):
    d=Figure(out,'selector-order',163)
    d.art('publication-symbols','rail',11,11,78,46)
    d.art('evidence-symbols','manifest',137,9,44,52)
    d.switch('new-selector',269,35,1)
    def reused(c):
        c.setStrokeColorRGB(*BLUE)
        c.setFillColorRGB(*PALE_BLUE)
        c.setLineWidth(.85)
        c.rect(-12,-8,24,16,stroke=1,fill=1)
    d.frame('reused',378,35,56,30,reused,MUTED,GREY)
    d.label(378,35,'C',10.5,owner='reused')
    for x, label in ((50,'Write B, fresh slot'),(159,'Prepare manifest B'),
                     (269,'Select B'),(378,'Reuse A\'s slot')):
        d.label(x,71,label,10.5)
    for start, end in ((94,133),(185,236)):
        d.line((start,35),(end,35),arrow=True)
    d.label(324,19,'Only then',10.5,colour=BLUE)
    d.line((302,35),(346,35),arrow=True,colour=BLUE,weight=1.42)
    # An alternate cut: the selector still resolves its manifest; the
    # reference inside that manifest, not the selector link, is broken.
    d.switch('old-selector',50,116,-1)
    d.art('evidence-symbols','manifest',136,90,42,50)
    d.label(50,150,'Still selects A',10.5,colour=RUST)
    d.label(157,150,'Manifest A',10.5)
    d.line((84,116),(131,116),arrow=True)
    d.label(224,100,'Reference to A',10.5,colour=RUST)
    d.line((184,116),(267,116),arrow=True,dashed=True,colour=RUST,weight=1.15)
    d.cross(274,116)
    d.label(274,135,'A missing',10.5,colour=RUST)
    d.art('storage-symbols','slot',308,95,101,42)
    d.label(358.5,150,'B in A\'s slot',10.5,colour=RUST)
    d.save()


def sqlite(out):
    d=Figure(out,'evidence-paths',160)
    d.art('publication-symbols','database',20,60,57,51)
    d.label(48.5,130,'Committed\nSQLite state',11.5)
    def projection(c):
        c.setStrokeColorRGB(*BLUE)
        c.setFillColorRGB(*PALE_BLUE)
        c.setLineWidth(.75)
        c.rect(-14,-6,14,12,fill=1,stroke=0)
        for x in (-14,0,14):
            c.line(x,-8,x,8)
    d.frame('projection',180,33,56,20,projection,BLUE,PAPER)
    d.label(180,65,'Audit projection',11.5)
    def records(c):
        c.setLineWidth(.65)
        c.setStrokeColorRGB(*MUTED)
        for y in (7,0,-7):
            c.line(-22,y,22,y)
        for x in (-11,9):
            c.line(x,-13,x,13)
    d.frame('records',180,119,50,30,records,MUTED,GREY)
    d.label(180,146,'Retained records',11.5)
    d.art('publication-symbols','compare',351,59,68,50)
    d.label(385,122,'Compare results',11.5)
    d.label(264,20,'Model recovery',11.5,colour=BLUE)
    d.label(264,107,'Native recovery',11.5,colour=MUTED)
    d.line((83,85),(106,85))
    d.dot(106,85,INK,2)
    d.line((106,85),(106,33),(140,33),colour=BLUE,arrow=True)
    d.line((106,85),(106,119),(152,119),colour=MUTED,arrow=True)
    d.line((219,33),(316,33),(316,73),(346,73),colour=BLUE,arrow=True)
    d.line((208,119),(316,119),(316,96),(346,96),colour=MUTED,arrow=True)
    d.save()


def main():
    global ASSETS
    ASSETS=json.loads((ART/'regions.json').read_text(encoding='utf-8'))
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
