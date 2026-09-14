"""A very small Excel writer, standard library only.

An .xlsx file is a zip of XML parts, which is little enough work to do by hand and saves
adding a dependency to a project that deliberately has none. This writes what a workbook
of records actually needs and nothing more: several sheets, a frozen and filtered header
row, sensible column widths, real numbers that Excel will total, and text that survives
Arabic, quotes and stray control characters.

    book = Workbook()
    book.sheet("Events", ["Event ID", "Name", "Budget"],
               [["IC-2026-001", "Ice Cream Day", 781.0]])
    data = book.save()          # bytes, ready to send or write to disk
"""

import datetime
import io
import re
import zipfile

# Characters Excel refuses to load. Tabs, newlines and carriage returns are fine.
_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

MAX_ROWS = 1_048_576          # the format's own limit
MAX_CELL = 32767              # characters Excel keeps in one cell


def col_letter(index):
    """1 -> A, 26 -> Z, 27 -> AA."""
    name = ""
    while index > 0:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def _esc(value):
    return (str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


class Workbook:
    def __init__(self):
        self._sheets = []

    def sheet(self, title, headers, rows, widths=None, money_columns=()):
        """One sheet. `money_columns` are 1-based indexes shown with two decimals."""
        self._sheets.append({
            "title": self._clean_title(title),
            "headers": [str(h) for h in headers],
            "rows": rows,
            "widths": widths or self._guess_widths(headers, rows),
            "money": set(money_columns),
        })

    @staticmethod
    def _clean_title(title):
        # Excel forbids these in a sheet name, and caps it at 31 characters.
        for ch in "[]:*?/\\":
            title = title.replace(ch, " ")
        return (title.strip() or "Sheet")[:31]

    @staticmethod
    def _guess_widths(headers, rows, cap=52):
        widths = [len(str(h)) + 4 for h in headers]
        for row in rows[:400]:                      # a sample is enough to look right
            for i, cell in enumerate(row[:len(widths)]):
                widths[i] = max(widths[i], min(len(str(cell if cell is not None else "")) + 2, cap))
        return widths

    # ------------------------------------------------------------------ parts
    def _sheet_xml(self, sheet):
        cols = "".join('<col min="%d" max="%d" width="%.1f" customWidth="1"/>'
                       % (i + 1, i + 1, w) for i, w in enumerate(sheet["widths"]))
        last_col = col_letter(max(1, len(sheet["headers"])))

        out = [
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
            '<sheetViews><sheetView workbookViewId="0">',
            '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>',
            '</sheetView></sheetViews>',
            '<sheetFormatPr defaultRowHeight="15"/>',
            '<cols>%s</cols>' % cols if cols else "",
            '<sheetData>',
        ]

        # the header row
        cells = ['<c r="%s1" s="1" t="inlineStr"><is><t>%s</t></is></c>'
                 % (col_letter(i + 1), _esc(h)) for i, h in enumerate(sheet["headers"])]
        out.append('<row r="1" ht="22" customHeight="1">%s</row>' % "".join(cells))

        for r, row in enumerate(sheet["rows"][:MAX_ROWS - 1], start=2):
            cells = []
            for i, value in enumerate(row):
                ref = "%s%d" % (col_letter(i + 1), r)
                style = 2 if (i + 1) in sheet["money"] else 0
                if value is None or value == "":
                    continue                                  # an empty cell needs no XML
                if isinstance(value, bool):
                    value = "Yes" if value else "No"
                if isinstance(value, (int, float)):
                    cells.append('<c r="%s" s="%d"><v>%s</v></c>' % (ref, style, repr(value)))
                elif isinstance(value, datetime.datetime):
                    cells.append('<c r="%s" t="inlineStr"><is><t>%s</t></is></c>'
                                 % (ref, value.isoformat(sep=" ")[:19]))
                elif isinstance(value, datetime.date):
                    cells.append('<c r="%s" t="inlineStr"><is><t>%s</t></is></c>'
                                 % (ref, value.isoformat()))
                else:
                    text = _ILLEGAL.sub("", str(value))[:MAX_CELL]
                    cells.append('<c r="%s" s="%d" t="inlineStr"><is><t xml:space="preserve">%s</t>'
                                 '</is></c>' % (ref, style, _esc(text)))
            out.append('<row r="%d">%s</row>' % (r, "".join(cells)))

        out.append('</sheetData>')
        if sheet["headers"]:
            out.append('<autoFilter ref="A1:%s%d"/>' % (last_col, max(1, len(sheet["rows"]) + 1)))
        out.append('</worksheet>')
        return "".join(out)

    def _workbook_xml(self):
        tabs = "".join('<sheet name="%s" sheetId="%d" r:id="rId%d"/>'
                       % (_esc(s["title"]), i + 1, i + 1) for i, s in enumerate(self._sheets))
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<sheets>%s</sheets></workbook>' % tabs)

    def _rels_xml(self):
        items = "".join(
            '<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/'
            '2006/relationships/worksheet" Target="worksheets/sheet%d.xml"/>' % (i + 1, i + 1)
            for i in range(len(self._sheets)))
        items += ('<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/'
                  'officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                  % (len(self._sheets) + 1))
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                'relationships">%s</Relationships>' % items)

    def _content_types_xml(self):
        sheets = "".join('<Override PartName="/xl/worksheets/sheet%d.xml" ContentType='
                         '"application/vnd.openxmlformats-officedocument.spreadsheetml.'
                         'worksheet+xml"/>' % (i + 1) for i in range(len(self._sheets)))
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-'
                'package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.'
                'openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                '<Override PartName="/xl/styles.xml" ContentType="application/vnd.'
                'openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                '%s</Types>' % sheets)

    @staticmethod
    def _styles_xml():
        # 0 plain, 1 header (white on KABi mid blue), 2 money with two decimals
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                '<numFmts count="1"><numFmt numFmtId="164" formatCode="#,##0.00"/></numFmts>'
                '<fonts count="2">'
                '<font><sz val="11"/><name val="Calibri"/></font>'
                '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>'
                '</fonts>'
                '<fills count="3">'
                '<fill><patternFill patternType="none"/></fill>'
                '<fill><patternFill patternType="gray125"/></fill>'
                '<fill><patternFill patternType="solid"><fgColor rgb="FF216AB1"/>'
                '<bgColor indexed="64"/></patternFill></fill>'
                '</fills>'
                '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border>'
                '</borders>'
                '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>'
                '</cellStyleXfs>'
                '<cellXfs count="3">'
                '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
                '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" '
                'applyFill="1" applyAlignment="1"><alignment vertical="center"/></xf>'
                '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" '
                'applyNumberFormat="1"/>'
                '</cellXfs>'
                '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/>'
                '</cellStyles></styleSheet>')

    def save(self):
        if not self._sheets:
            self.sheet("Empty", ["Nothing to export"], [])
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", self._content_types_xml())
            z.writestr("_rels/.rels",
                       '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                       '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                       'relationships"><Relationship Id="rId1" Type="http://schemas.'
                       'openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                       'Target="xl/workbook.xml"/></Relationships>')
            z.writestr("xl/workbook.xml", self._workbook_xml())
            z.writestr("xl/_rels/workbook.xml.rels", self._rels_xml())
            z.writestr("xl/styles.xml", self._styles_xml())
            for i, sheet in enumerate(self._sheets):
                z.writestr("xl/worksheets/sheet%d.xml" % (i + 1), self._sheet_xml(sheet))
        return buf.getvalue()
