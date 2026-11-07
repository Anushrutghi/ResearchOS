"""Small deterministic exporters with no external runtime dependencies."""

from html import escape
import re
import textwrap
import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO


def export_content(content: str, output_format: str, figures: list[dict] | None = None) -> tuple[str, bytes, str]:
    figures = figures or []
    if output_format == "markdown":
        rendered = content
        for figure in figures:
            marker = f"[[FIGURE:{figure['figure_id']}]]"
            rendered = rendered.replace(marker, f"<figure>{figure['svg']}<figcaption>{escape(figure['title'])}</figcaption></figure>")
        return "md", rendered.encode("utf-8"), "text/markdown; charset=utf-8"
    if output_format == "latex":
        return "tex", _latex(content, figures).encode("utf-8"), "application/x-tex; charset=utf-8"
    if output_format == "docx":
        return "docx", _docx(content, figures), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if output_format == "pdf":
        return "pdf", _pdf(content, figures), "application/pdf"
    raise ValueError("Export format must be markdown, latex, docx, or pdf.")


def _latex(content: str, figures: list[dict] | None = None) -> str:
    def tex(value):
        replacements = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$",
            "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
        return "".join(replacements.get(char, char) for char in value)
    figures_by_id = {item["figure_id"]: item for item in (figures or [])}
    result = [r"\documentclass{article}", r"\usepackage[utf8]{inputenc}", r"\usepackage[T1]{fontenc}",
              r"\usepackage{hyperref}", r"\usepackage{tikz}", r"\usepackage{pgfplots}",
              r"\pgfplotsset{compat=1.18}", r"\begin{document}"]
    lines = content.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        marker = re.fullmatch(r"\[\[FIGURE:(FIG\d{3})\]\]", line.strip())
        if marker and marker.group(1) in figures_by_id:
            result.extend(_tikz_figure(figures_by_id[marker.group(1)]))
            index += 1
            continue
        if line.strip().startswith("|"):
            table = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                cells = _table_cells(lines[index])
                if cells and not all(re.fullmatch(r":?-{2,}:?", cell.replace(" ", "")) for cell in cells):
                    table.append(cells)
                index += 1
            if table:
                count = max(len(row) for row in table)
                result.append(r"\begin{tabular}{" + "|".join(["l"] * count) + "|}")
                result.append(r"\hline")
                for row in table:
                    padded = row + [""] * (count - len(row))
                    result.append(" & ".join(tex(cell) for cell in padded) + r" \\")
                    result.append(r"\hline")
                result.append(r"\end{tabular}")
            continue
        if line.startswith("# "):
            result.extend([r"\title{" + tex(line[2:]) + "}", r"\maketitle"])
        elif line.startswith("## "):
            result.append(r"\section{" + tex(line[3:]) + "}")
        elif line.startswith("### "):
            result.append(r"\subsection{" + tex(line[4:]) + "}")
        elif line.startswith("- "):
            result.append(r"\begin{itemize}\item " + tex(line[2:]) + r"\end{itemize}")
        elif line.startswith("> "):
            result.append(r"\begin{quote}" + tex(line[2:]) + r"\end{quote}")
        elif line.strip():
            result.append(tex(re.sub(r"[`*_]", "", line)))
        else:
            result.append("")
        index += 1
    result.append(r"\end{document}")
    return "\n".join(result) + "\n"


def _tikz_figure(figure: dict) -> list[str]:
    data = figure.get("data", {})
    result = [r"\begin{figure}[ht]", r"\centering", r"\begin{tikzpicture}"]
    if data.get("type") == "actual_predicted_scatter":
        points = data.get("points", [])
        if points:
            low = min(value for pair in points for value in pair)
            high = max(value for pair in points for value in pair)
            span = high - low or 1
            coords = " ".join(f"({(x-low)/span:.6f},{(y-low)/span:.6f})" for x, y in points[:200])
            result.extend([r"\begin{axis}[xlabel={Actual},ylabel={Predicted},xmin=0,xmax=1,ymin=0,ymax=1,grid=major]",
                           r"\addplot[only marks,blue] coordinates {" + coords + "};", r"\addplot[gray,dashed] coordinates {(0,0) (1,1)};", r"\end{axis}"])
    elif data.get("type") == "confusion_matrix":
        matrix = data.get("matrix", [])
        size = len(matrix)
        maximum = max((value for row in matrix for value in row), default=1) or 1
        for r, row in enumerate(matrix[:20]):
            for c, value in enumerate(row[:20]):
                shade = max(0, min(90, int(90 * value / maximum)))
                result.extend([f"\\fill[blue!{shade}] ({c},{-r}) rectangle ({c+1},{-r-1});",
                               f"\\node[font=\\scriptsize] at ({c+0.5},{-r-0.5}) {{{int(value)}}};"])
        result.append(r"\draw[step=1,gray] (0,0) grid (" + str(size) + "," + str(-size) + ");")
    result.extend([r"\end{tikzpicture}", r"\caption{" + tex_title(figure.get("title", "Recorded evaluation figure")) + "}", r"\end{figure}"])
    return result


def tex_title(value: str) -> str:
    return value.replace("&", r"\&").replace("%", r"\%").replace("_", r"\_")


def _table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _docx(content: str, figures: list[dict] | None = None) -> bytes:
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    ET.register_namespace("w", ns)
    wp = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
    a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    pic = "http://schemas.openxmlformats.org/drawingml/2006/picture"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    asvg = "http://schemas.microsoft.com/office/drawing/2016/SVG/main"
    for prefix, uri in (("wp", wp), ("a", a), ("pic", pic), ("r", rel), ("asvg", asvg)):
        ET.register_namespace(prefix, uri)
    figures_by_id = {item["figure_id"]: item for item in (figures or [])}
    figure_rels = []
    document = ET.Element(f"{{{ns}}}document")
    body = ET.SubElement(document, f"{{{ns}}}body")
    lines = content.splitlines() or [""]
    index = 0
    while index < len(lines):
        line = lines[index]
        marker = re.fullmatch(r"\[\[FIGURE:(FIG\d{3})\]\]", line.strip())
        if marker and marker.group(1) in figures_by_id:
            figure = figures_by_id[marker.group(1)]
            rel_id = f"rIdFigure{len(figure_rels)+2}"
            paragraph = ET.SubElement(body, f"{{{ns}}}p")
            run = ET.SubElement(paragraph, f"{{{ns}}}r")
            drawing = ET.SubElement(run, f"{{{ns}}}drawing")
            inline = ET.SubElement(drawing, f"{{{wp}}}inline", {"distT": "0", "distB": "0", "distL": "0", "distR": "0"})
            ET.SubElement(inline, f"{{{wp}}}extent", {"cx": "5486400", "cy": "3657600"})
            ET.SubElement(inline, f"{{{wp}}}docPr", {"id": str(len(figure_rels)+1), "name": figure["title"][:100], "descr": figure["alt_text"][:200]})
            frame = ET.SubElement(inline, f"{{{wp}}}cNvGraphicFramePr")
            ET.SubElement(frame, f"{{{a}}}graphicFrameLocks", {"noChangeAspect": "1"})
            graphic = ET.SubElement(inline, f"{{{a}}}graphic")
            graphic_data = ET.SubElement(graphic, f"{{{a}}}graphicData", {"uri": "http://schemas.openxmlformats.org/drawingml/2006/picture"})
            picture = ET.SubElement(graphic_data, f"{{{pic}}}pic")
            nonvisual = ET.SubElement(picture, f"{{{pic}}}nvPicPr")
            ET.SubElement(nonvisual, f"{{{pic}}}cNvPr", {"id": str(len(figure_rels)+1), "name": figure["figure_id"] + ".svg"})
            ET.SubElement(nonvisual, f"{{{pic}}}cNvPicPr")
            fill = ET.SubElement(picture, f"{{{pic}}}blipFill")
            blip = ET.SubElement(fill, f"{{{a}}}blip", {f"{{{rel}}}embed": rel_id})
            extensions = ET.SubElement(blip, f"{{{a}}}extLst")
            extension = ET.SubElement(extensions, f"{{{a}}}ext", {"uri": "{96DAC541-7B7A-43D3-8B79-37D633B846F1}"})
            ET.SubElement(extension, f"{{{asvg}}}svgBlip", {f"{{{rel}}}embed": rel_id})
            stretch = ET.SubElement(fill, f"{{{a}}}stretch")
            ET.SubElement(stretch, f"{{{a}}}fillRect")
            shape = ET.SubElement(picture, f"{{{pic}}}spPr")
            transform = ET.SubElement(shape, f"{{{a}}}xfrm")
            ET.SubElement(transform, f"{{{a}}}off", {"x": "0", "y": "0"})
            ET.SubElement(transform, f"{{{a}}}ext", {"cx": "5486400", "cy": "3657600"})
            geometry = ET.SubElement(shape, f"{{{a}}}prstGeom", {"prst": "rect"})
            ET.SubElement(geometry, f"{{{a}}}avLst")
            figure_rels.append((rel_id, figure["svg"].encode("utf-8")))
            index += 1
            continue
        if line.strip().startswith("|"):
            table_rows = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                cells = _table_cells(lines[index])
                if cells and not all(re.fullmatch(r":?-{2,}:?", cell.replace(" ", "")) for cell in cells):
                    table_rows.append(cells)
                index += 1
            if table_rows:
                table = ET.SubElement(body, f"{{{ns}}}tbl")
                for cells in table_rows:
                    row = ET.SubElement(table, f"{{{ns}}}tr")
                    for value in cells:
                        cell = ET.SubElement(row, f"{{{ns}}}tc")
                        paragraph = ET.SubElement(cell, f"{{{ns}}}p")
                        run = ET.SubElement(paragraph, f"{{{ns}}}r")
                        text_node = ET.SubElement(run, f"{{{ns}}}t")
                        text_node.text = value
            continue
        paragraph = ET.SubElement(body, f"{{{ns}}}p")
        style = "Title" if line.startswith("# ") else "Heading1" if line.startswith("## ") else "Heading2" if line.startswith("### ") else None
        if style:
            properties = ET.SubElement(paragraph, f"{{{ns}}}pPr")
            ET.SubElement(properties, f"{{{ns}}}pStyle", {f"{{{ns}}}val": style})
        text = line.lstrip("#> ") if line.startswith(("#", "> ")) else line[2:] if line.startswith("- ") else line
        run = ET.SubElement(paragraph, f"{{{ns}}}r")
        node = ET.SubElement(run, f"{{{ns}}}t")
        node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        node.text = re.sub(r"[`*_]", "", text)
        index += 1
    ET.SubElement(body, f"{{{ns}}}sectPr")
    doc_xml = ET.tostring(document, encoding="utf-8", xml_declaration=True)
    types = b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Default Extension="svg" ContentType="image/svg+xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>'''
    rels = b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>'''
    document_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>'''.replace("</Relationships>", "".join(
        f'<Relationship Id="{rel_id}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/{rel_id}.svg"/>'
        for rel_id, _ in figure_rels) + "</Relationships>").encode()
    styles = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="{ns}"><w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/></w:style></w:styles>'''.encode()
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", doc_xml)
        archive.writestr("word/_rels/document.xml.rels", document_rels)
        archive.writestr("word/styles.xml", styles)
        for rel_id, svg in figure_rels:
            archive.writestr(f"word/media/{rel_id}.svg", svg)
    return output.getvalue()


def _pdf(content: str, figures: list[dict] | None = None) -> bytes:
    figures_by_id = {item["figure_id"]: item for item in (figures or [])}
    lines = []
    for line in content.splitlines():
        marker = re.fullmatch(r"\[\[FIGURE:(FIG\d{3})\]\]", line.strip())
        if marker and marker.group(1) in figures_by_id:
            lines.append({"figure": figures_by_id[marker.group(1)]})
            continue
        if line.startswith("# "):
            line = line[2:].upper()
        elif line.startswith("## "):
            line = line[3:]
        elif line.startswith("### "):
            line = line[4:]
        line = re.sub(r"[`*_]", "", line.lstrip("> "))
        if line.strip().startswith("|"):
            line = "   ".join(_table_cells(line))
        lines.extend(textwrap.wrap(line, width=92, replace_whitespace=True, drop_whitespace=True) or [""])
    page_lines = []
    current = []
    for line in lines:
        if isinstance(line, dict):
            if current: page_lines.append(current); current = []
            page_lines.append([line])
        else:
            current.append(line)
            if len(current) >= 54: page_lines.append(current); current = []
    if current or not page_lines: page_lines.append(current)
    objects: dict[int, bytes] = {1: b"<< /Type /Catalog /Pages 2 0 R >>"}
    page_ids = []
    next_id = 4
    for page in page_lines:
        page_id, content_id = next_id, next_id + 1
        next_id += 2
        page_ids.append(page_id)
        if len(page) == 1 and isinstance(page[0], dict):
            commands = _pdf_figure_commands(page[0]["figure"])
        else:
            commands = ["BT", "/F1 9 Tf", "48 744 Td", "12 TL"]
            for line in page:
                safe = line.encode("latin-1", errors="replace").decode("latin-1")
                safe = safe.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
                commands.append(f"({safe}) Tj")
                commands.append("T*")
            commands.append("ET")
        stream = "\n".join(commands).encode("latin-1")
        objects[page_id] = f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>".encode()
        objects[content_id] = f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream"
    kids = " ".join(f"{item} 0 R" for item in page_ids)
    objects[2] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode()
    objects[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    output = bytearray(b"%PDF-1.4\n% ResearchOS\n")
    offsets = [0] * (max(objects) + 1)
    for object_id in range(1, max(objects) + 1):
        offsets[object_id] = len(output)
        output.extend(f"{object_id} 0 obj\n".encode() + objects[object_id] + b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(output)


def _pdf_figure_commands(figure: dict) -> list[str]:
    data = figure.get("data", {})
    title = figure.get("title", "Recorded evaluation figure").encode("latin-1", errors="replace").decode("latin-1")
    title = title.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    commands = ["BT /F1 12 Tf 48 750 Td (" + title + ") Tj ET"]
    if data.get("type") == "actual_predicted_scatter":
        points = data.get("points", [])
        if not points: return commands
        low = min(value for pair in points for value in pair)
        high = max(value for pair in points for value in pair)
        span = high-low or 1
        commands.append("0.15 0.45 0.72 rg 48 100 500 560 re S")
        commands.append("0.55 G 48 100 m 548 660 l S")
        for actual, predicted in points[:1000]:
            x = 48+(actual-low)/span*500
            y = 100+(predicted-low)/span*560
            commands.append(f"{x:.2f} {y:.2f} 3 3 re f")
    elif data.get("type") == "confusion_matrix":
        matrix = data.get("matrix", [])[:20]
        size = len(matrix)
        if not size: return commands
        maximum = max((cell for row in matrix for cell in row), default=1) or 1
        cell = min(24, 460/size)
        x0, y0 = 80, 670-size*cell
        for row_index, row in enumerate(matrix):
            for column, value in enumerate(row[:size]):
                gray = 0.94-0.65*value/maximum
                x, y = x0+column*cell, y0+(size-row_index-1)*cell
                commands.extend([f"{gray:.3f} g", f"{x:.2f} {y:.2f} {cell:.2f} {cell:.2f} re f",
                                 "0 G", f"{x:.2f} {y:.2f} {cell:.2f} {cell:.2f} re S"])
                value_text = str(value)
                commands.append(f"BT /F1 7 Tf {x+cell/3:.2f} {y+cell/2:.2f} Td ({value_text}) Tj ET")
    return commands
