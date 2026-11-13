"""Draw storage relationships as text and native PDF vectors at column size."""
import argparse
import json
import math
from pathlib import Path
import sys

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen.canvas import Canvas

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

    def document(self, key, x, y, width, height, text, edge=MUTED, fill=PAPER):
        """A record with a folded corner, one label, and explicit ports."""
        l,t,r,b = x-width/2,y-height/2,x+width/2,y+height/2
        if key in self.nodes:
            raise ValueError(f'Duplicate node {key}')
        self.nodes[key] = l,t,r,b
        fold = 6
        path = self.c.beginPath()
        path.moveTo(l,self.height-t)
        for px,py in ((r-fold,t),(r,t+fold),(r,b),(l,b),(l,t)):
            path.lineTo(px,self.height-py)
        self.c.setStrokeColorRGB(*edge)
        self.c.setFillColorRGB(*fill)
        self.c.setLineWidth(.85)
        self.c.drawPath(path,fill=1,stroke=1)
        self.c.lines(((r-fold,self.height-t,r-fold,self.height-t-fold),
                      (r-fold,self.height-t-fold,r,self.height-t-fold)))
        self.label(x,y,text,11.5,owner=key)

    def port(self, key, side, fraction=.5):
        l,t,r,b = self.nodes[key]
        if not 0 < fraction < 1:
            raise ValueError('Port must lie strictly inside its edge')
        return {'left': (l,t+(b-t)*fraction), 'right': (r,t+(b-t)*fraction),
                'top': (l+(r-l)*fraction,t), 'bottom': (l+(r-l)*fraction,b)}[side]

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
    expired = sorted(catalog - (previous | current))
    names = [*before_only,shared,after_only,*expired]
    positions = dict(zip(names,(103,139,175,211,247,283),strict=True))
    d = Figure(out, 'reuse-cut', 248)
    d.label(18,13,'(a) Checkpoint references across compaction',11.8,'left')
    d.label(412,13,f'Client sequence {native["sequence"]}',10.5,'right',MUTED)
    d.node('before-view',122,47,114,22,'Before MANIFEST',MUTED,PAPER,radius=1.6)
    d.node('after-view',272,47,108,22,'After MANIFEST',BLUE,PAPER,radius=1.6)
    d.label(196,25,'Compact',10.2,colour=MUTED)
    d.line((182,47),(213,47),arrow=True,colour=MUTED)
    for port,name in zip((96,128,159),(*before_only,shared),strict=True):
        d.line((port,59),(positions[name]-(4 if name==shared else 0),84),
               arrow=True,colour=MUTED)
    for port,name in zip((234,260),(shared,after_only),strict=True):
        d.line((port,59),(positions[name]+(4 if name==shared else 0),84),
               arrow=True,colour=BLUE)
    d.label(66,96,'SST IDs',10.4,'right',MUTED)
    for name,x in positions.items():
        d.node('sst-'+number(name),x,96,29,20,number(name),
               BLUE if name==shared else MUTED,PALE_BLUE if name==shared else PAPER,
               dashed=name in expired,radius=0)
    d.label(175,115,'Shared',10.4,colour=BLUE)
    d.label(265,115,'Expired only',10.4,colour=MUTED)

    d.label(18,136,'(b) Retention before checkpoint selection',11.8,'left')
    d.label(18,155,'Policy',10.6,'left',MUTED)
    d.label(320,155,'Before',10.6,colour=MUTED)
    d.label(355,155,'After',10.6,colour=MUTED)
    d.label(402,155,'Bytes freed',10.6,colour=MUTED)
    for y,policy,label in ((173,'keep-before','Before only'),(191,'keep-after','After only'),
                           (209,'keep-both','Union'),(227,'hold-all','Keep all')):
        d.label(18,y,label,11.2,'left')
        for name in names:
            x=positions[name]
            if name in retained[policy]:
                d.dot(x,y,BLUE,3.4)
            else:
                d.ring(x,y,MUTED,3.4)
        exact_by_view = {view: rows[view,policy]['native_status'] == f'ok:{keys}'
                         for view in needed}
        for x,view in ((320,'before'),(355,'after')):
            exact = exact_by_view[view]
            d.label(x,y,'Exact' if exact else 'Fails',10.8,colour=BLUE if exact else RUST)
        d.label(402,y,f"{rows['before',policy]['early_bytes']:,}"
                if all(exact_by_view.values()) else 'Unsafe',
                10.5,colour=INK if all(exact_by_view.values()) else RUST)
    d.dot(103,242,BLUE,3.4)
    d.label(113,242,'Kept',10.6,'left',MUTED)
    d.ring(160,242,MUTED,3.4)
    d.label(170,242,'Omitted',10.6,'left',MUTED)
    d.save()


def frontier(out):
    d=Figure(out,'retirement-frontier',119)
    d.node('alloc',52,62,72,26,'Allocate a',MUTED,GREY)
    d.document('old',156,62,70,26,'Publish r')
    d.document('new',269,62,70,26,'Publish q',BLUE,PALE_BLUE)
    d.node('write',374,62,72,26,'Write w',MUTED,GREY)
    for first,second in (('alloc','old'),('old','new'),('new','write')):
        d.line(d.port(first,'right'),d.port(second,'left'),weight=.76,arrow=True)
    d.line(d.port('old','top'),(156,28),(52,28),d.port('alloc','top'),
           dashed=True,arrow=True)
    d.label(104,15,'References',10.8,colour=MUTED)
    d.line(d.port('new','top'),(269,28),(374,28),d.port('write','top'),
           arrow=True,colour=BLUE,weight=1.15)
    d.label(321.5,15,'Durability dependency',10.8,colour=BLUE)
    d.label(212,89,'Issue order',10.5,colour=MUTED)
    d.label(212,107,'T(w) = r  <  q = F(w)',12.1)
    d.save()


def certificate(out):
    d=Figure(out,'certificate-check',143)
    d.document('history',49,73,76,38,'Admitted\nhistory')
    d.node('produce',143,39,74,32,'Compute\nfrontier',BLUE,PALE_BLUE)
    d.document('supplied',238,39,86,32,'Supplied rows',BLUE)
    d.document('expected',238,108,86,32,'Expected rows')
    d.node('compare',322,73,52,31,'Compare',MUTED,GREY)
    d.node('accept',394,46,56,23,'Accept',BLUE)
    d.node('reject',394,100,56,23,'Reject',RUST,dashed=True)
    d.label(144,13,'Producer',10.5,colour=BLUE)
    d.line(d.port('history','right',.25),d.port('produce','left'),
           arrow=True,colour=BLUE)
    d.line(d.port('produce','right'),d.port('supplied','left'),arrow=True,colour=BLUE)
    d.line(d.port('history','right',.75),(103,108),d.port('expected','left'),
           arrow=True)
    d.label(138,128,'Independent derivation',10.6,colour=MUTED)
    for key,fraction,colour in (('supplied',.27,BLUE),('expected',.73,MUTED)):
        x,y = d.port('compare','left',fraction)
        d.line(d.port(key,'right'),(x-10,y),(x,y),arrow=True,colour=colour)
    d.line(d.port('compare','right',.25),d.port('accept','left'),arrow=True,colour=BLUE)
    d.line(d.port('compare','right',.75),d.port('reject','left'),
           arrow=True,colour=RUST,dashed=True)
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
        d.document(f'root-{root}',299,y,42,21,f'Root {root}',BLUE)
        d.node(f'read-{root}',345,y,23,21,str(manifest_event),BLUE,PALE_BLUE,radius=0)
        d.line(d.port(f'root-{root}','right'),d.port(f'read-{root}','left'),
               arrow=True,colour=BLUE)
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
    d=Figure(out,'selector-order',143)
    d.document('initial',51,71,73,27,'Root A')
    d.node('initial-a',51,116,70,25,'Old slot: A',MUTED,GREY,radius=1)
    d.line(d.port('initial','bottom'),d.port('initial-a','top'),arrow=True)
    d.label(51,46,'Initial state',10.8,colour=MUTED)
    d.line(d.port('initial','right'),(106,71))
    d.dot(106,71,INK,1.6)
    d.document('current-b',175,39,72,26,'Root B',BLUE)
    d.document('current-a',175,104,72,26,'Root A')
    d.line((106,71),d.port('current-b','left'),colour=BLUE,arrow=True)
    d.line((106,71),d.port('current-a','left'),colour=RUST,arrow=True)
    d.label(139,15,'Publish before reuse',10.6,colour=BLUE)
    d.label(139,128,'Reuse before publish',10.6,colour=RUST)
    d.node('fresh',270,39,81,25,'Fresh slot: B',BLUE,PALE_BLUE,radius=1)
    d.node('reused',369,39,81,25,'Old slot: C',MUTED,GREY,radius=1)
    d.node('overwritten',369,104,81,25,'Old slot: B',RUST,PAPER,radius=1)
    d.line(d.port('current-b','right'),d.port('fresh','left'),colour=BLUE,arrow=True)
    d.line(d.port('current-a','right'),(309,104),colour=RUST,dashed=True)
    d.cross(319,104)
    d.label(261,92,'A missing',10.8,colour=RUST)
    d.label(270,65,'Recovers B',10.6,colour=BLUE)
    d.save()


def sqlite(out):
    d=Figure(out,'evidence-paths',142)
    d.node('db',55,69,90,35,'Committed\nSQLite state',MUTED,GREY)
    for y,key,text,relation,colour in (
            (32,'model','Model\nrecovery','Audit events',BLUE),
            (107,'native','Native\nrecovery','Roots and slots',INK)):
        d.node(key,234,y,74,34,text,colour,PALE_BLUE if key=='model' else PAPER)
        d.line(d.port('db','right',.25 if key=='model' else .75),(123,y),
               d.port(key,'left'),colour=colour,arrow=True)
        d.label(155,y-11,relation,10.7,colour=colour)
    d.node('compare',359,69,114,37,'Compare\nrecovery outcomes',MUTED,GREY)
    for key,fraction,colour in (('model',.29,BLUE),('native',.71,INK)):
        d.line(d.port(key,'right'),d.port('compare','left',fraction),colour=colour,arrow=True)
    d.document('bytes',359,123,114,30,'Root, prefix, bytes')
    d.line(d.port('compare','bottom'),d.port('bytes','top'),arrow=True)
    d.label(368,98,'Both succeed',10.4,'left',MUTED)
    d.save()


def main():
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
