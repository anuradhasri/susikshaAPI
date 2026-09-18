from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
from xml.sax.saxutils import escape


OUTPUT = Path(__file__).resolve().parents[2] / "session_migration_template_demo.xlsx"


HEADERS = [
    "Date",
    "Region",
    "Child First Name",
    "Child Last Name",
    "Phone Number",
    "Program",
    "Package Name",
    "Package Total Sessions",
    "Package Total Amount",
    "Package Sessions Used",
    "Package Sessions Remaining",
    "Therapist",
    "Therapy",
    "Start Time",
    "End Time",
    "Billing Category",
    "Session Status",
    "Session Count",
    "Amount Per Session",
    "Session Amount",
    "Assessment Name",
    "Assessment Amount",
    "Payment Amount",
    "Payment Mode",
    "Transaction ID",
    "Due Amount",
    "Notes",
]


DEMO_ROWS = [
    [
        "2026-04-20",
        "Mulund",
        "Aahan",
        "Singh",
        "9876543210",
        "General",
        "12 Session Package",
        "12",
        "13200",
        "1",
        "11",
        "Aishwarya",
        "OT",
        "09:00",
        "09:45",
        "Fixed Package Session",
        "Completed",
        "1",
        "1100",
        "1100",
        "",
        "0",
        "0",
        "",
        "",
        "0",
        "Package session completed",
    ],
    [
        "2026-04-20",
        "Mulund",
        "Rashvi",
        "",
        "9876543211",
        "General",
        "Flexi",
        "",
        "",
        "",
        "",
        "Jankhana",
        "Language",
        "10:00",
        "10:45",
        "Flexi Session",
        "Completed",
        "1",
        "1200",
        "1200",
        "",
        "0",
        "1200",
        "Cash",
        "",
        "0",
        "Flexi session paid same day",
    ],
    [
        "2026-04-20",
        "Mulund",
        "Rashvi",
        "",
        "9876543211",
        "Assessment",
        "",
        "",
        "",
        "",
        "",
        "Jyothi",
        "Assessment",
        "11:00",
        "11:45",
        "Assessment",
        "Completed",
        "1",
        "0",
        "0",
        "Language Assessment",
        "2500",
        "0",
        "",
        "",
        "2500",
        "Assessment completed, payment pending",
    ],
    [
        "2026-04-21",
        "Mulund",
        "Mayank",
        "Patel",
        "9876543212",
        "Vocational Program",
        "Vocational 8 Session - Package",
        "8",
        "10000",
        "2",
        "6",
        "Amisha",
        "Language and Communication",
        "09:45",
        "10:30",
        "Group Program Session",
        "Completed",
        "1",
        "1250",
        "1250",
        "",
        "0",
        "1000",
        "UPI",
        "UPI123456",
        "250",
        "Partial payment for vocational group session",
    ],
    [
        "2026-04-21",
        "Mulund",
        "Aadya",
        "Chaudhary",
        "9876543213",
        "Vocational Program",
        "Vocational 8 Session - Package",
        "8",
        "10000",
        "2",
        "6",
        "Aishwarya",
        "Language and Communication",
        "09:45",
        "10:30",
        "Group Program Session",
        "Completed",
        "1",
        "1250",
        "1250",
        "",
        "0",
        "1250",
        "Online",
        "TXN998877",
        "0",
        "Full payment for vocational group session",
    ],
    [
        "2026-04-22",
        "Mulund",
        "Krishav",
        "",
        "9876543214",
        "General",
        "16 Session Package",
        "16",
        "17600",
        "0",
        "16",
        "",
        "",
        "",
        "",
        "Package Purchase",
        "Completed",
        "0",
        "0",
        "0",
        "",
        "0",
        "17600",
        "Bank Transfer",
        "BANK5566",
        "0",
        "Package advance payment only, no session row",
    ],
]


INSTRUCTIONS = [
    ["Rule", "Details"],
    [
        "Review introduction",
        "We are preparing historical monthly session and payment data for migration into the Sushiksha system. The Excel must be row-based so each row maps safely to children, programs, therapies, therapist slots, slot bookings, packages, and payments.",
    ],
    ["One row rule", "Use one row per child per session/payment. If two sessions happen in a day, use two rows."],
    ["No merged cells", "Do not merge cells and do not use therapist names as columns."],
    ["Date format", "Use YYYY-MM-DD, for example 2026-04-20."],
    ["Amounts", "Use numbers only. Write 1200, not Rs 1,200 or INR 1200."],
    ["Program values", "General, Structured Program, Vocational Program, Social Skills Program, Parent Training Program, Schooling Program, Assessment."],
    ["Billing Category values", "Flexi Session, Fixed Package Session, Group Program Session, Assessment, Package Purchase, Payment Only."],
    ["Session Status values", "Completed, Cancelled, No Show."],
    ["Payment Mode values", "Cash, UPI, Online, Card, Bank Transfer, Package. Leave blank if no payment."],
    ["Payment Only rows", "Use Billing Category = Payment Only when money was collected without a session on that row."],
    ["Package fields", "Fill Package Name, Package Total Sessions, Package Total Amount when the row belongs to a package."],
    ["Optional fields", "Phone Number, Start Time, End Time, Transaction ID, Notes help matching and auditing."],
]


def col_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def sheet_xml(rows: list[list[str]]) -> str:
    xml_rows = []
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for col_index, value in enumerate(row, start=1):
            ref = f"{col_name(col_index)}{row_index}"
            text = escape(str(value))
            cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>')
        xml_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheetData>'
        + "".join(xml_rows)
        + "</sheetData></worksheet>"
    )


def write_xlsx() -> None:
    template_rows = [HEADERS, *DEMO_ROWS]
    with ZipFile(OUTPUT, "w", ZIP_DEFLATED) as xlsx:
        xlsx.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>",
        )
        xlsx.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        xlsx.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            "<sheets>"
            '<sheet name="Import Template" sheetId="1" r:id="rId1"/>'
            '<sheet name="Instructions" sheetId="2" r:id="rId2"/>'
            "</sheets></workbook>",
        )
        xlsx.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>'
            "</Relationships>",
        )
        xlsx.writestr("xl/worksheets/sheet1.xml", sheet_xml(template_rows))
        xlsx.writestr("xl/worksheets/sheet2.xml", sheet_xml(INSTRUCTIONS))


if __name__ == "__main__":
    write_xlsx()
    print(OUTPUT)
