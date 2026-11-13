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
             dashed=False, radius=3):
        if key in self.nodes:
            raise ValueError(f'Duplicate node {key}')
        self.nodes[key] = (x-width/2,y-height/2,x+width/2,y+height/2)
        self.c.setStrokeColorRGB(*edge)
        self.c.setFillColorRGB(*fill)
        self.c.setLineWidth(.85)
        if dashed:
            self.c.setDash(3.2,2.5)
        if radius:
            self.c.roundRect(x-width/2,self.height-y-height/2,width,height,radius,fill=1,stroke=1)
        else:
            self.c.rect(x-width/2,self.height-y-height/2,width,height,fill=1,stroke=1)
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

    def ring(self,x,y,colour=BLUE,radius=4):
        self.c.setStrokeColorRGB(*colour)
        self.c.setLineWidth(1.15)
        self.c.circle(x,self.height-y,radius,fill=0,stroke=1)

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
    def number(name):
        return str(int(Path(name).stem))
    ordered = case['outcomes']['ordered']
    bad = case['outcomes']['unguarded-retirement']['minimum']
    if (bad['view'] != 'epoch:before' or ordered['bad_cuts'] != 0
            or {a for a, b in ordered['certificate_guards'] if b == 'epoch:after'}
               != {f'retire:{name}' for name in previous - current}):
        raise ValueError('Native overview does not match the guard or failure evidence')

    missing, = bad['missing']
    if missing not in previous - current or f'retire:{missing}' not in bad['cut']:
        raise ValueError('The projected bad cut must retire an old-only table')
    d = Figure(out, 'reuse-cut', 278)
    d.label(15, 15, '(a) Observed MANIFEST references', 12, 'left')
    d.label(414, 15, f'Client sequence: {before["sequence"]} in both views', 10.2, 'right', MUTED)
    d.art('metadata-symbols','manifest',16,45,18,22)
    d.label(44, 55, 'Before edit', 11.3, 'left')
    d.art('metadata-symbols','manifest',16,119,18,22)
    d.label(44, 130, 'After edit', 11.3, 'left', BLUE)
    d.label(104, 92, 'SST files', 10.3, 'right', MUTED)
    names = sorted(previous - current) + sorted(previous & current) + sorted(current - previous)
    columns = (151, 210, 269, 328, 387)
    positions = dict(zip(names, columns, strict=True))
    for files, y, port, color in ((previous,55,82,INK), (current,130,102,BLUE)):
        targets = sorted(positions[name] for name in files)
        d.line((112,y),(targets[-1],y),colour=color,weight=.84)
        for x in targets:
            d.line((x,y),(x,port),colour=color,weight=.84)
            if x != targets[-1]:
                d.dot(x,y,color,1.9)
    for x, name in zip(columns, names, strict=True):
        edge=BLUE if name in current else INK
        fill=PALE_BLUE if name in previous & current else PAPER
        d.node(name,x,92,34,20,number(name),edge,fill,radius=0)
    d.label(328,147,'Shared SST',10.1,colour=BLUE)
    d.label(15, 170, f'(b) Projected recovery if SST {number(missing)} is retired', 12, 'left')
    d.label(15, 199, 'No retirement edge', 10.6, 'left', RUST)
    d.label(184, 199, 'Old root still selectable', 11)
    d.line((243,199),(268,199),colour=RUST,dashed=True,arrow=True)
    d.cross(277,199)
    d.label(292, 199, f'SST {number(missing)} missing', 11, 'left', RUST)
    d.label(15, 233, 'Guarded retirement', 10.6, 'left', BLUE)
    d.label(184, 233, 'Unlink forces new root', 11, colour=BLUE)
    d.line((243,233),(268,233),colour=BLUE,arrow=True)
    d.ring(277,233,BLUE,4.2)
    d.label(292, 233, f'SSTs {" and ".join(map(number, sorted(current)))} present', 11, 'left', BLUE)
    d.label(184, 256, 'Last user < forced root', 10.6, colour=MUTED)
    d.save()


def frontier(out):
    d=Figure(out,'retirement-frontier',137)
    xs=(51,153,272,380)
    d.line((29,74),(405,74),colour=RULE,weight=.8,arrow=True)
    for x,caption in zip(xs,('Allocate a','Publish r','Publish q','Write w')):
        d.dot(x,74,BLUE if x==272 else INK,3)
        d.label(x,91,caption,11.8,colour=BLUE if x==272 else INK)
    d.line((153,67),(153,43),(51,43),(51,67),dashed=True,arrow=True)
    d.label(102,30,'r references a',11,colour=MUTED)
    d.line((272,67),(272,43),(380,43),(380,67),arrow=True,colour=BLUE,weight=1.15)
    d.label(326,30,'q must persist before w',11,colour=BLUE)
    d.label(153,117,'T(w) = r',12)
    d.label(272,117,'F(w) = q',12,colour=BLUE)
    d.label(212,117,'<',13)
    d.save()


def certificate(out):
    d=Figure(out,'certificate-check',148)
    d.label(51,58,'Admitted history',11.8)
    d.line((102,58),(115,58))
    d.dot(115,58,INK,2.2)
    d.line((115,58),(115,28),(148,28),arrow=True,colour=BLUE)
    d.dot(152,28,BLUE,2.3)
    d.label(182,13,'Compute frontier',11.1,colour=BLUE)
    d.line((156,28),(243,28),arrow=True,colour=BLUE)
    d.art('metadata-symbols','records',247,10,33,35)
    d.label(263,56,'Witnesses',10.7)
    d.line((286,28),(328,28),(328,48),arrow=True,colour=BLUE)
    d.line((115,58),(115,90),(294,90),(294,67),(309,67),arrow=True)
    d.label(206,105,'Original history',10.9,colour=MUTED)
    d.ring(328,67,BLUE,12)
    d.label(328,67,'?',12)
    d.label(328,114,'Independent verifier',11.3)
    d.line((343,67),(357,67),(357,51),(371,51),arrow=True,colour=BLUE)
    d.line((357,67),(357,88),(371,88),arrow=True,colour=RUST,dashed=True)
    d.dot(357,67,INK,1.9)
    d.ring(380,51,BLUE,4)
    d.cross(380,88)
    d.label(390,51,'Accept',10.1,'left',BLUE)
    d.label(390,88,'Reject',10.1,'left',RUST)
    d.save()


def minimum_witness(out):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
    from witness_examples import shared_comparison

    evidence = shared_comparison()
    d = Figure(out, 'minimum-witness', 126)
    d.label(16, 20, 'Durable events', 11.5, 'left')
    columns = (172, 215, 258, 301, 344, 387)
    for i, (x, name) in enumerate(zip(columns, ('chunk', 'manifest', 'root', 'manifest', 'root', 'free'))):
        d.label(x, 15, str(i), 11.7)
        d.label(x, 31, name, 10.1, colour=MUTED)
    d.line((156, 43), (405, 43), colour=RULE, weight=.75)

    def ring(x, y):
        d.c.setStrokeColorRGB(*BLUE)
        d.c.setLineWidth(1.5)
        d.c.circle(x, d.height-y, 4.7, fill=0, stroke=1)

    rows = ((f'Frontier-root witness ({len(evidence["frontier_cut"])})', evidence['frontier_cut'], 61),
            (f'Minimum witness ({len(evidence["minimum_cut"])})', evidence['minimum_cut'], 88))
    for (name, cut, y), selected in zip(rows, evidence['selected_roots'], strict=True):
        d.label(16, y, name, 11.4, 'left', BLUE if y == 88 else INK)
        for i, x in enumerate(columns):
            if i == selected:
                ring(x, y)
            elif i in cut:
                d.dot(x, y, INK, 3.3)
            else:
                d.line((x-3.1, y), (x+3.1, y), colour=MUTED, weight=.9)
    ring(121, 113)
    d.label(134, 113, 'Selected root', 10.4, 'left')
    d.dot(225, 113, INK, 3.3)
    d.label(236, 113, 'Other durable event', 10.4, 'left')
    d.line((347, 113), (353, 113), colour=MUTED, weight=.9)
    d.label(360, 113, 'Absent', 10.4, 'left')
    d.save()


def selector(out):
    d=Figure(out,'selector-order',163)
    d.art('publication-symbols','rail',25,16,52,33)
    d.art('evidence-symbols','manifest',146,13,28,42)
    d.label(269,35,'B',13,colour=BLUE)
    d.ring(269,35,BLUE,13)
    d.node('reused',378,35,31,22,'C',BLUE,PALE_BLUE,radius=0)
    for x, label in ((50,'Write B, fresh slot'),(159,'Prepare manifest B'),
                     (269,'Select B'),(378,'Reuse A\'s slot')):
        d.label(x,71,label,10.5)
    for start, end in ((94,133),(185,236)):
        d.line((start,35),(end,35),arrow=True)
    d.label(324,19,'Only then',10.5,colour=BLUE)
    d.line((302,35),(346,35),arrow=True,colour=BLUE,weight=1.42)
    # An alternate cut: the selector still resolves its manifest; the
    # reference inside that manifest, not the selector link, is broken.
    d.label(50,116,'A',13,colour=RUST)
    d.ring(50,116,RUST,13)
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
    d=Figure(out,'evidence-paths',138)
    d.art('metadata-symbols','database',17,46,37,43)
    d.label(36,110,'Committed\nSQLite state',11.1)
    d.line((58,67),(86,67))
    d.dot(86,67,INK,2.3)
    d.line((86,67),(86,35),(110,35),colour=BLUE,arrow=True)
    d.line((86,67),(86,99),(110,99),arrow=True)
    d.label(155,35,'Audit projection',11.2,colour=BLUE)
    d.label(155,99,'Retained records',11.2)
    d.line((199,35),(223,35),colour=BLUE,arrow=True)
    d.line((199,99),(223,99),arrow=True)
    d.label(268,35,'Model recovery',11.2,colour=BLUE)
    d.label(268,99,'Native recovery',11.2)
    d.line((313,35),(357,35),(357,52),colour=BLUE,arrow=True)
    d.line((313,99),(357,99),(357,82),arrow=True)
    d.ring(357,67,BLUE,11)
    d.label(357,67,'?',12)
    d.label(415,53,'Compare',10.5,'right')
    d.label(418,124,'Outcome; if successful: root, prefix, bytes',10.2,'right',MUTED)
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
    for draw in (motivation,frontier,certificate,minimum_witness,selector,sqlite):
        draw(args.out)


if __name__=='__main__':
    main()
