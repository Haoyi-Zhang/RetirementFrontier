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
    if (missing not in previous - current or f'retire:{missing}' not in bad['cut']
            or not {f'available:{name}' for name in previous} <= set(bad['cut'])
            or any(f'retire:{name}' in bad['cut'] for name in previous - {missing})):
        raise ValueError('The projected bad cut must retire an old-only table')
    d = Figure(out, 'reuse-cut', 278)
    shared = previous & current
    names = sorted(previous - current) + sorted(shared) + sorted(current - previous)
    positions = dict(zip(names, (145,193,241,289,337), strict=True))

    def file_marks(files, y, unavailable=None):
        # A repeated number denotes the same SST, not another copy.
        for name in sorted(files, key=positions.__getitem__):
            x = positions[name]
            if name == unavailable:
                d.cross(x-12,y)
                d.label(x,y,number(name),12,colour=RUST)
            elif name in shared:
                d.node(f'shared-{y}',x,y,29,21,number(name),BLUE,PALE_BLUE,radius=0)
            else:
                d.label(x,y,number(name),12)

    d.label(16,25,'(a) Observed MANIFEST references',12,'left')
    d.label(412,25,f'Client sequence {before["sequence"]} in both views',10.8,'right',MUTED)
    d.label(16,75,'Before edit',11.6,'left')
    d.label(16,111,'After edit',11.6,'left')
    file_marks(previous,75)
    file_marks(current,111)
    d.label(289,132,'Shared SST',10.7,colour=BLUE)

    d.label(16,164,f'(b) Projected recovery after unlinking SST {number(missing)}',12,'left')
    d.label(16,201,'No retirement edge',11.6,'left')
    d.label(16,216,'Old root selectable',10.7,'left',MUTED)
    d.label(16,244,'Retirement edge',11.6,'left')
    d.label(16,259,'New root forced',10.7,'left',MUTED)
    file_marks(previous,203,unavailable=missing)
    file_marks(current,246)
    d.label(145,223,'Missing',10.7,colour=RUST)
    d.label(410,203,'Recovery fails',10.8,'right',RUST)
    d.label(410,246,'Recoverable',10.8,'right',BLUE)
    # Grouping in this overview is by alignment and whitespace alone.
    if any(math.dist(a,b) > 12 for a,b in d.lines):
        raise ValueError('Overview must not contain a long rule or connector')
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
    d.label(20,67,'Admitted\nhistory',11.8,'left')
    d.label(110,24,'Producer',11.6,'left')
    d.label(315,24,'Independent verifier',11.6)
    d.label(109,46,'Compute frontier',11.4,'left',BLUE)
    d.label(109,111,'Derive canonical rows',11.3,'left')
    d.line((77,59),(100,46),arrow=True,colour=BLUE)
    d.line((77,76),(100,109),arrow=True)
    d.line((203,46),(221,46),arrow=True,colour=BLUE)
    d.line((218,111),(234,111),arrow=True)
    d.art('certificate-glyph','packet',229,29,26,27)
    d.label(242,66,'Supplied rows',10.6,colour=BLUE)
    d.label(268,111,'Required rows',10.6)
    d.line((260,46),(283,59),arrow=True,colour=BLUE)
    d.line((292,101),(299,86),arrow=True)
    d.label(311,71,'Compare\nrows',11.2)
    d.line((337,65),(357,53),arrow=True,colour=BLUE)
    d.line((337,79),(357,94),arrow=True,colour=RUST,dashed=True)
    d.ring(365,50,BLUE,3.5)
    d.cross(365,98)
    d.label(374,50,'Accept',10.7,'left',BLUE)
    d.label(374,98,'Reject',10.7,'left',RUST)
    d.save()


def minimum_witness(out):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
    from witness_examples import shared_chunk, shared_comparison
    from medium import materialize, recover
    from model import admit_trace

    evidence = shared_comparison()
    model = admit_trace(shared_chunk('no-guard'))
    event_ids = tuple(range(model.n))
    names = tuple(event.get('role',event['kind']) for event in model.events)
    if names != ('chunk', 'manifest', 'root', 'manifest', 'root', 'free'):
        raise ValueError('Witness figure no longer matches the shared-chunk history')
    chunk_slot = model.events[0]['slot']
    cuts = (evidence['frontier_cut'], evidence['minimum_cut'])
    for cut, root in zip(cuts,evidence['selected_roots'],strict=True):
        image = materialize(model,cut)
        slots = image['slots']
        chunk = slots.get(str(chunk_slot),{})
        manifest_slot = model.events[root]['manifest_ref']['slot']
        result = recover(image)
        if (chunk.get('kind') != 'free' or chunk.get('event') != 5
                or slots.get(str(manifest_slot),{}).get('role') != 'manifest'
                or result['ok'] or result['root_event'] != root
                or result['reason'] != f'slot {chunk_slot} is absent or free'):
            raise ValueError('Expected a surviving manifest and an unreadable shared chunk')

    d = Figure(out, 'minimum-witness', 126)
    d.label(12, 20, 'Durable events', 11.5, 'left')
    columns = (110, 145, 177, 211, 244, 271)
    for i, x, name in zip(event_ids,columns,names,strict=True):
        d.label(x, 15, str(i), 11.7)
        d.label(x, 31, name, 9.6, colour=RUST if i == 5 else MUTED)

    rows = ((f'Frontier cut ({len(evidence["frontier_cut"])})', evidence['frontier_cut'], 61),
            (f'Minimum cut ({len(evidence["minimum_cut"])})', evidence['minimum_cut'], 88))
    for (name, cut, y), selected in zip(rows, evidence['selected_roots'], strict=True):
        d.label(12, y, name, 11.1, 'left')
        for i, x in enumerate(columns):
            if i == selected:
                d.ring(x, y, BLUE, 4.7)
            elif i == 5 and i in cut:
                d.dot(x, y, RUST, 3.3)
            elif i in cut:
                d.dot(x, y, INK, 3.3)
            else:
                d.line((x-3.1, y), (x+3.1, y), colour=MUTED, weight=.9)
    # The side panel shows recovery references, not extra durable events.
    d.label(351,20,'Recovery reads',11.5)
    d.label(345,40,'Manifest',10.1,colour=MUTED)
    d.label(397,40,'Shared chunk',10.4,colour=RUST)
    for (_,_,y),root in zip(rows,evidence['selected_roots'],strict=True):
        manifest_event = next(i for i in model.events[root]['deps']
                              if model.events[i].get('role') == 'manifest')
        d.label(296,y,f'Root {root}',10.7,colour=BLUE)
        d.line((315,y),(329,y),arrow=True,colour=BLUE)
        d.node(f'read-{root}',345,y,23,21,str(manifest_event),BLUE,PALE_BLUE,radius=0)
        d.line((359,y),(375,y),(375,74.5),colour=RUST,dashed=True)
    d.line((375,74.5),(389,74.5),colour=RUST,dashed=True)
    d.cross(396,74.5)
    d.label(396,96,'Freed by 5',10.3,colour=RUST)
    d.ring(109,113,BLUE,4.7)
    d.label(121,113,'Selected root',10.4,'left')
    d.dot(205,113,INK,3.3)
    d.label(216,113,'Durable',10.4,'left')
    d.line((278,113),(284,113),colour=MUTED,weight=.9)
    d.label(291,113,'Not durable',10.4,'left')
    d.save()


def selector(out):
    d=Figure(out,'selector-order',173)
    d.label(18,35,'Initial state',11.6,'left')
    d.art('manifest-glyph','manifest',25,49,26,23)
    d.label(38,90,'Manifest A\nselected',10.7)
    d.node('initial-a',38,121,28,21,'A',MUTED,GREY,radius=0)
    d.label(38,143,"A's slot",10.6)
    d.dot(68,101,INK,1.7)
    d.line((68,101),(87,55),colour=BLUE,arrow=True)
    d.line((68,101),(89,127),colour=RUST,arrow=True)

    d.label(91,21,'Publish before reuse',11.6,'left',BLUE)
    d.node('fresh-b',108,53,26,21,'B',BLUE,PALE_BLUE,radius=0)
    d.label(108,78,'Fresh slot',10.7)
    d.line((125,53),(155,53),colour=BLUE,arrow=True)
    d.art('manifest-glyph','manifest',162,40,25,26)
    d.label(174,78,'Prepare manifest',10.7)
    d.line((192,53),(219,53),colour=BLUE,arrow=True)
    d.ring(233,53,BLUE,9.5)
    d.label(233,53,'B',11.4,colour=BLUE)
    d.label(233,78,'Select B',10.7)
    d.line((247,53),(271,53),colour=BLUE,arrow=True)
    d.node('reused-c',287,53,26,21,'C',BLUE,PALE_BLUE,radius=0)
    d.label(287,78,"Reuse A's slot",10.7)
    d.line((305,53),(331,53),colour=BLUE,arrow=True)
    d.label(340,53,'Recover B',11.3,'left',BLUE)

    d.label(91,105,'Reuse before publication',11.6,'left',RUST)
    d.node('overwritten-b',108,136,26,21,'B',RUST,PAPER,radius=0)
    d.label(108,159,"In A's slot",10.7)
    d.line((125,136),(155,136),colour=RUST,arrow=True)
    d.art('manifest-glyph','manifest',162,123,25,26)
    d.label(174,159,'A still selected',10.7)
    d.label(243,123,'Reference to A',10.5,colour=RUST)
    d.line((192,136),(225,136),colour=RUST,dashed=True)
    d.cross(234,136)
    d.label(248,136,'A unavailable',11.3,'left',RUST)
    d.save()


def sqlite(out):
    d=Figure(out,'evidence-paths',138)
    d.art('sqlite-glyph','database',16,42,31,33)
    d.label(32,96,'Committed\nSQLite state',11.4)
    d.label(129,23,'Inputs',11.4)
    d.label(244,23,'Recovery',11.4)
    d.label(358,23,'Compare',11.4)
    for y,source,reader,colour in (
            (49,'Audit projection','Model',BLUE),
            (94,'Stored roots / slots','Native',INK)):
        d.line((56,59 if y==49 else 67),(77,y),colour=colour,arrow=True)
        d.label(130,y,source,11.3,colour=colour)
        d.line((185,y),(214,y),colour=colour,arrow=True)
        d.label(244,y,reader,11.3,colour=colour)
        d.line((267,y),(291,64 if y==49 else 80),colour=colour,arrow=True)
    d.label(353,60,'Success or failure',11.1)
    d.label(353,84,'If both succeed:',10.6,colour=MUTED)
    d.label(353,101,'root, prefix, object bytes',10.6,colour=MUTED)
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
