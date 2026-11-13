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
    (out / 'native-compaction.txt').write_text(
        native_listing(native_range_case()), encoding='ascii', newline='\n')
    evidence = Path(__file__).resolve().parents[1] / 'results/rocksdb-lifecycle'
    native = json.loads((evidence/'checkpoints.json').read_text(encoding='utf-8'))['native']
    restore = json.loads((evidence/'checkpoint-restore.json').read_text(encoding='utf-8'))
    previous, current = set(native['before']), set(native['after'])
    def number(name):
        return str(int(Path(name).stem))
    rows = {(r['chosen'],r['policy']): r for r in restore['rows']}
    keys = native['verified_keys']
    needed = {'before': previous, 'after': current}
    catalog = set(native['archive']) | previous | current
    expired = set(native['archive']) - previous - current
    retained = {'keep-before': previous, 'keep-after': current,
                'keep-both': previous | current, 'hold-all': catalog}
    lengths = {name: size for view in ('archive','before','after')
               for name,size in native[view].items()}
    if (len(rows) != len(restore['rows']) or set(rows) !=
            {(view,policy) for view in needed for policy in retained}
            or keys != restore['summary']['keys_per_reopen']
            or (len(previous-current),len(previous & current),len(current-previous)) != (2,1,1)):
        raise ValueError('Overview no longer matches the observed checkpoint restores')
    for (view,policy),row in rows.items():
        keep = retained[policy]
        missing = needed[view] - keep
        if (set(row['missing']) != missing or set(row['reclaimed']) != catalog - keep
                or row['native_status'] != ('open-failed' if missing else f'ok:{keys}')
                or row['early_bytes'] != sum(lengths[name] for name in catalog - keep)):
            raise ValueError(f'Overview cannot substantiate {view}/{policy}')
    before_only = sorted(previous-current)
    shared, = previous & current
    after_only, = current-previous
    names = [*before_only,shared,after_only]
    positions = dict(zip(names,(123,179,235,291),strict=True))
    d = Figure(out, 'reuse-cut', 254)
    d.label(18,13,'(a) References before and after compaction',11.8,'left')
    d.label(412,13,f'Client sequence {native["sequence"]}',10.5,'right',MUTED)
    d.node('before-view',144,47,116,22,'Before MANIFEST',MUTED,PAPER,radius=1.6)
    d.node('after-view',310,47,115,22,'After MANIFEST',BLUE,PAPER,radius=1.6)
    d.label(227,34,'Compact',10.2,colour=MUTED)
    d.line((206,47),(248,47),arrow=True,colour=MUTED)
    for port,name in zip((112,146,180),(*before_only,shared),strict=True):
        d.line((port,59),(positions[name]-(4 if name==shared else 0),84),
               arrow=True,colour=MUTED)
    for port,name in zip((275,314),(shared,after_only),strict=True):
        d.line((port,59),(positions[name]+(4 if name==shared else 0),84),
               arrow=True,colour=BLUE)
    for name,x in positions.items():
        d.node('sst-'+number(name),x,96,46,20,'SST '+number(name),
               BLUE if name==shared else MUTED,PALE_BLUE if name==shared else PAPER,radius=0)
    d.label(235,115,'Shared',10.4,colour=BLUE)

    d.label(18,137,'(b) Restore under either selected MANIFEST',11.8,'left')
    d.label(62,156,'Retain',10.6,colour=MUTED)
    d.label(366,156,'Native reopen',10.6,colour=MUTED)
    for y,policy,label in ((176,'keep-after','After only'),(202,'keep-both','Both views')):
        d.label(62,y,label,11.2)
        d.line((95,y),(110,y),arrow=True,colour=MUTED)
        for name in names:
            x=positions[name]
            if name in retained[policy]:
                d.dot(x,y,BLUE,3.4)
            else:
                d.cross(x,y)
        d.line((302,y),(321,y),arrow=True,colour=MUTED)
    d.label(330,172,'Before fails',11.3,'left',RUST)
    d.label(330,185,'After exact',10.3,'left',MUTED)
    d.label(330,202,'Both exact',11.3,'left',BLUE)
    d.dot(27,231,BLUE,3.4)
    d.label(37,231,'Retained in pool',10.6,'left',MUTED)
    d.cross(137,231)
    d.label(147,231,'Missing from pool',10.6,'left',MUTED)
    d.label(412,231,f'Exact: {keys} key-value pairs match',10.6,'right',MUTED)
    retired_bytes = sum(lengths[name] for name in expired)
    d.label(412,247,f'Both views: {retired_bytes:,} bytes reclaimed early',
            10.5,'right',BLUE)
    d.save()


def frontier(out):
    d=Figure(out,'retirement-frontier',137)
    xs=(51,153,272,380)
    for i,(x,caption) in enumerate(zip(xs,('Allocate a','Publish r','Publish q','Write w'))):
        d.dot(x,74,BLUE if x==272 else INK,3)
        d.label(x,91,caption,11.8,colour=BLUE if x==272 else INK)
        if i:
            d.line((xs[i-1]+8,74),(x-8,74),colour=MUTED,weight=.74,arrow=True)
    d.line((153,67),(153,43),(51,43),(51,67),dashed=True,arrow=True)
    d.label(102,30,'r references a',11,colour=MUTED)
    d.line((272,67),(272,43),(380,43),(380,67),arrow=True,colour=BLUE,weight=1.15)
    d.label(326,30,'q must persist before w',11,colour=BLUE)
    d.label(153,117,'T(w) = r',12)
    d.label(272,117,'F(w) = q',12,colour=BLUE)
    d.label(212,117,'<',13)
    d.label(354,117,'Issue order above',10.5,colour=MUTED)
    d.save()


def certificate(out):
    d=Figure(out,'certificate-check',148)
    d.label(48,72,'Admitted\nhistory',11.7)
    d.line((75,66),(96,42),arrow=True,colour=BLUE)
    d.line((75,79),(103,112),arrow=True)
    d.label(143,23,'Producer',10.7,colour=MUTED)
    d.label(143,42,'Compute frontier',11.1,colour=BLUE)
    d.line((190,42),(209,42),arrow=True,colour=BLUE)
    d.art('certificate-glyph','packet',216,29,29,26)
    d.label(230,63,'Certificate rows',10.6,colour=BLUE)
    d.line((250,43),(279,66),arrow=True,colour=BLUE)

    d.label(169,93,'Independent verification',10.7,colour=MUTED)
    d.label(169,113,'Derive canonical rows',11.1)
    d.line((226,113),(257,113),(279,82),arrow=True)
    d.label(311,73,'Row-by-row\ncheck',11.3)
    d.line((343,68),(365,54),arrow=True,colour=BLUE)
    d.line((343,82),(365,99),arrow=True,colour=RUST,dashed=True)
    d.ring(372,51,BLUE,3.5)
    d.cross(372,102)
    d.label(382,51,'Accept',10.7,'left',BLUE)
    d.label(382,102,'Reject',10.7,'left',RUST)
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
    d.label(54,35,'Initial state',11.2)
    d.art('manifest-glyph','manifest',42,48,24,24)
    d.label(54,83,'A selected',10.6)
    d.line((54,93),(54,111),arrow=True)
    d.node('initial-a',54,126,28,20,'A',MUTED,GREY,radius=0)
    d.label(54,145,"A's slot",10.6)
    d.line((83,86),(106,86),colour=MUTED)
    d.dot(106,86,INK,1.65)
    d.line((106,86),(145,55),colour=BLUE,arrow=True)
    d.line((106,86),(145,133),colour=RUST,arrow=True)

    d.label(157,20,"Write B elsewhere, publish B, then reuse A's slot",10.8,'left',BLUE)
    d.art('manifest-glyph','manifest',158,43,24,24)
    d.label(170,78,'B selected',10.6)
    d.line((188,55),(249,55),arrow=True,colour=BLUE)
    d.label(218,43,'Refers to B',10.2,colour=BLUE)
    d.node('fresh-b',268,55,27,20,'B',BLUE,PALE_BLUE,radius=0)
    d.label(268,78,'Fresh slot',10.6)
    d.node('reused-c',333,55,27,20,'C',MUTED,GREY,radius=0)
    d.label(333,78,"A's slot",10.6)
    d.label(365,55,'Recover B',11,'left',BLUE)

    d.label(157,100,"Overwrite A's slot before publishing B",10.8,'left',RUST)
    d.art('manifest-glyph','manifest',158,121,24,24)
    d.label(170,156,'A selected',10.6)
    d.line((188,133),(301,133),colour=RUST,dashed=True)
    d.label(247,121,'Refers to A',10.2,colour=RUST)
    d.cross(309,133)
    d.node('overwritten-b',333,133,27,20,'B',RUST,PAPER,radius=0)
    d.label(333,156,"A's slot",10.6)
    d.label(365,129,'A missing',11,'left',RUST)
    d.label(365,143,'Recovery fails',9.7,'left',MUTED)
    d.save()


def sqlite(out):
    d=Figure(out,'evidence-paths',138)
    d.art('sqlite-glyph','database',19,43,29,30)
    d.label(34,95,'Committed\nSQLite state',11)
    d.line((51,61),(66,61),colour=MUTED)
    d.dot(66,61,INK,1.65)
    for y,source,reader,colour in (
            (37,'Audit projection','Model recovery',BLUE),
            (95,'Stored roots / slots','Native recovery',INK)):
        d.line((66,61),(87,y),colour=colour,arrow=True)
        d.label(132,y,source,10.7,colour=colour)
        d.line((181,y),(201,y),colour=colour,arrow=True)
        d.label(243,y,reader,11.1,colour=colour)
        d.line((286,y),(303,y),(321,59 if y==37 else 73),
               colour=colour,arrow=True)
    d.label(365,66,'Compare success\nor failure',11)
    d.line((365,81),(365,101),arrow=True)
    d.label(373,91,'Both succeed',9.5,'left',MUTED)
    d.label(365,118,'Compare root, prefix\nand object bytes',10.7)
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
