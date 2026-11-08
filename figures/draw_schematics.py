"""Assemble manuscript schematics with exact vector annotation and frozen artwork."""
import argparse
import math
from pathlib import Path
import sys
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plots'))
import draw_write_ratios as vector

CM = 72 / 2.54
FONT = vector.FONT
NODE_PADDING = .14  # cm in the editable diagram, before manuscript scaling
TEXT_CLEARANCE = .08
ART = Path(__file__).resolve().parent / 'artwork'
W = 430.00462*vector.PT/CM
INK = (.12,.18,.24)
# Insets retain the complete glyph outlines while excluding sheet whitespace.
DOCUMENT = (.069,.294,.293,.706)
RETIRED_DOCUMENT = (.393,.294,.615,.706)
STORAGE = (.695,.292,.939,.708)


def arrow_vertices(p, q):
    dx, dy = q[0]-p[0], q[1]-p[1]
    length = math.hypot(dx, dy)
    if length <= 1e-12:
        raise ValueError('Arrow needs a nonzero final segment')
    ux, uy = dx/length, dy/length
    bx, by = q[0]-6.0/CM*ux, q[1]-6.0/CM*uy
    return [q, (bx-2.6/CM*uy, by+2.6/CM*ux),
            (bx+2.6/CM*uy, by-2.6/CM*ux)]


def segment_enters_box(p, q, box):
    """Liang--Barsky clipping for connector/text clearance."""
    lo, hi = 0., 1.
    for start, delta, lower, upper in (
            (p[0], q[0]-p[0], box[0], box[2]),
            (p[1], q[1]-p[1], box[1], box[3])):
        if abs(delta) < 1e-12:
            if not lower < start < upper:
                return False
        else:
            a, b = sorted(((lower-start)/delta, (upper-start)/delta))
            lo, hi = max(lo, a), min(hi, b)
            if lo >= hi:
                return False
    return lo < hi


class Drawing:
    def __init__(self, bounds):
        self.bounds = bounds
        self.nodes = {}
        self.labels = []
        self.segments = []
        self.commands = []
        self.artwork = []

    def text(self, x, y, label, size=10.5, ha='center', va='center'):
        if va != 'center':
            raise ValueError('Diagram text uses centered baselines')
        lines = label.split('\n')
        leading = size * 1.15
        ascent, descent = pdfmetrics.getAscentDescent(FONT, size)
        first = y*CM + (len(lines)-1)*leading/2 - (ascent+descent)/2
        width = max(pdfmetrics.stringWidth(line, FONT, size) for line in lines)
        left = x*CM - (width/2 if ha == 'center' else width if ha == 'right' else 0)
        box = (left/CM, (first-(len(lines)-1)*leading+descent)/CM,
               (left+width)/CM, (first+ascent)/CM)
        self.labels.append([label, box, None])
        if any(not 32 <= ord(ch) <= 126 for ch in label.replace('\n','')):
            raise ValueError('Use glyphs from the embedded manuscript face')
        self.commands.append(('text', x, first/CM, lines, leading/CM, size, ha))

    def box(self, name, x, y, w, h, label, size=10.5, tone='neutral', symbol=None):
        if name in self.nodes:
            raise ValueError('Duplicate node: '+name)
        self.nodes[name] = (x, y, w, h)
        self.commands.append(('box', x-w/2, y-h/2, w, h, tone))
        if symbol is not None:
            source, crop = symbol
            self.art(source, crop, (x-w/2+.16, y-.30, x-w/2+.77, y+.30))
        if label:
            self.text(x+(.29 if symbol else 0), y, label, size)
            self.labels[-1][2] = (x-w/2, y-h/2, x+w/2, y+h/2)

    def point(self, name, side):
        x, y, w, h = self.nodes[name]
        return {'n': (x, y+h/2), 's': (x, y-h/2),
                'e': (x+w/2, y), 'w': (x-w/2, y)}[side]

    def line(self, points, dashed=False):
        self.segments.extend(zip(points, points[1:]))
        self.commands.append(('line', points, dashed))

    def arrow(self, p, q, dashed=False, via=()):
        self.line([p, *via, q], dashed)
        self.commands.append(('head', ([p, *via])[-1], q))

    def edge(self, a, b, sa='s', sb='n', **kwargs):
        self.arrow(self.point(a, sa), self.point(b, sb), **kwargs)

    def art(self, source, crop, box):
        self.artwork.append(box)
        self.commands.append(('art', source, crop, box))

    def save(self, out, name):
        x0, y0, x1, y1 = self.bounds
        node_boxes = [(key,(x-w/2,y-h/2,x+w/2,y+h/2))
                      for key,(x,y,w,h) in self.nodes.items()]
        def overlaps(a,b):
            return min(a[2],b[2]) > max(a[0],b[0]) and min(a[3],b[3]) > max(a[1],b[1])
        for i,(key,b) in enumerate(node_boxes):
            if b[0]<x0 or b[1]<y0 or b[2]>x1 or b[3]>y1:
                raise ValueError(name+': node outside canvas '+key)
            for other,a in node_boxes[i+1:]:
                if overlaps(a,b):
                    raise ValueError(name+': overlapping nodes '+key+' and '+other)
        boxes = []
        for label, b, node in self.labels:
            if b[0] < x0 or b[1] < y0 or b[2] > x1 or b[3] > y1:
                raise ValueError(name + ': clipped label ' + repr(label))
            if node and (b[0] < node[0]+NODE_PADDING or b[1] < node[1]+NODE_PADDING
                         or b[2] > node[2]-NODE_PADDING or b[3] > node[3]-NODE_PADDING):
                raise ValueError(name + ': insufficient node padding ' + repr(label))
            for key,a in node_boxes:
                if a != node and overlaps(a,b):
                    raise ValueError(name+': label crosses unrelated node '+key)
            boxes.append((label, b))
        for i, (label, a) in enumerate(boxes):
            for other, b in boxes[i+1:]:
                if min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1]):
                    raise ValueError(name + ': overlapping labels ' + repr((label, other)))
            if any(min(a[2], b[2]) > max(a[0], b[0]) and
                   min(a[3], b[3]) > max(a[1], b[1]) for b in self.artwork):
                raise ValueError(name + ': artwork overlaps ' + repr(label))
        if any(b[0]<x0 or b[1]<y0 or b[2]>x1 or b[3]>y1 for b in self.artwork):
            raise ValueError(name + ': artwork outside page')
        obstacles = [(key, (x-w/2+.02, y-h/2+.02, x+w/2-.02, y+h/2-.02))
                     for key, (x, y, w, h) in self.nodes.items()] + [
            (label,(b[0]-TEXT_CLEARANCE,b[1]-TEXT_CLEARANCE,
                    b[2]+TEXT_CLEARANCE,b[3]+TEXT_CLEARANCE)) for label,b in boxes] + [
            ('artwork', b) for b in self.artwork]
        # A clear centreline is not sufficient if the arrowhead grazes text.
        segments = list(self.segments)
        for kind,*args in self.commands:
            if kind == 'head':
                vertices=arrow_vertices(*args)
                segments.extend(zip(vertices,vertices[1:]+vertices[:1]))
        for p, q in segments:
            for label, box in obstacles:
                if segment_enters_box(p, q, box):
                    raise ValueError(name + ': connector crosses ' + repr(label))
        c = canvas.Canvas(str(out / (name+'.pdf')),
                          pagesize=((x1-x0)*CM, (y1-y0)*CM), invariant=1,
                          initialFontName=FONT, initialFontSize=9)
        c.setCreator('Python ReportLab')
        c.translate(-x0*CM, -y0*CM)
        for command in self.commands:
            kind, *args = command
            c.setStrokeColorRGB(0, 0, 0)
            c.setFillColorRGB(*INK)
            c.setLineWidth(.85)
            c.setDash()
            if kind == 'box':
                x, y, w, h, tone = args
                fill,accent={'neutral':((.985,.988,.990),(.43,.49,.55)),
                             'blue':((.95,.97,.985),vector.BLUE),
                             'orange':((.993,.929,.85),vector.ORANGE),
                             'green':((.92,.967,.952),(.15,.40,.35)),
                             'red':((.992,.917,.895),(.59,.20,.13)),
                             'paper':((1.,1.,1.),(.43,.49,.55))}[tone]
                c.setFillColorRGB(*fill)
                c.setStrokeColorRGB(*accent)
                c.roundRect(x*CM,y*CM,w*CM,h*CM,2.0,fill=1,stroke=1)
            elif kind == 'art':
                source, (l,t,r,b), (x,y,xmax,ymax) = args
                if not (0 <= l < r <= 1 and 0 <= t < b <= 1):
                    raise ValueError('Invalid source viewport')
                img = ImageReader(str(source))
                pw, ph = img.getSize()
                scale = min((xmax-x)*CM/(pw*(r-l)), (ymax-y)*CM/(ph*(b-t)))
                w, h = pw*scale, ph*scale
                cw, ch = (r-l)*w, (b-t)*h
                left, bottom = ((x+xmax)*CM-cw)/2, ((y+ymax)*CM-ch)/2
                c.saveState()
                clip = c.beginPath()
                clip.rect(left,bottom,cw,ch)
                c.clipPath(clip,stroke=0,fill=0)
                c.drawImage(img,left-l*w,bottom-(1-b)*h,w,h,mask='auto')
                c.restoreState()
            elif kind == 'text':
                x, first, lines, leading, size, ha = args
                c.setFont(FONT, size)
                for i, line in enumerate(lines):
                    at = (first-i*leading)*CM
                    if ha == 'center':
                        c.drawCentredString(x*CM, at, line)
                    elif ha == 'right':
                        c.drawRightString(x*CM, at, line)
                    else:
                        c.drawString(x*CM, at, line)
            elif kind == 'line':
                points, dashed = args
                c.setStrokeColorRGB(*INK)
                c.setLineWidth(1.02)
                if dashed:
                    c.setDash(3.2, 2.1)
                path = c.beginPath()
                path.moveTo(points[0][0]*CM, points[0][1]*CM)
                for x, y in points[1:]:
                    path.lineTo(x*CM, y*CM)
                c.drawPath(path)
            elif kind == 'head':
                p, q = args
                c.setFillColorRGB(*INK)
                vertices=arrow_vertices(p,q)
                path = c.beginPath()
                path.moveTo(vertices[0][0]*CM,vertices[0][1]*CM)
                for x,y in vertices[1:]:path.lineTo(x*CM,y*CM)
                path.close()
                c.drawPath(path, fill=1, stroke=0)
        c.save()


def frontier(out):
    d=Drawing((0.,0.,W,4.75))
    for name,x,label,tone in [('a',1.78,'Allocation a\nold incarnation','neutral'),
                              ('r',5.64,'Root r\nlast user of a','neutral'),
                              ('q',9.50,'Root q\nretires a','blue'),
                              ('w',13.36,'Writer w\noverwrites slot','orange')]:
        d.box(name,x,2.45,3.2,1.24,label,11,tone)
    for a,b in [('a','r'),('r','q'),('q','w')]:
        d.edge(a,b,'e','w')
    d.arrow(d.point('r','n'),d.point('a','n'),dashed=True,via=[(5.64,3.75),(1.78,3.75)])
    d.text(3.71,4.25,'Root reference',10.5)
    d.text(5.64,1.10,'T(w) = r',13)
    d.text(7.57,1.10,'<',13)
    d.text(9.50,1.10,'F(w) = q',13)
    d.text(5.64,.42,'Last threatened root',10)
    d.text(9.50,.42,'Forced durable root',10)
    d.save(out,'retirement-frontier')


def motivation(out):
    d = Drawing((0.,0.,W,4.72))
    glyphs = ART/'storage-symbols.png'
    for i, (title, tone, crop, root, verdict) in enumerate((
        ('(a) Older root selected','red',DOCUMENT,'Old root','Recovery fails'),
        ('(b) Retiring root selected','green',RETIRED_DOCUMENT,'Retiring root','Old allocation unreferenced'),
    )):
        left = .22 + 7.64*i
        d.text(left+3.50,4.33,title,11.5)
        d.art(glyphs,crop,(left+.36,1.97,left+1.58,3.30))
        d.text(left+.97,1.59,root,10.5)
        for j in range(3):
            d.box(f'slot{i}{j}',left+3.59+1.25*j,2.62,1.12,.85,
                  'New' if j==1 else '',10.5,'orange' if j==1 else 'neutral')
        d.text(left+4.84,1.59,'Overwritten slot',10.5)
        d.box(f'result{i}',left+3.50,.56,6.80,.82,verdict,11,tone)
        if i==0:
            d.arrow((left+.93,3.30),d.point('slot01','n'),dashed=True,
                    via=[(left+.93,3.57),(left+4.84,3.57)])
            d.text(left+3.30,3.88,'Stale reference',10.5)
        else:
            d.text(left+3.35,3.57,'No reference to this slot',10.5)
    d.save(out,'reuse-cut')


def paths(out):
    d = Drawing((0.,0.,W,9.08))
    symbols=ART/'storage-symbols.png'
    doc=(symbols,DOCUMENT)
    disk=(symbols,STORAGE)
    d.text(.25,8.77,'(a) Check a positive certificate',11.5,ha='left')
    d.text(.25,5.47,'(b) Compare recovery at a SQLite commit',11.5,ha='left')
    for name,x,y,w,label,tone,icon in (
        ('trace',1.79,7.70,3.05,'Admitted\nhistory','paper',doc),
        ('producer',5.64,7.70,3.02,'Frontier\nanalysis','blue',None),
        ('certificate',9.49,7.70,3.02,'Positive\ncertificate','blue',None),
        ('consumer',13.34,7.70,3.03,'Separate\nverifier','blue',None),
        ('commits',1.79,2.61,3.05,'SQLite\ncommit','paper',disk),
        ('projection',5.64,3.95,3.02,'Audit\nprojection','neutral',None),
        ('records',5.64,1.27,3.02,'Retained\nrecords','neutral',None),
        ('model',9.49,3.95,3.02,'Model\nrecovery','blue',None),
        ('native',9.49,1.27,3.02,'Native\nrecovery','orange',None),
        ('compare',13.34,2.61,3.03,'Compare\nrecovery results','green',None)):
        d.box(name,x,y,w,1.12,label,10.5,tone,symbol=icon)
    for a,b in (('trace','producer'),('producer','certificate'),('certificate','consumer'),
                ('projection','model'),('records','native')):
        d.edge(a,b,'e','w')
    d.arrow(d.point('trace','s'),d.point('consumer','s'),via=[(1.79,6.34),(13.34,6.34)])
    d.text(7.57,6.00,'Original history',10.5)
    joint=(3.71,2.61)
    d.line([d.point('commits','e'),joint])
    d.arrow(joint,d.point('projection','w'),via=[(3.71,3.95)])
    d.arrow(joint,d.point('records','w'),via=[(3.71,1.27)])
    d.arrow(d.point('model','e'),d.point('compare','n'),via=[(13.34,3.95)])
    d.arrow(d.point('native','e'),d.point('compare','s'),via=[(13.34,1.27)])
    d.save(out,'evidence-paths')


def selector(out):
    d=Drawing((0.,0.,W,6.05))
    columns=(1.78,5.64,9.50,13.36)
    for prefix,y,title,steps in (
        ('safe',4.29,'(a) Ordered protocol',(
            ('Write B in\nfresh slot','neutral'),('Prepare B\nmanifest','neutral'),
            ('Select B','blue'),('Reuse old slot','green'))),
        ('unsafe',1.52,'(b) Unsafe control',(
            ('Overwrite A\nwith B','red'),('Prepare B\nmanifest','orange'),
            ('Select B','blue'))),
    ):
        d.text(.25,y+1.03,title,11.5,ha='left')
        for i,(label,tone) in enumerate(steps):
            d.box(f'{prefix}{i}',columns[i],y,3.2,1.16,label,10.5,tone)
            if i:
                d.edge(f'{prefix}{i-1}',f'{prefix}{i}','e','w')
    d.text(3.71,3.31,'A remains recoverable',10.5)
    d.text(11.43,3.31,'B is selected before reuse',10.5)
    d.text(3.71,.54,'Selector still names A: recovery fails',10.5)
    d.text(9.50,.54,'B is recoverable',10.5)
    d.save(out,'selector-order')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--font-root', type=Path, required=True)
    args = parser.parse_args()
    vector.register_font(args.font_root)
    args.out.mkdir(parents=True, exist_ok=True)
    frontier(args.out)
    motivation(args.out)
    paths(args.out)
    selector(args.out)


if __name__ == '__main__':
    main()

