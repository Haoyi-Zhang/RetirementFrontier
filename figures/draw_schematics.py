"""Python vector diagrams with the manuscript's embedded Type 1 font."""
import argparse
import math
from pathlib import Path
import sys
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plots'))
import draw_write_ratios as vector

CM = 72 / 2.54
FONT = vector.FONT


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

    def text(self, x, y, label, size=9, ha='center', va='center'):
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
        self.commands.append(('text', x, first/CM, lines, leading/CM, size, ha))

    def box(self, name, x, y, w, h, label, size=9, tone='neutral'):
        self.nodes[name] = (x, y, w, h)
        self.commands.append(('box', x-w/2, y-h/2, w, h, tone))
        self.text(x, y, label, size)
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

    def save(self, out, name):
        x0, y0, x1, y1 = self.bounds
        boxes = []
        for label, b, node in self.labels:
            if b[0] < x0 or b[1] < y0 or b[2] > x1 or b[3] > y1:
                raise ValueError(name + ': clipped label ' + repr(label))
            if node and (b[0] < node[0]+.025 or b[1] < node[1]+.025
                         or b[2] > node[2]-.025 or b[3] > node[3]-.025):
                raise ValueError(name + ': insufficient node padding ' + repr(label))
            boxes.append((label, b))
        for i, (label, a) in enumerate(boxes):
            for other, b in boxes[i+1:]:
                if min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1]):
                    raise ValueError(name + ': overlapping labels ' + repr((label, other)))
        obstacles = [(key, (x-w/2+.02, y-h/2+.02, x+w/2-.02, y+h/2-.02))
                     for key, (x, y, w, h) in self.nodes.items()] + boxes
        for p, q in self.segments:
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
            c.setFillColorRGB(0, 0, 0)
            c.setLineWidth(.55)
            c.setDash()
            if kind == 'box':
                x, y, w, h, tone = args
                fill,accent={'neutral':((.96,.97,.98),(.43,.49,.55)),
                             'blue':((.91,.95,.98),vector.BLUE),
                             'orange':((.99,.95,.90),vector.ORANGE)}[tone]
                c.setFillColorRGB(*fill)
                c.setStrokeColorRGB(*accent)
                c.roundRect(x*CM,y*CM,w*CM,h*CM,3,fill=1,stroke=1)
                c.setStrokeColorRGB(*accent)
                c.setLineWidth(1.6)
                c.line((x+.13)*CM,(y+h)*CM,(x+w-.13)*CM,(y+h)*CM)
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
                if dashed:
                    c.setDash(3, 2)
                path = c.beginPath()
                path.moveTo(points[0][0]*CM, points[0][1]*CM)
                for x, y in points[1:]:
                    path.lineTo(x*CM, y*CM)
                c.drawPath(path)
            elif kind == 'head':
                p, q = args
                dx, dy = q[0]-p[0], q[1]-p[1]
                length = math.hypot(dx, dy)
                ux, uy = dx/length, dy/length
                bx, by = q[0]*CM-4.5*ux, q[1]*CM-4.5*uy
                path = c.beginPath()
                path.moveTo(q[0]*CM, q[1]*CM)
                path.lineTo(bx-1.8*uy, by+1.8*ux)
                path.lineTo(bx+1.8*uy, by-1.8*ux)
                path.close()
                c.drawPath(path, fill=1, stroke=0)
        c.save()


def frontier(out):
    d=Drawing((0.,0.,15.2,4.2))
    for name,x,label,tone in [('a',1.9,'Allocation a\nold incarnation','neutral'),
                              ('r',5.7,'Root r\nlast recoverable user','neutral'),
                              ('q',9.5,'Root q\nallocation retired','blue'),
                              ('w',13.3,'Writer w\nslot overwritten','orange')]:
        d.box(name,x,1.9,3.0,1.15,label,9.5,tone)
    for a,b in [('a','r'),('r','q'),('q','w')]:d.edge(a,b,'e','w')
    d.arrow(d.point('r','n'),d.point('a','n'),dashed=True,via=[(5.7,3.25),(1.9,3.25)])
    d.text(3.8,3.65,'Root reference',size=9)
    d.text(5.7,.65,'Threat frontier: T(w) = r',size=9)
    d.text(11.4,.65,'Forced frontier: F(w) = q > r',size=9)
    d.save(out,'retirement-frontier')

def paths(out):
    d=Drawing((-1.1,0.,15.1,9.75))
    nodes=[('trace',1.65,5.2,2.8,1.15,'Admitted\nhistory',10,'neutral'),
           ('producer',5.4,7.65,2.8,1.15,'Frontier\nanalysis',10,'blue'),
           ('certificate',9.15,7.65,2.8,1.15,'Positive\ncertificate',10,'blue'),
           ('consumer',13.0,7.65,3.0,1.15,'Separate\nverification',10,'blue'),
           ('cut',5.4,5.2,2.8,1.15,'Legal\ncrash cut',10,'neutral'),
           ('image',9.15,5.2,2.8,1.15,'Materialized\ncrash image',10,'neutral'),
           ('recovery',13.0,5.2,3.0,1.15,'Model\nrecovery',10,'neutral'),
           ('alternate',5.4,3.25,2.8,1.,'Pairwise / SMT\ncomparison',9.5,'neutral')]
    for row in nodes:d.box(*row)
    for a,b in [('producer','certificate'),('certificate','consumer'),('trace','cut'),('cut','image'),('image','recovery')]:
        d.edge(a,b,'e','w')
    d.arrow(d.point('trace','e'),d.point('producer','w'),via=[(3.5,5.2),(3.5,7.65)])
    d.arrow(d.point('trace','n'),d.point('consumer','n'),via=[(1.65,8.95),(13.,8.95)])
    d.text(7.3,9.35,'Original history supplied to the verifier',size=9)
    d.arrow(d.point('trace','s'),d.point('alternate','w'),via=[(1.65,3.25)])
    for row in [('sqlite',1.65,1.25,2.8,1.15,'SQLite\ncommits',10,'orange'),
                ('tables',5.4,1.25,2.8,1.15,'Retained\nslot and root tables',9.5,'orange'),
                ('native',9.15,1.25,2.8,1.15,'Native\nrecovery',10,'orange'),
                ('compare',13.,1.25,3.,1.15,'Compare prefix\nand object bytes',9.5,'orange')]:
        d.box(*row)
    for a,b in [('sqlite','tables'),('tables','native'),('native','compare')]:
        d.edge(a,b,'e','w')
    d.edge('recovery','compare','s','n')
    d.arrow(d.point('sqlite','w'),d.point('trace','w'),
            via=[(-.7,1.25),(-.7,5.2)],dashed=True)
    d.text(.05,2.25,'Audit\nprojection',ha='left',size=8.5)
    d.save(out,'evidence-paths')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--font-root', type=Path, required=True)
    args = parser.parse_args()
    vector.register_font(args.font_root)
    args.out.mkdir(parents=True, exist_ok=True)
    frontier(args.out)
    paths(args.out)


if __name__ == '__main__':
    main()

