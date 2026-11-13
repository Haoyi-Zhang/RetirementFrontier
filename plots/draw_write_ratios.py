"""Draw retained serialized-write medians; no experiments are run.

Geometry is constructed in Python with ReportLab. Labels use the installed
Linux Libertine 5.3 regular Type 1 font, embedded in full. Its ASCII advances
are retained beside this source. This is the native pdfLaTeX face and the
Type 1 counterpart of the XeLaTeX manuscript's OpenType Libertine face.
Font files are not vendored or relicensed.

Run from any directory with --font-root <TeX distribution fonts directory>.
Optional --column-width-pt follows the manuscript's actual TeX column width;
the default comes from its retained compilation log. TeX pt -> PDF bp is
72/72.27. Figure heights follow the original plotting declarations.
"""
import argparse
import csv
import math
from decimal import Decimal
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas
from write_ratio_data import retained_rows

HERE = Path(__file__).resolve().parent
PAPER = HERE.parent
OUTPUT = None
INPUT = PAPER / "results/scaling/policy-metrics.jsonl"
FONT = "ManuscriptRoman"
PT = 72 / 72.27
CM = 72 / 2.54
BLUE = (.12, .27, .41)
SLATE = (.32, .39, .46)
MID = (.49, .56, .63)
GREY = (.81, .83, .85)
TICK = 9.75
LABEL = 10.25


def register_font(root):
    pfb = Path(root) / "type1/public/libertine/LinLibertineT.pfb"
    face = pdfmetrics.EmbeddedType1Face(
        str(HERE / "libertine-labels.afm"), str(pfb))
    pdfmetrics.registerTypeFace(face)
    font = pdfmetrics.Font(FONT, face.name, "WinAnsiEncoding")
    font.substitutionFonts = []  # Missing labels must not introduce Helvetica.
    pdfmetrics.registerFont(font)


def new_pdf(name, width, height):
    return canvas.Canvas(str(OUTPUT / name), pagesize=(width, height),
                         initialFontName=FONT, initialFontSize=8,
                         pageCompression=1, invariant=1)


def text(c, x, y, label, size=8, align="center"):
    # The reviewed labels use ASCII; never silently substitute a font.
    if any(ord(ch) < 32 or ord(ch) > 126 for ch in label):
        raise ValueError("Label requires explicit native glyph support")
    c.setFont(FONT, size)
    c.setFillColorRGB(0, 0, 0)
    if align == "left":
        c.drawString(x, y, label)
    elif align == "right":
        c.drawRightString(x, y, label)
    else:
        c.drawCentredString(x, y, label)


def axes(c, box, xrange, yrange, xticks, yticks, xlabel, ylabel,
         logx=False, logy=False, grid=True, ticksize=8, labelsize=9):
    left, bottom, width, height = box
    transform_x = math.log10 if logx else float
    transform_y = math.log10 if logy else float
    xa, xb = map(transform_x, xrange)
    ya, yb = map(transform_y, yrange)

    def xy(x, y):
        return (left + width * (transform_x(x) - xa) / (xb - xa),
                bottom + height * (transform_y(y) - ya) / (yb - ya))

    c.setLineWidth(.35)
    if grid:
        c.setStrokeColorRGB(.86, .86, .86)
        for value, _ in yticks:
            _, y = xy(xrange[0], value)
            c.line(left, y, left + width, y)
    c.setStrokeColorRGB(.28, .28, .28)
    c.rect(left, bottom, width, height, fill=0, stroke=1)
    for value, label in xticks:
        x, _ = xy(value, yrange[0])
        c.line(x, bottom, x, bottom - 3)
        text(c, x, bottom - 13, label, ticksize)
    for value, label in yticks:
        _, y = xy(xrange[0], value)
        c.line(left - 3, y, left, y)
        text(c, left - 6, y - 2.4, label, ticksize, "right")
    text(c, left + width / 2, bottom - 28, xlabel, labelsize)
    if ylabel:
        c.saveState()
        c.translate(left - 26, bottom + height / 2)
        c.rotate(90)
        text(c, 0, 0, ylabel, labelsize)
        c.restoreState()
    return xy


def legend(c, items, width, y, size=8):
    # Compact one-row native-font legend; no in-figure title or footer.
    lengths = [14 + pdfmetrics.stringWidth(name, FONT, size)
               for name, _ in items]
    total = sum(lengths) + 14 * (len(items) - 1)
    if total > width - 8:
        raise ValueError("Legend would exceed the figure width")
    x = (width - total) / 2
    c.setLineWidth(.5)
    for j, ((name, colour), length) in enumerate(zip(items, lengths)):
        policy_bar(c, x, y, 8, 5, colour, hatched=(j==1))
        text(c, x + 12, y - .1, name, size, "left")
        x += length + 14


def policy_bar(c, x, y, width, height, colour, hatched):
    c.setFillColorRGB(*colour)
    c.setStrokeColorRGB(.23, .28, .32)
    c.setLineWidth(.52)
    c.rect(x, y, width, height, stroke=1, fill=1)
    if hatched and height > 0:
        c.saveState()
        p = c.beginPath()
        p.rect(x+.3,y+.3,width-.6,height-.6)
        c.clipPath(p,stroke=0,fill=0)
        c.setStrokeColorRGB(.93,.95,.97)
        c.setLineWidth(.44)
        for n in range(-math.ceil(height),math.ceil(width)+1,5):
            c.line(x+n,y,x+n+height,y+height)
        c.restoreState()


def line_points(c, points, shape="circle", radius=1.8, connect=True):
    c.setStrokeColorRGB(0, 0, 0)
    c.setLineWidth(.6)
    if connect:
        p = c.beginPath()
        p.moveTo(*points[0])
        for point in points[1:]:
            p.lineTo(*point)
        c.drawPath(p)
    c.setFillColorRGB(1, 1, 1)
    for x, y in points:
        if shape == "square":
            c.rect(x - radius, y - radius, 2 * radius, 2 * radius,
                   fill=1, stroke=1)
        elif shape == "triangle":
            p = c.beginPath()
            p.moveTo(x, y + radius)
            p.lineTo(x - radius, y - radius)
            p.lineTo(x + radius, y - radius)
            p.close()
            c.drawPath(p, fill=1, stroke=1)
        else:
            c.circle(x, y, radius, fill=1, stroke=1)


def bars(c, xy, categories, series, colours, barwidth):
    for i, category in enumerate(categories):
        centre, baseline = xy(i, 0)
        for j, values in enumerate(series):
            top = xy(i, float(values[i]))[1]
            x = centre + (j - (len(series) - 1) / 2) * barwidth
            c.setFillColorRGB(*colours[j])
            c.setStrokeColorRGB(.18, .23, .28)
            c.setLineWidth(.45)
            # Zero-height bars remain at baseline, never given invented height.
            drawn_width = .82*barwidth
            c.rect(x - drawn_width / 2, baseline, drawn_width, top - baseline,
                   stroke=1, fill=1)
            label,size=f'{float(values[i]):.2f}',11
            baseline_y=top+5.5
            ascent,descent=pdfmetrics.getAscentDescent(FONT,size)
            half_width=pdfmetrics.stringWidth(label,FONT,size)/2
            # Keep the pale grid out of the glyphs without obscuring the bar.
            c.setFillColorRGB(1,1,1)
            c.rect(x-half_width-1.2, baseline_y+descent-.45, 2*half_width+2.4,
                   ascent-descent+.9, fill=1, stroke=0)
            text(c, x, baseline_y, label, size)


def table(path, delimiter=None):
    with path.open(newline="", encoding="utf-8") as stream:
        if delimiter:
            return list(csv.DictReader(stream, delimiter=delimiter))
        names = stream.readline().split()
        return [dict(zip(names, line.split())) for line in stream
                if line.strip()]


def main_args(default_width):
    global INPUT, OUTPUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font-root", required=True, type=Path)
    parser.add_argument("--column-width-pt", default=default_width, type=float)
    parser.add_argument("--input", type=Path, default=INPUT)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    INPUT = args.input
    OUTPUT = args.output_dir
    if not math.isfinite(args.column_width_pt) or args.column_width_pt <= 100:
        raise ValueError("Column width must be finite and greater than 100 TeX pt")
    if OUTPUT.exists() and any(OUTPUT.iterdir()):
        raise ValueError("Choose a new, empty figure output directory")
    register_font(args.font_root)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    return args.column_width_pt * PT

def draw(width):
    rows=retained_rows(INPUT)
    keys=('no_dedup','sync_metadata','batch_four_dedup')
    colours=((.91,.92,.93),(.67,.70,.73),BLUE)
    height=176.
    c=new_pdf('write-ratios.pdf',width,height)
    left,right,base,top=49.,width-8.,29.,147.
    step=(right-left)/3
    def position(y):
        return base+(top-base)*y/6.6
    for tick in (0,1,2,4,6):
        y=position(tick)
        c.setStrokeColorRGB(*((.43,.49,.54) if tick in (0,1) else (.88,.89,.90)))
        c.setLineWidth(.8 if tick==0 else .58)
        if tick==1:
            c.setDash(3,2.2)
        c.line(left,y,right,y)
        c.setDash()
        text(c,left-6,y-3.1,str(tick),TICK,'right')
    for i,row in enumerate(rows):
        centre=left+(i+.5)*step
        text(c,centre,11,row['family'],LABEL)
        for j,(key,colour) in enumerate(zip(keys,colours)):
            value=float(Decimal(row[key]))
            x=centre+(j-1)*27
            y=position(value)
            policy_bar(c,x-8,base,16,y-base,colour,hatched=(j==1))
            label=f'{value:.2f}'
            bw=pdfmetrics.stringWidth(label,FONT,TICK)
            c.setFillColorRGB(1,1,1)
            c.rect(x-bw/2-.7,y+3,bw+1.4,TICK,stroke=0,fill=1)
            text(c,x,y+4.4,label,TICK)
    c.saveState()
    c.translate(17,(base+top)/2)
    c.rotate(90)
    text(c,0,0,'Encoded bytes / logical input byte',LABEL)
    c.restoreState()
    legend(c,list(zip(('No deduplication','Per-operation deduplication',
                        'Batch-four deduplication'),colours)),width,height-12,TICK)
    c.save()


if __name__ == "__main__":
    draw(main_args(430.00462))
