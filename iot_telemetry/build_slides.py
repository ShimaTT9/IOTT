#!/usr/bin/env python3
"""Build the presentation (PowerPoint .pptx) for the IoT telemetry project.

    python3 build_slides.py                         # deck with empty result slides
    python3 build_slides.py --results results       # deck with your measured results

With --results, slides 7-9 and 11 get native PowerPoint charts and the
numbers from results/summary.json and results/timeline.json (written by
analyze.py). Charts stay editable in PowerPoint (right-click > Edit Data).

Needs python-pptx:  pip3 install python-pptx
"""

import argparse
import json
import os
import statistics
import sys

try:
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData, XyChartData
    from pptx.dml.color import RGBColor
    from pptx.enum.chart import (XL_AXIS_CROSSES, XL_CHART_TYPE, XL_LABEL_POSITION, XL_LEGEND_POSITION,
                                 XL_MARKER_STYLE, XL_TICK_MARK, XL_TICK_LABEL_POSITION)
    from pptx.enum.dml import MSO_LINE
    from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
    from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
    from pptx.opc.constants import RELATIONSHIP_TYPE as RT
    from pptx.oxml.ns import qn
    from pptx.oxml.xmlchemy import OxmlElement
    from pptx.util import Inches, Pt
except ImportError:
    sys.exit("python-pptx is missing. Install it with:  pip3 install python-pptx")

import config

# ------------------------------------------------------------------ style ---
NAVY = "0F1A2B"        # dark slides, dark cards, table headers
BG = "F5F5F3"          # light slide background
CARD = "FFFFFF"
CARD_LINE = "E2E3E0"
INK = "1A2333"
MUTED = "5B6575"
ON_DARK = "E9EDF2"
ON_DARK_MUTED = "A3AEBD"
DARK_RULE = "2C3A4F"
EYEBROW = "F0A35E"
BLUE = "2563C9"        # QoS 0 everywhere (charts, dots, labels)
ORANGE = "E07B2E"      # QoS 1 everywhere
GRID = "DADDE2"
TINT_BLUE = "E3EBF8"
PLACE_FILL = "ECEEF1"
PLACE_LINE = "8A96A6"
HEAD = "+mj-lt"        # theme heading font (Segoe UI Semibold)
BODY = "+mn-lt"        # theme body font (Segoe UI)
MONO = "Consolas"
COURSE = "977-337 Network Programming and Simulation"
SLIDE_W, SLIDE_H = 13.333, 7.5
L, R = 0.89, 12.44     # content left / right edge (inches)
TOP = 1.72             # content top


def rgb(hex_color):
    return RGBColor.from_string(hex_color)


# ------------------------------------------------------------ primitives ---
def apply_theme(prs):
    """Write fonts and colours into the theme, so new text boxes match."""
    part = prs.slide_master.part.part_related_by(RT.THEME)
    xml = part.blob.decode("utf-8")
    from lxml import etree
    root = etree.fromstring(xml.encode("utf-8"))
    ns = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
    root.find(".//a:majorFont/a:latin", ns).set("typeface", "Segoe UI Semibold")
    root.find(".//a:minorFont/a:latin", ns).set("typeface", "Segoe UI")
    scheme = root.find(".//a:clrScheme", ns)
    scheme.set("name", "IoT Telemetry")
    colors = {"dk1": INK, "lt1": "FFFFFF", "dk2": NAVY, "lt2": BG, "accent1": BLUE,
              "accent2": ORANGE, "accent3": "3E8E7E", "accent4": "7A5CC2",
              "accent5": MUTED, "accent6": EYEBROW, "hlink": BLUE, "folHlink": "7A5CC2"}
    for tag, value in colors.items():
        slot = scheme.find("a:" + tag, ns)
        for child in list(slot):
            slot.remove(child)
        etree.SubElement(slot, qn("a:srgbClr")).set("val", value)
    part._blob = etree.tostring(root, xml_declaration=True, encoding="UTF-8",
                                standalone=True)


def background(slide, color):
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = rgb(color)


def box(slide, x, y, w, h, fill=CARD, line=CARD_LINE, radius=0.12, line_w=0.75,
        dash=False, name=None, shape=None):
    kind = shape or (MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE)
    shp = slide.shapes.add_shape(kind, Inches(x), Inches(y), Inches(w), Inches(h))
    if kind == MSO_SHAPE.ROUNDED_RECTANGLE:
        shp.adjustments[0] = radius / min(w, h)
    if fill:
        shp.fill.solid()
        shp.fill.fore_color.rgb = rgb(fill)
    else:
        shp.fill.background()
    if line:
        shp.line.color.rgb = rgb(line)
        shp.line.width = Pt(line_w)
        if dash:
            shp.line.dash_style = MSO_LINE.DASH
    else:
        shp.line.fill.background()
    shp.shadow.inherit = False
    if name:
        shp.name = name
    return shp


def text(slide, x, y, w, h, paras, size=14, color=INK, font=BODY, bold=False,
         align="l", anchor="t", space_after=0, line_spacing=None, name=None):
    """paras: a string, or a list of paragraphs; a paragraph is a string or a
    list of runs; a run is a string or (string, {size, color, font, bold, italic})."""
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.auto_size = MSO_AUTO_SIZE.NONE
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = {"t": MSO_ANCHOR.TOP, "m": MSO_ANCHOR.MIDDLE,
                          "b": MSO_ANCHOR.BOTTOM}[anchor]
    fill_paragraphs(tf, paras, size, color, font, bold, align, space_after, line_spacing)
    if name:
        tb.name = name
    return tb


def fill_paragraphs(tf, paras, size, color, font, bold, align, space_after=0,
                    line_spacing=None):
    if isinstance(paras, str):
        paras = [paras]
    for i, para in enumerate(paras):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = {"l": PP_ALIGN.LEFT, "c": PP_ALIGN.CENTER, "r": PP_ALIGN.RIGHT}[align]
        if space_after:
            p.space_after = Pt(space_after)
        if line_spacing:
            p.line_spacing = line_spacing
        runs = [para] if isinstance(para, (str, tuple)) else para
        for run in runs:
            content, opts = (run, {}) if isinstance(run, str) else run
            r = p.add_run()
            r.text = content
            f = r.font
            f.size = Pt(opts.get("size", size))
            f.bold = opts.get("bold", bold)
            f.italic = opts.get("italic", False)
            f.name = opts.get("font", font)
            f.color.rgb = rgb(opts.get("color", color))


def title(slide, words, dark=False, y=0.72, size=36):
    t = slide.shapes.title
    t.left, t.top, t.width, t.height = Inches(L), Inches(y), Inches(R - L), Inches(0.8)
    tf = t.text_frame
    tf.clear()
    tf.word_wrap = True
    tf.auto_size = MSO_AUTO_SIZE.NONE
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = MSO_ANCHOR.TOP
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.LEFT
    r = p.add_run()
    r.text = words
    r.font.size = Pt(size)
    r.font.bold = False
    r.font.name = HEAD
    r.font.color.rgb = rgb("FFFFFF" if dark else INK)
    return t


def footer(slide, number):
    text(slide, L, 6.86, 7, 0.3, COURSE, size=11, color=MUTED)
    text(slide, R - 1, 6.86, 1, 0.3, str(number), size=11, color=MUTED, align="r")


def arrow(slide, x1, y1, x2, y2, color=MUTED, width=1.25, dash=False, head=False, tail=True):
    c = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1),
                                   Inches(x2), Inches(y2))
    c.line.color.rgb = rgb(color)
    c.line.width = Pt(width)
    if dash:
        c.line.dash_style = MSO_LINE.DASH
    ln = c.line._get_or_add_ln()
    for tag, on in (("a:headEnd", head), ("a:tailEnd", tail)):
        if on:
            end = OxmlElement(tag)
            end.set("type", "triangle")
            end.set("w", "med")
            end.set("len", "med")
            ln.append(end)
    return c


def dot(slide, x, y, d, color):
    return box(slide, x, y, d, d, fill=color, line=None, radius=0, shape=MSO_SHAPE.OVAL)


def card(slide, x, y, w, h, heading, body, dark=False, heading_dot=None, size=14):
    box(slide, x, y, w, h, fill=NAVY if dark else CARD, line=None if dark else CARD_LINE)
    hx = x + 0.25
    if heading_dot:
        dot(slide, hx, y + 0.31, 0.16, heading_dot)
        hx += 0.28
    text(slide, hx, y + 0.2, w - (hx - x) - 0.25, 0.4, heading, size=20, font=HEAD,
         color="FFFFFF" if dark else INK)
    text(slide, x + 0.25, y + 0.66, w - 0.5, h - 0.8, body, size=size,
         color=ON_DARK_MUTED if dark else MUTED)


def table(slide, x, y, col_w, rows, row_h=0.4, size=14):
    shape = slide.shapes.add_table(len(rows), len(col_w), Inches(x), Inches(y),
                                   Inches(sum(col_w)), Inches(row_h * len(rows)))
    tbl = shape.table
    tbl.first_row = True
    tbl.horz_banding = False
    style = shape._element.graphic.graphicData.tbl.tblPr.find(qn("a:tableStyleId"))
    if style is not None:
        style.text = "{2D5ABB26-0587-4C30-8999-92F81FD0307C}"   # No Style, No Grid
    for i, w in enumerate(col_w):
        tbl.columns[i].width = Inches(w)
    for r_i, row in enumerate(rows):
        tbl.rows[r_i].height = Inches(row_h)
        for c_i, value in enumerate(row):
            cell = tbl.cell(r_i, c_i)
            cell.margin_left = cell.margin_right = Inches(0.12)
            cell.margin_top = cell.margin_bottom = Inches(0.03)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            cell.fill.solid()
            cell.fill.fore_color.rgb = rgb(NAVY if r_i == 0 else CARD)
            tf = cell.text_frame
            tf.word_wrap = True
            fill_paragraphs(tf, value, size, "FFFFFF" if r_i == 0 else INK,
                            HEAD if r_i == 0 else BODY, False, "l")
            cell_border(cell, NAVY if r_i == 0 else "D5D8DD")
    return shape


def cell_border(cell, color, width_pt=0.75):
    tcPr = cell._tc.get_or_add_tcPr()
    for tag in ("a:lnB", "a:lnT", "a:lnR", "a:lnL"):     # inserted at 0 -> L, R, T, B
        old = tcPr.find(qn(tag))
        if old is not None:
            tcPr.remove(old)
        ln = OxmlElement(tag)
        ln.set("w", str(int(width_pt * 12700)))
        ln.set("cap", "flat")
        ln.set("cmpd", "sng")
        ln.set("algn", "ctr")
        solid = OxmlElement("a:solidFill")
        clr = OxmlElement("a:srgbClr")
        clr.set("val", color)
        solid.append(clr)
        ln.append(solid)
        dash = OxmlElement("a:prstDash")
        dash.set("val", "solid")
        ln.append(dash)
        tcPr.insert(0, ln)


def placeholder(slide, x, y, w, h, label, hint):
    box(slide, x, y, w, h, fill=PLACE_FILL, line=PLACE_LINE, dash=True, radius=0.1)
    text(slide, x + 0.3, y + h / 2 - 0.55, w - 0.6, 1.1,
         [[(label, {"bold": True, "color": BLUE, "size": 13})],
          [(hint, {"color": MUTED, "size": 14})]], align="c", space_after=4)


def stat(slide, x, y, w, h, label, value, color_dot=None, value_size=36):
    box(slide, x, y, w, h)
    lx = x + 0.25
    if color_dot:
        dot(slide, lx, y + 0.27, 0.14, color_dot)
        lx += 0.24
    text(slide, lx, y + 0.18, w - (lx - x) - 0.2, 0.35, label, size=14, color=MUTED)
    text(slide, x + 0.25, y + 0.52, w - 0.5, h - 0.6, value, size=value_size,
         font=HEAD, color=INK)


# ------------------------------------------------------------------ charts ---
def style_axis_text(axis, size=12, color=MUTED):
    axis.tick_labels.font.size = Pt(size)
    axis.tick_labels.font.color.rgb = rgb(color)
    axis.tick_labels.font.name = BODY


def chart_title(chart, words, size=14):
    chart.has_title = True
    tf = chart.chart_title.text_frame
    tf.text = words
    for p in tf.paragraphs:
        p.alignment = PP_ALIGN.LEFT
        for r in p.runs:
            r.font.size = Pt(size)
            r.font.bold = False
            r.font.name = HEAD
            r.font.color.rgb = rgb(INK)


def bar_chart(slide, x, y, w, h, heading, cases, metric, number_format, ymax=None):
    data = CategoryChartData()
    data.categories = ["Stationary", "Mobile"]
    for qos in (0, 1):
        vals = []
        for scenario in ("stationary", "mobile"):
            c = cases.get("%s_qos%d" % (scenario, qos))
            vals.append(c[metric] if c and c[metric] is not None else None)
        data.add_series("QoS %d" % qos, vals)
    frame = slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(x), Inches(y),
                                   Inches(w), Inches(h), data)
    ch = frame.chart
    ch.font.name = BODY
    ch.font.size = Pt(12)
    ch.font.color.rgb = rgb(MUTED)
    chart_title(ch, heading)
    ch.has_legend = True
    ch.legend.position = XL_LEGEND_POSITION.BOTTOM
    ch.legend.include_in_layout = False
    ch.legend.font.size = Pt(12)
    ch.legend.font.color.rgb = rgb(INK)
    plot = ch.plots[0]
    plot.gap_width = 70
    plot.overlap = -6
    plot.has_data_labels = True
    labels = plot.data_labels
    labels.number_format = number_format
    labels.number_format_is_linked = False
    labels.position = XL_LABEL_POSITION.OUTSIDE_END
    labels.font.size = Pt(12)
    labels.font.color.rgb = rgb(INK)
    for series, color in zip(plot.series, (BLUE, ORANGE)):
        series.format.fill.solid()
        series.format.fill.fore_color.rgb = rgb(color)
        series.format.line.fill.background()
        series.invert_if_negative = False
    va = ch.value_axis
    va.minimum_scale = 0
    if ymax:
        va.maximum_scale = ymax
    va.has_major_gridlines = True
    va.major_gridlines.format.line.color.rgb = rgb(GRID)
    va.major_gridlines.format.line.width = Pt(0.75)
    va.format.line.fill.background()
    va.major_tick_mark = XL_TICK_MARK.NONE
    style_axis_text(va, 11)
    ca = ch.category_axis
    ca.format.line.color.rgb = rgb(GRID)
    ca.major_tick_mark = XL_TICK_MARK.NONE
    style_axis_text(ca, 13, INK)
    return ch


def nice_step(top):
    """A round gridline step that gives 4-6 lines up to top."""
    for step in (5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000):
        if top / step <= 6:
            return step
    return 5000


def latency_chart(slide, x, y, w, h, timeline):
    """One scatter chart: a dot per delivered reading (one-way latency) and a
    cross below zero for every lost reading, coloured by QoS level."""
    levels = [q for q in (0, 1) if timeline.get("qos%d" % q)]
    points = [p for q in levels for p in timeline["qos%d" % q]["points"]]
    xmax = 5 * int(max(p[0] for p in points) / 5 + 1)
    step = nice_step(max(20.0, max(p[1] for p in points)))
    ymax = step * (int(max(p[1] for p in points) / step) + 1)
    data = XyChartData()
    styles = []
    for q in levels:
        s = data.add_series("QoS %d delivered" % q)
        for t, latency in timeline["qos%d" % q]["points"]:
            s.add_data_point(t, latency)
        styles.append((XL_MARKER_STYLE.CIRCLE, 4, BLUE if q == 0 else ORANGE))
    for q in levels:
        lost = timeline["qos%d" % q]["lost"]
        if lost:
            s = data.add_series("QoS %d lost (%d)" % (q, len(lost)))
            for t in lost:
                s.add_data_point(t, -step * (0.3 if q == 0 else 0.7))
            styles.append((XL_MARKER_STYLE.X, 7, BLUE if q == 0 else ORANGE))
    frame = slide.shapes.add_chart(XL_CHART_TYPE.XY_SCATTER, Inches(x), Inches(y),
                                   Inches(w), Inches(h), data)
    ch = frame.chart
    ch.font.name = BODY
    ch.font.size = Pt(11)
    ch.font.color.rgb = rgb(MUTED)
    chart_title(ch, "Latency per reading, one mobile run (ms)")
    ch.has_legend = True
    ch.legend.position = XL_LEGEND_POSITION.BOTTOM
    ch.legend.include_in_layout = False
    ch.legend.font.size = Pt(11)
    ch.legend.font.color.rgb = rgb(INK)
    for s, (marker, size, color) in zip(ch.plots[0].series, styles):
        s.format.line.fill.background()
        s.marker.style = marker
        s.marker.size = size
        if marker == XL_MARKER_STYLE.X:
            s.marker.format.fill.background()       # an outline-only cross
            s.marker.format.line.width = Pt(1.25)
        else:
            s.marker.format.fill.solid()
            s.marker.format.fill.fore_color.rgb = rgb(color)
        s.marker.format.line.color.rgb = rgb(color)
        s.smooth = False
    xa, ya = ch.category_axis, ch.value_axis      # both are value axes for XY
    xa.minimum_scale, xa.maximum_scale = 0, xmax
    xa.major_unit = 5
    xa.has_major_gridlines = False
    xa.format.line.color.rgb = rgb(GRID)
    xa.major_tick_mark = XL_TICK_MARK.OUTSIDE
    xa.tick_labels.number_format = '0" s"'
    xa.tick_labels.number_format_is_linked = False
    style_axis_text(xa, 11)
    ya.minimum_scale, ya.maximum_scale, ya.major_unit = -step, ymax, step
    ya.crosses = XL_AXIS_CROSSES.MINIMUM          # time axis at the bottom
    ya.has_major_gridlines = True
    ya.major_gridlines.format.line.color.rgb = rgb(GRID)
    ya.format.line.fill.background()
    ya.major_tick_mark = XL_TICK_MARK.NONE
    ya.tick_labels.number_format = '0;"lost";0'   # the band below 0 holds the crosses
    ya.tick_labels.number_format_is_linked = False
    style_axis_text(ya, 11)
    return ch


# ------------------------------------------------------------------ slides ---
def new_slide(prs, color=BG):
    slide = prs.slides.add_slide(prs.slide_layouts[5])     # "Title Only"
    background(slide, color)
    return slide


def notes(slide, words):
    slide.notes_slide.notes_text_frame.text = words


def cover(prs, eyebrow, heading, subtitle, speaker_notes):
    s = new_slide(prs, NAVY)
    text(s, L, 0.95, 11, 0.35, eyebrow, size=14, color=EYEBROW, bold=True)
    title(s, heading, dark=True, y=1.32, size=54)
    s.shapes.title.height = Inches(1.1)
    text(s, L, 2.5, 11, 0.5, subtitle, size=20, color=ON_DARK_MUTED)
    rule = s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(L), Inches(5.1),
                                  Inches(R), Inches(5.1))
    rule.line.color.rgb = rgb(DARK_RULE)
    rule.line.width = Pt(1)
    cols = [("Presented by", "Patcharakorn Zoonboon", "6630613034"),
            ("Course", "977-337", "Network Programming\nand Simulation"),
            ("Instructor", "Kuljaree Tantayakul", "")]
    for i, (label, main, sub) in enumerate(cols):
        x = L + i * 4.0
        text(s, x, 5.38, 3.6, 0.3, label, size=13, color=ON_DARK_MUTED)
        text(s, x, 5.72, 3.6, 0.4, main, size=17, color="FFFFFF", font=HEAD)
        if sub:
            text(s, x, 6.1, 3.6, 0.7, sub.split("\n"), size=16, color=ON_DARK)
    notes(s, speaker_notes)
    return s


def slide_problem(prs):
    s = new_slide(prs)
    title(s, "Problem statement")
    cards = [("Sensors move", "Drones and vehicles carry sensors that report readings "
              "over WiFi while they travel.", False),
             ("Links break", "Signal fades with distance, and the node has to hand over "
              "from one access point to the next.", False),
             ("The question", "Is acknowledging every message (QoS 1) worth its cost "
              "compared with send-and-forget (QoS 0)?", True)]
    for i, (h, b, dark) in enumerate(cards):
        card(s, L, TOP + i * 1.42, 5.85, 1.24, h, b, dark=dark)

    # right: what happens on the road
    px, pw, py, ph = 7.15, R - 7.15, TOP, 2 * 1.42 + 1.24     # same height as the cards
    box(s, px, py, pw, ph)
    text(s, px + 0.3, py + 0.25, pw - 0.6, 0.3, "A sensor driving from ap1 to ap2",
         size=14, color=MUTED)
    scale = (pw - 0.6) / 110.0
    x0, cy = px + 0.3, py + 1.8
    rad = config.AP_RANGE * scale
    for name, (ax_m, _, _) in sorted(config.ACCESS_POINTS.items()):
        cx = x0 + ax_m * scale
        box(s, cx - rad, cy - rad, 2 * rad, 2 * rad, fill=TINT_BLUE, line=BLUE,
            dash=True, radius=0, shape=MSO_SHAPE.OVAL)
        dot(s, cx - 0.11, cy - 0.11, 0.22, NAVY)
        text(s, cx - 0.5, cy + 0.17, 1.0, 0.3, name, size=13, color=INK, align="c")
    lane = cy + 0.62
    arrow(s, x0 + config.PATH_START_X * scale, lane, x0 + config.PATH_END_X * scale, lane,
          color=INK, width=1.5)
    box(s, x0 + config.PATH_START_X * scale - 0.14, lane - 0.14, 0.28, 0.28,
        fill=ORANGE, line=None, radius=0.06)
    text(s, x0 + config.PATH_START_X * scale - 0.3, lane + 0.2, 1.2, 0.3, "sensor",
         size=12, color=INK)
    gx = x0 + 55 * scale
    arrow(s, gx, cy - rad - 0.05, gx, lane + 0.45, color=ORANGE, width=1.5,
          dash=True, tail=False)
    text(s, gx - 1.6, lane + 0.55, 3.2, 0.6,
         [[("Handover zone", {"bold": True, "color": INK})],
          "readings sent here are at risk"], size=13, color=MUTED, align="c")
    notes(s, "เซนเซอร์ติดอยู่กับโดรนหรือยานพาหนะ ส่งค่าที่อ่านได้ผ่าน WiFi ตลอดเวลาที่เคลื่อนที่ "
             "เมื่อออกห่างจาก AP สัญญาณจะอ่อนลง และต้อง handover ไปยัง AP ตัวถัดไป "
             "ช่วงที่สลับ AP นี้ข้อความอาจหายได้ คำถามของโปรเจกต์คือ การให้ collector ตอบ ACK "
             "ทุกข้อความ (QoS 1) คุ้มกับต้นทุนที่เพิ่มขึ้นหรือไม่ เมื่อเทียบกับการส่งแล้วไม่รอคำตอบ (QoS 0)")


def slide_architecture(prs):
    s = new_slide(prs)
    title(s, "System architecture")
    px, py, pw, ph = L, TOP, 7.05, 4.66
    box(s, px, py, pw, ph)

    def node(x, y, w, h, head, sub, fill=CARD, line=CARD_LINE, ink=INK, sub_ink=MUTED):
        box(s, x, y, w, h, fill=fill, line=line, radius=0.08)
        text(s, x, y + 0.12, w, 0.32, head, size=15, font=HEAD, color=ink, align="c")
        if sub:
            text(s, x + 0.05, y + 0.45, w - 0.1, h - 0.5, sub, size=11, color=sub_ink,
                 align="c")

    rows = (py + 0.75, py + 2.85)
    for i, yy in enumerate(rows):
        node(px + 0.3, yy, 1.75, 0.95, "sta%d" % (i + 1), "sensor.py", fill="FFF4EA",
             line="F2C9A6")
        node(px + 2.75, yy + 0.1, 1.15, 0.75, "ap%d" % (i + 1), "OVS AP", fill=TINT_BLUE,
             line="BCD0F0")
        arrow(s, px + 2.05, yy + 0.475, px + 2.75, yy + 0.475, color=BLUE, width=1.5,
              dash=True, head=True)
        arrow(s, px + 3.9, yy + 0.475, px + 4.55, py + 2.33, color=MUTED, width=1.25,
              tail=False)
    node(px + 4.55, py + 1.95, 0.85, 0.75, "s1", "switch", fill="F1F2F4", line="D5D8DD")
    node(px + 5.85, py + 1.75, 1.0, 1.15, "h1", "collector.py\n10.0.0.100", fill=NAVY,
         line=None, ink="FFFFFF", sub_ink=ON_DARK_MUTED)
    arrow(s, px + 5.4, py + 2.33, px + 5.85, py + 2.33, color=INK, width=2.25,
          head=True)
    text(s, px + 0.3, py + 1.83, 2.4, 0.5, ["WiFi 802.11g", "(Mininet-WiFi)"], size=11,
         color=BLUE)
    text(s, px + 4.3, py + 3.0, 2.6, 0.6, ["s1 - h1: 10 Mbit/s,", "5 ms, queue 100"],
         size=11, color=MUTED, align="c")
    text(s, px + 0.3, py + ph - 0.55, pw - 0.6, 0.35,
         [[("DATA", {"bold": True, "color": INK}), "  48 B, sensor to collector      ",
           ("ACK", {"bold": True, "color": INK}), "  16 B, back to the sensor (QoS 1)"]],
         size=12, color=MUTED)

    cx, cw = 8.24, R - 8.24
    items = [("Sensor (client)", "UDP client. One reading every 0.1 s; in QoS 1 it "
              "waits 0.2 s for an ACK and resends up to 3 times."),
             ("Collector (server)", "UDP server on port 5005. ACKs QoS 1 data, drops "
              "duplicates and logs every packet to CSV."),
             ("Shared protocol", "A 16-byte binary header packed with Python's struct "
              "module (protocol.py).")]
    for i, (h, b) in enumerate(items):
        card(s, cx, TOP + i * 1.62, cw, 1.42, h, b)
    notes(s, "ระบบมีสองโปรแกรม sensor.py ทำงานบน station sta1 และ sta2 เป็น UDP client "
             "ส่งค่าอุณหภูมิและความชื้นทุก 0.1 วินาที ผ่าน AP เข้า switch s1 ไปยัง collector.py "
             "บน h1 ที่ 10.0.0.100 พอร์ต 5005 ถ้าเป็น QoS 1 collector จะส่ง ACK กลับ "
             "ทั้งสองฝั่งใช้ protocol.py ร่วมกันในการแพ็กข้อความด้วย struct")


def slide_protocol(prs):
    s = new_slide(prs)
    title(s, "Custom UDP protocol")
    rows = [["Field", "Size", "Purpose"],
            ["type", "1 byte", "0 = DATA, 1 = ACK"],
            ["qos", "1 byte", "0 or 1"],
            ["sensor_id", "2 bytes", "Which sensor sent it"],
            ["seq", "4 bytes", "Loss and duplicate detection"],
            ["timestamp", "8 bytes", "Latency measurement"],
            ["payload", "32 bytes", "Temperature, humidity, padding"]]
    table(s, L, TOP, [1.6, 1.25, 4.1], rows, row_h=0.4)

    # byte layout of a DATA message
    by = TOP + 3.05
    text(s, L, by, 6.95, 0.3, [[("DATA message on the wire", {"bold": True, "color": INK}),
                                "  (struct format !BBHId + !ff24x)"]], size=13, color=MUTED)
    fields = [("type", 1), ("qos", 1), ("sensor_id", 2), ("seq", 4), ("timestamp", 8)]
    per_byte = 6.95 / 16.0
    x = L
    for name, size in fields:
        w = size * per_byte
        box(s, x, by + 0.42, w, 0.5, fill="E3E8F0", line="FFFFFF", radius=0, line_w=1.5)
        text(s, x, by + 0.42, w, 0.5, name, size=11 if size < 2 else 12, color=INK,
             align="c", anchor="m")
        x += w
    box(s, L, by + 0.92, 6.95, 0.5, fill="F3F1EC", line="FFFFFF", radius=0, line_w=1.5)
    text(s, L, by + 0.92, 6.95, 0.5, "payload: temperature float32 | humidity float32 | "
         "24 B padding", size=12, color=INK, align="c", anchor="m")
    text(s, L, by + 1.5, 6.95, 0.3, "Header 16 B + payload 32 B = 48 B.  "
         "An ACK is the header alone: 16 B.", size=12, color=MUTED)

    cx, cw = 8.24, R - 8.24
    card(s, cx, TOP, cw, 2.2, "QoS 0", "Send once, expect no reply. Lowest overhead, but "
         "a reading lost in the air stays lost.", heading_dot=BLUE)
    text(s, cx + 0.25, TOP + 1.65, cw - 0.5, 0.35,
         [[("76 B", {"bold": True, "color": INK}), " on the network per reading"]],
         size=13, color=MUTED)
    card(s, cx, TOP + 2.42, cw, 2.24, "QoS 1", "The sensor waits 0.2 s for an ACK and "
         "resends up to 3 times. The collector drops duplicates by "
         "(sensor_id, seq, timestamp).", heading_dot=ORANGE)
    text(s, cx + 0.25, TOP + 2.42 + 1.7, cw - 0.5, 0.35,
         [[("76 B + 44 B ACK", {"bold": True, "color": INK}), " per reading, more on resends"]],
         size=13, color=MUTED)
    notes(s, "ข้อความทุกอันมี header 16 ไบต์ คือ type, qos, sensor_id, seq และ timestamp "
             "แพ็กด้วย struct แบบ network byte order ข้อความ DATA มี payload อีก 32 ไบต์ รวม 48 ไบต์ "
             "ส่วน ACK มีแค่ header 16 ไบต์ เมื่อรวม UDP/IP header แล้ว DATA ใช้ 76 ไบต์ ACK ใช้ 44 ไบต์ "
             "QoS 0 ส่งครั้งเดียว QoS 1 รอ ACK 0.2 วินาที ถ้าไม่ได้จะส่งซ้ำสูงสุด 3 ครั้ง "
             "collector ตัดข้อความซ้ำด้วย sensor_id, seq และ timestamp")


def slide_topology(prs):
    s = new_slide(prs)
    title(s, "Mininet-WiFi topology")
    px, py, pw, ph = L, TOP, 6.95, 4.66
    box(s, px, py, pw, ph)
    scale = (pw - 0.6) / 110.0
    x0, cy = px + 0.3, py + 2.45
    ym = lambda y_m: cy + (y_m - 50) * scale          # y in metres -> inches
    rad = config.AP_RANGE * scale
    ap_x = {}
    for name, (ax_m, ay_m, ch) in sorted(config.ACCESS_POINTS.items()):
        cx = x0 + ax_m * scale
        ap_x[name] = cx
        box(s, cx - rad, ym(ay_m) - rad, 2 * rad, 2 * rad, fill=TINT_BLUE, line=BLUE,
            dash=True, radius=0, shape=MSO_SHAPE.OVAL)
    # wired part, drawn above the radio area
    gx = x0 + 55 * scale
    sx, sy = gx - 0.35, py + 0.25
    for cx in ap_x.values():
        arrow(s, cx, cy, sx + 0.35 + (cx - gx) * 0.25, sy + 0.42, color=MUTED,
              width=1, tail=False)
    box(s, sx, sy, 0.7, 0.42, fill="F1F2F4", line="D5D8DD", radius=0.06)
    text(s, sx, sy, 0.7, 0.42, "s1", size=13, font=HEAD, align="c", anchor="m")
    hx = sx + 1.55
    box(s, hx, sy, 1.85, 0.42, fill=NAVY, line=None, radius=0.06)
    text(s, hx, sy, 1.85, 0.42, "h1 collector", size=12, color="FFFFFF", align="c",
         anchor="m")
    arrow(s, sx + 0.7, sy + 0.21, hx, sy + 0.21, color=INK, width=2, tail=False)
    for name, cx in ap_x.items():
        dot(s, cx - 0.1, cy - 0.1, 0.2, NAVY)
        text(s, cx + 0.16, cy - 0.13, 1.3, 0.26,
             "%s · ch %d" % (name, config.ACCESS_POINTS[name][2]), size=12, color=INK)
    for sta, (ip, sid, lane_y) in sorted(config.STATIONS.items()):
        yy = ym(lane_y)
        start_x = x0 + config.PATH_START_X * scale
        arrow(s, start_x, yy, x0 + config.PATH_END_X * scale, yy, color=ORANGE, width=1.25)
        box(s, start_x - 0.1, yy - 0.1, 0.2, 0.2, fill=ORANGE, line=None, radius=0.04)
        text(s, start_x - 0.1, yy - 0.38 if sid == 1 else yy + 0.13, 0.8, 0.24, sta,
             size=11, color=INK)
    arrow(s, gx, ym(50) - rad - 0.05, gx, ym(50) + rad + 0.05, color=ORANGE, width=1.25,
          dash=True, tail=False)
    text(s, gx + 0.1, ym(50) + rad + 0.06, 2.6, 0.26, "handover near x = 55 m",
         size=12, color=INK)
    text(s, x0, py + ph - 0.34, pw - 0.6, 0.26,
         "Circles: %d m AP range. Sensors drive x = %d → %d m in %d s."
         % (config.AP_RANGE, config.PATH_START_X, config.PATH_END_X, config.MOVE_TIME),
         size=12, color=MUTED)

    rows = [["Parameter", "Value"],
            ["Access points", "2 (ap1, ap2), 50 m apart"],
            ["AP range", "%d m, 802.11g, ch 1 / 6" % config.AP_RANGE],
            ["Sensors", "2 WiFi stations"],
            ["Propagation", "logDistance, exp = %.1f" % config.PROPAGATION_EXP],
            ["Mobility", "x = %d → %d m in %d s" % (config.PATH_START_X, config.PATH_END_X,
                                                    config.MOVE_TIME)],
            ["Wired link", "%d Mbit/s, %s delay" % (config.LINK_BW, config.LINK_DELAY.replace("ms", " ms"))],
            ["Queue", "max_queue_size = %d" % config.QUEUE_SIZE]]
    table(s, 8.24, TOP, [1.6, R - 8.24 - 1.6], rows, row_h=0.4, size=13)
    text(s, 8.24, TOP + 3.4, R - 8.24, 1.2,
         "On their lanes the sensors leave ap1's range at x = 54.5 m and "
         "enter ap2's at x = 55.5 m, so every pass forces a handover.", size=14, color=MUTED)
    notes(s, "topology สร้างด้วย Mininet-WiFi มี AP สองตัวห่างกัน 50 เมตร ระยะครอบคลุม 25 เมตร "
             "ใช้ propagation model แบบ logDistance exponent 3.5 สถานีสองตัววิ่งจาก x = 10 ถึง 100 เมตร "
             "ใน 40 วินาที วงครอบคลุมของสอง AP ไม่ทับกันบนเลนที่สถานีวิ่ง จึงต้อง handover ทุกครั้ง "
             "ฝั่งสายเป็น TCLink 10 Mbit/s ดีเลย์ 5 ms และ max_queue_size 100 ปรับได้ด้วย --bw --delay --loss --queue")


def slide_method(prs):
    s = new_slide(prs)
    title(s, "Benchmark methodology")
    cases = [("Case A", "Stationary, QoS 0", BLUE), ("Case B", "Stationary, QoS 1", ORANGE),
             ("Case C", "Mobile, QoS 0", BLUE), ("Case D", "Mobile, QoS 1", ORANGE)]
    for i, (label, name, color) in enumerate(cases):
        x = L + (i % 2) * 2.82
        y = TOP + (i // 2) * 1.2
        box(s, x, y, 2.62, 1.02)
        dot(s, x + 0.25, y + 0.25, 0.13, color)
        text(s, x + 0.47, y + 0.17, 2.0, 0.3, label, size=13, color=MUTED)
        text(s, x + 0.25, y + 0.5, 2.3, 0.4, name, size=17, font=HEAD)
    mx, mw = 6.56, R - 6.56
    box(s, mx, TOP, mw, 2.22, fill=NAVY, line=None)
    text(s, mx + 0.3, TOP + 0.2, mw - 0.6, 0.4, "Metrics", size=20, font=HEAD, color="FFFFFF")
    lines = [("PDR: ", "unique readings delivered ÷ readings sent"),
             ("Handover gap: ", "longest silence at the collector"),
             ("Latency and jitter: ", "receive time − timestamp in the reading"),
             ("Overhead: ", "bytes on the network (DATA + ACK) ÷ payload delivered")]
    text(s, mx + 0.3, TOP + 0.7, mw - 0.6, 1.5,
         [[(a, {"bold": True, "color": ON_DARK}), b] for a, b in lines],
         size=14, color=ON_DARK_MUTED, space_after=5)

    for x, w in ((L, 5.45), (mx, mw)):
        rule = s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x), Inches(4.25),
                                      Inches(x + w), Inches(4.25))
        rule.line.color.rgb = rgb("D5D8DD")
        rule.line.width = Pt(1)
    text(s, L, 4.42, 5.45, 1.0,
         [[("Workload", {"font": HEAD, "color": INK, "size": 15})],
          "2 sensors × 10 readings/s × 40 s per run. Each case runs 3 times; runs are "
          "interleaved and averaged."], size=14, color=MUTED, space_after=4)
    text(s, mx, 4.42, mw, 1.0,
         [[("Tools", {"font": HEAD, "color": INK, "size": 15})],
          "CSV logs from both programs, tcpdump + Wireshark (Lua dissector), ping, iperf3."],
         size=14, color=MUTED, space_after=4)

    steps = [("run_all.sh", "12 runs"), ("results/raw", "CSV logs"),
             ("analyze.py", "metrics + charts"), ("build_slides.py", "these slides")]
    sw, gap = 2.55, (R - L - 4 * 2.55) / 3
    for i, (cmd, what) in enumerate(steps):
        x = L + i * (sw + gap)
        box(s, x, 5.62, sw, 0.62, fill="EEF0F3", line=None, radius=0.08)
        text(s, x, 5.62, sw, 0.62, [[(cmd, {"font": MONO, "color": INK, "size": 13}),
                                     ("  " + what, {"color": MUTED, "size": 12})]],
             align="c", anchor="m")
        if i < 3:
            arrow(s, x + sw + 0.06, 5.93, x + sw + gap - 0.06, 5.93, color=MUTED, width=1.25)
    notes(s, "ทดลอง 4 กรณี คือ อยู่กับที่หรือเคลื่อนที่ คูณกับ QoS 0 หรือ QoS 1 "
             "แต่ละรอบมีเซนเซอร์ 2 ตัว ส่ง 10 ข้อความต่อวินาที นาน 40 วินาที ทำซ้ำกรณีละ 3 รอบแล้วเฉลี่ย "
             "วัด PDR, ช่วงเงียบที่ยาวที่สุดที่ collector ซึ่งก็คือเวลา handover, latency, jitter และ overhead "
             "ทั้งหมดรันอัตโนมัติด้วย run_all.sh แล้ววิเคราะห์ด้วย analyze.py")


def fmt(value, pattern, missing="—"):
    return missing if value is None else pattern % value


def slide_pdr(prs, res):
    s = new_slide(prs)
    title(s, "Results: packet delivery ratio")
    cw = 7.25
    if res:
        box(s, L, TOP, cw, 4.66)
        bar_chart(s, L + 0.15, TOP + 0.15, cw - 0.3, 4.36, "Packet delivery ratio (%)",
                  res["cases"], "pdr_pct", '0.0"%"', ymax=110)
    else:
        placeholder(s, L, TOP, cw, 4.66, "CHART AFTER THE LAB",
                    "PDR bar chart: run analyze.py, then build_slides.py --results results")
    m0, m1 = case(res, "mobile_qos0"), case(res, "mobile_qos1")
    sx, sw = 8.4, R - 8.4
    stat(s, sx, TOP, sw, 1.25, "Mobile, QoS 0", fmt(m0.get("pdr_pct"), "%.1f %%"), BLUE)
    stat(s, sx, TOP + 1.43, sw, 1.25, "Mobile, QoS 1", fmt(m1.get("pdr_pct"), "%.1f %%"), ORANGE)
    s0 = case(res, "stationary_qos0")
    if res and m0 and m1 and s0:
        msg = ("Moving through the handover cut QoS 0 delivery from %.1f %% to %.1f %%. "
               "QoS 1 brought it back to %.1f %%." % (s0["pdr_pct"], m0["pdr_pct"], m1["pdr_pct"]))
    else:
        msg = "[Sentence filled in by build_slides.py from your measured PDR.]"
    text(s, sx, TOP + 2.95, sw, 1.6, msg, size=15, color=INK)
    notes(s, "กราฟนี้คือ PDR ของ 4 กรณี เมื่ออยู่กับที่ ทั้งสอง QoS ส่งได้เกือบครบ "
             "เมื่อเคลื่อนที่ QoS 0 เสียข้อความช่วง handover ส่วน QoS 1 ส่งซ้ำจนกู้คืนได้เกือบทั้งหมด "
             "(อ่านตัวเลขจริงจากกล่องด้านขวา)")


def case(res, key):
    return (res or {}).get("cases", {}).get(key) or {}


def slide_gap(prs, res, timeline):
    s = new_slide(prs)
    title(s, "Results: handover and latency")
    w, h = 5.6, 3.9
    rx = R - w
    if res:
        box(s, L, TOP, w, h)
        bar_chart(s, L + 0.12, TOP + 0.12, w - 0.24, h - 0.24, "Longest gap at the collector (ms)",
                  res["cases"], "longest_gap_ms", '0')
    else:
        placeholder(s, L, TOP, w, h, "CHART AFTER THE LAB", "Longest-gap bar chart")
    if res and timeline and (timeline.get("qos0") or timeline.get("qos1")):
        box(s, rx, TOP, w, h)
        latency_chart(s, rx + 0.12, TOP + 0.12, w - 0.24, h - 0.24, timeline)
    else:
        placeholder(s, rx, TOP, w, h, "CHART AFTER THE LAB", "Latency over time, one mobile run")

    def gap(prefix):
        vals = [c["longest_gap_ms"] for k, c in (res or {}).get("cases", {}).items()
                if k.startswith(prefix) and c.get("longest_gap_ms") is not None]
        return statistics.mean(vals) if vals else None

    m0, m1 = case(res, "mobile_qos0"), case(res, "mobile_qos1")
    stats = [("Longest gap, stationary", fmt(gap("stationary"), "%.0f ms")),
             ("Longest gap, mobile", fmt(gap("mobile"), "%.0f ms")),
             ("Mean latency while moving, QoS 0 vs QoS 1",
              "%s vs %s" % (fmt(m0.get("latency_ms"), "%.1f ms"), fmt(m1.get("latency_ms"), "%.1f ms")))]
    cw = (R - L - 2 * 0.3) / 3
    for i, (label, value) in enumerate(stats):
        x = L + i * (cw + 0.3)
        rule = s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x), Inches(5.83),
                                      Inches(x + cw), Inches(5.83))
        rule.line.color.rgb = rgb("D5D8DD")
        text(s, x, 5.95, cw, 0.3, label, size=13, color=MUTED)
        text(s, x, 6.25, cw, 0.45, value, size=22, font=HEAD, color=INK)
    notes(s, "ซ้ายคือช่วงเงียบที่ยาวที่สุดที่ collector เมื่ออยู่กับที่จะประมาณ 100 ms ตามอัตราส่ง "
             "เมื่อเคลื่อนที่ช่วงเงียบยาวขึ้นมากเพราะ handover ขวาคือ latency ของแต่ละข้อความในรอบที่เคลื่อนที่ "
             "กากบาทคือข้อความที่หาย QoS 1 มีจุดที่ latency สูงขึ้นช่วง handover เพราะเป็นข้อความที่ส่งซ้ำแล้วไปถึงช้า")


def slide_overhead(prs, res):
    s = new_slide(prs)
    title(s, "Results: overhead and cost")
    cw = 7.25
    if res:
        box(s, L, TOP, cw, 4.66)
        bar_chart(s, L + 0.15, TOP + 0.15, cw - 0.3, 4.36,
                  "Bytes on the network per payload byte delivered", res["cases"],
                  "overhead_x", '0.00"×"')
    else:
        placeholder(s, L, TOP, cw, 4.66, "CHART AFTER THE LAB", "Overhead bar chart")
    m0, m1 = case(res, "mobile_qos0"), case(res, "mobile_qos1")
    sx, sw = 8.4, R - 8.4
    stat(s, sx, TOP, sw, 1.25, "Overhead, mobile, QoS 0", fmt(m0.get("overhead_x"), "%.2f ×"), BLUE)
    stat(s, sx, TOP + 1.43, sw, 1.25, "Overhead, mobile, QoS 1", fmt(m1.get("overhead_x"), "%.2f ×"), ORANGE)
    text(s, sx, TOP + 2.95, sw, 1.6,
         [[("Retransmissions per run, mobile, QoS 1: ", {}),
           (fmt(m1.get("retransmissions"), "%.0f", "[__]"), {"bold": True}),
           (". Each one is an extra radio transmission, used here as a stand-in for "
            "energy cost.", {})]], size=15, color=INK)
    notes(s, "overhead คือจำนวนไบต์ที่วิ่งบนเครือข่ายทั้งหมดหารด้วย payload ที่ส่งถึงจริง "
             "QoS 0 ใช้ประมาณ 2.4 เท่า มาจาก header UDP/IP กับ header ของเรา "
             "QoS 1 เพิ่ม ACK อีก 44 ไบต์ต่อข้อความ และการส่งซ้ำ จึงสูงกว่า "
             "จำนวนการส่งซ้ำใช้แทนพลังงานที่ใช้เพิ่มของวิทยุ")


def slide_demo(prs):
    s = new_slide(prs)
    title(s, "Live demo")
    box(s, L, TOP, R - L, 0.72, fill=NAVY, line=None, radius=0.08)
    text(s, L + 0.3, TOP, 0.4, 0.72, "$", size=18, font=MONO, color=EYEBROW, anchor="m")
    text(s, L + 0.65, TOP, 8.6, 0.72,
         "sudo python3 topology.py --scenario mobile --demo --plot",
         size=17, font=MONO, color="FFFFFF", anchor="m")
    text(s, R - 3.2, TOP, 2.95, 0.72, "one command starts everything", size=13,
         color=ON_DARK_MUTED, anchor="m", align="r")
    steps = [("1", "Network starts", "The plot window shows sta1 and sta2 driving from "
              "ap1 towards ap2."),
             ("2", "Handover", "The collector prints \"sensor 1 was silent for ... s\" when "
              "the sensors switch AP."),
             ("3", "Result table", "PDR per sensor: sta1 (QoS 0) loses the readings sent in "
              "the gap; sta2 (QoS 1) recovers most of them.")]
    sw = (R - L - 2 * 0.25) / 3
    for i, (num, head, body) in enumerate(steps):
        x = L + i * (sw + 0.25)
        box(s, x, TOP + 0.95, sw, 1.55)
        text(s, x + 0.25, TOP + 1.15, 0.5, 0.4, num, size=20, font=HEAD, color=EYEBROW)
        text(s, x + 0.62, TOP + 1.15, sw - 0.85, 0.4, head, size=18, font=HEAD)
        text(s, x + 0.25, TOP + 1.62, sw - 0.5, 0.85, body, size=14, color=MUTED)
    bx, by = L, TOP + 2.75
    box(s, bx, by, R - L, 1.91, fill="EEF0F3", line=None, radius=0.1)
    text(s, bx + 0.3, by + 0.22, 7.9, 0.35,
         [[("Manual version", {"font": HEAD, "color": INK, "size": 16}),
           ("   mininet-wifi> xterm h1 sta1 sta2", {"font": MONO, "size": 14})]],
         color=MUTED)
    text(s, bx + 0.3, by + 0.72, 7.9, 1.0,
         ["h1:   python3 collector.py --log results/demo/collector.csv",
          "sta1: python3 sensor.py --id 1 --qos 0 --count 400",
          "sta2: python3 sensor.py --id 2 --qos 1 --count 400"],
         size=14, font=MONO, color=INK, space_after=4)
    text(s, bx + 8.55, by + 0.22, R - L - 8.85, 1.5,
         [[("Backup", {"font": HEAD, "color": INK, "size": 16})],
          "Screen recording of the same run (Ubuntu: press PrtSc, then the record button)."],
         size=14, color=MUTED, space_after=4)
    notes(s, "เดโมใช้คำสั่งเดียว topology.py --demo --plot เครือข่ายจะขึ้น หน้าต่าง plot แสดงสถานีเคลื่อนที่ "
             "sta1 ส่งแบบ QoS 0 และ sta2 ส่งแบบ QoS 1 พร้อมกัน collector พิมพ์จำนวนข้อความทุก 2 วินาที "
             "ช่วง handover จะเห็นข้อความ sensor ... was silent ... ตอนจบมีตาราง PDR ของแต่ละเซนเซอร์ "
             "ถ้าเครื่องมีปัญหาให้เปิดวิดีโอสำรอง")


def slide_conclusion(prs, res):
    s = new_slide(prs)
    title(s, "Conclusion and trade-offs")
    w = (R - L - 0.35) / 2
    for i, (name, color, body, fits) in enumerate([
            ("QoS 0", BLUE, "Smallest overhead and lowest delay. Data sent during a "
             "handover is gone.", "frequent readings where the next one replaces the last."),
            ("QoS 1", ORANGE, "Recovers lost messages, paid for with extra bytes, delay "
             "and transmissions.", "alerts and events that must arrive.")]):
        x = L + i * (w + 0.35)
        card(s, x, TOP, w, 1.95, name, body, heading_dot=color)
        text(s, x + 0.25, TOP + 1.42, w - 0.5, 0.4,
             [[("Fits: ", {"bold": True, "color": INK}), fits]], size=14, color=INK)
    box(s, L, TOP + 2.2, R - L, 1.32, fill=NAVY, line=None)
    text(s, L + 0.3, TOP + 2.38, 6, 0.3, "KEY FINDING", size=13, bold=True, color=EYEBROW)
    m0, m1 = case(res, "mobile_qos0"), case(res, "mobile_qos1")
    if res and m0 and m1:
        finding = ("While moving, QoS 1 raised delivery from %.1f %% to %.1f %% but sent "
                   "%.2f× instead of %.2f× bytes per payload byte."
                   % (m0["pdr_pct"], m1["pdr_pct"], m1["overhead_x"], m0["overhead_x"]))
    else:
        finding = "[Filled in by build_slides.py: measured PDR and overhead of both QoS levels.]"
    text(s, L + 0.3, TOP + 2.72, R - L - 0.6, 0.75, finding, size=19, color="FFFFFF")
    text(s, L, TOP + 3.8, w, 0.8,
         [[("Limitation: ", {"bold": True, "color": INK}),
           "emulated network; energy is estimated from the transmission count."]],
         size=14, color=MUTED)
    text(s, L + w + 0.35, TOP + 3.8, w, 0.8,
         [[("Next step: ", {"bold": True, "color": INK}),
           "adaptive timeout and a buffer that resends after reconnecting."]],
         size=14, color=MUTED)
    notes(s, "สรุป QoS 0 ประหยัดที่สุดและดีเลย์ต่ำ แต่ข้อมูลช่วง handover หายไปเลย "
             "เหมาะกับค่าที่อ่านบ่อยและค่าใหม่แทนค่าเก่าได้ QoS 1 กู้ข้อความคืนได้ แลกกับไบต์ ดีเลย์ "
             "และจำนวนการส่งที่มากขึ้น เหมาะกับ alert ที่ต้องถึงแน่นอน ข้อจำกัดคือเป็นเครือข่ายจำลอง "
             "และประมาณพลังงานจากจำนวนครั้งที่ส่ง งานต่อไปคือ timeout แบบปรับตัว "
             "และบัฟเฟอร์ที่ส่งซ้ำหลังเชื่อมต่อใหม่")


def build(results_dir, out_path):
    res, timeline = None, None
    if results_dir:
        path = os.path.join(results_dir, "summary.json")
        if not os.path.exists(path):
            sys.exit("%s not found. Run analyze.py first." % path)
        with open(path) as fh:
            res = json.load(fh)
        tl_path = os.path.join(results_dir, "timeline.json")
        if os.path.exists(tl_path):
            with open(tl_path) as fh:
                timeline = json.load(fh)

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(SLIDE_W), Inches(SLIDE_H)
    apply_theme(prs)
    prs.core_properties.title = "IoT Telemetry over Mobile WiFi"
    prs.core_properties.author = "Patcharakorn Zoonboon"

    cover(prs, "TERM PROJECT · IOT & SMART WIRELESS NETWORKS",
          "IoT Telemetry over Mobile WiFi",
          "Comparing QoS 0 and QoS 1 delivery for moving sensors in Mininet-WiFi",
          "สวัสดีครับ ผมจะนำเสนอโปรเจกต์ IoT Telemetry over Mobile WiFi "
          "เป็นการเปรียบเทียบการส่งข้อมูลเซนเซอร์แบบ QoS 0 กับ QoS 1 "
          "เมื่อเซนเซอร์เคลื่อนที่และต้อง handover ระหว่าง AP ใน Mininet-WiFi")
    slide_problem(prs)
    slide_architecture(prs)
    slide_protocol(prs)
    slide_topology(prs)
    slide_method(prs)
    slide_pdr(prs, res)
    slide_gap(prs, res, timeline)
    slide_overhead(prs, res)
    slide_demo(prs)
    slide_conclusion(prs, res)
    cover(prs, "Q&A AND DISCUSSION", "Thank you", "Questions are welcome.",
          "ขอบคุณครับ ยินดีตอบคำถาม")

    for i, slide in enumerate(prs.slides, start=1):
        if 1 < i < len(prs.slides):
            footer(slide, i)
    prs.save(out_path)
    print("Saved %s (%s)" % (out_path, "with results from " + results_dir if res
                             else "result slides are placeholders"))


def main():
    p = argparse.ArgumentParser(description="Build the project slides (.pptx)")
    p.add_argument("--results", default=None,
                   help="results folder with summary.json (from analyze.py)")
    p.add_argument("--out", default="IoT_Telemetry_over_Mobile_WiFi.pptx")
    args = p.parse_args()
    build(args.results, args.out)


if __name__ == "__main__":
    main()
