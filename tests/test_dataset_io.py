import io
from openpyxl import Workbook
from core.dataset_io import parse_csv_bytes, parse_xlsx_bytes, dataset_to_xlsx_bytes


def test_parse_csv_bytes_returns_list_of_dicts():
    csv = b"email,password\na@b.co,hunter2\nc@d.co,letmein\n"
    rows = parse_csv_bytes(csv)
    assert rows == [
        {"email": "a@b.co", "password": "hunter2"},
        {"email": "c@d.co", "password": "letmein"},
    ]


def test_parse_xlsx_bytes_reads_first_sheet():
    wb = Workbook()
    ws = wb.active
    ws.append(["email", "password"])
    ws.append(["a@b.co", "hunter2"])
    buf = io.BytesIO()
    wb.save(buf)
    rows = parse_xlsx_bytes(buf.getvalue())
    assert rows == [{"email": "a@b.co", "password": "hunter2"}]


def test_dataset_to_xlsx_bytes_round_trip():
    rows = [{"email": "a@b.co", "password": "hunter2"}]
    blob = dataset_to_xlsx_bytes(rows)
    assert parse_xlsx_bytes(blob) == rows


def test_parse_skips_fully_blank_rows():
    csv = b"a,b\n1,2\n,,\n3,4\n"
    rows = parse_csv_bytes(csv)
    assert rows == [{"a": "1", "b": "2"}, {"a": "3", "b": "4"}]
