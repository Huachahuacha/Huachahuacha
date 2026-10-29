"""Import a CSRC securities-company directory while preserving verified source mappings.

The main scanner reads the generated JSON and does not need an Excel dependency.
For official .xls imports, install xlrd in the environment running this script.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "bid-alerts" / "brokers.json"
LEGAL_SUFFIXES = ("股份有限公司", "有限责任公司", "有限公司")


class RegistryImportError(ValueError):
    pass


def load_rows(path: Path) -> tuple[str, list[list[Any]]]:
    if path.suffix.lower() == ".xls":
        try:
            import xlrd
        except ImportError as exc:
            raise RegistryImportError(
                "Reading .xls requires xlrd; install it with: python -m pip install 'xlrd>=2.0.2,<3'"
            ) from exc
        try:
            book = xlrd.open_workbook(path)
        except xlrd.XLRDError as exc:
            raise RegistryImportError(f"Cannot read official .xls source: {exc}") from exc
        for sheet in book.sheets():
            rows = [sheet.row_values(index) for index in range(sheet.nrows)]
            if any("公司名称" in row for row in rows[:10]):
                return sheet.name, rows
    elif path.suffix.lower() == ".xlsx":
        try:
            import openpyxl
        except ImportError as exc:
            raise RegistryImportError("Reading .xlsx requires openpyxl") from exc
        book = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            for sheet in book.worksheets:
                rows = [list(row) for row in sheet.iter_rows(values_only=True)]
                if any("公司名称" in row for row in rows[:10]):
                    return sheet.title, rows
        finally:
            book.close()
    else:
        raise RegistryImportError("Expected an official .xls or .xlsx directory")
    raise RegistryImportError("No worksheet with the 公司名称 header was found")


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def extract_companies(rows: list[list[Any]], *, min_count: int = 100) -> list[dict[str, Any]]:
    header_index = next(
        (index for index, row in enumerate(rows[:10]) if "公司名称" in row and "序号" in row),
        None,
    )
    if header_index is None:
        raise RegistryImportError("Required 序号 / 公司名称 headers are missing")
    header = rows[header_index]
    name_column = header.index("公司名称")
    sequence_column = header.index("序号")
    jurisdiction_column = next(
        (index for index, value in enumerate(header) if "辖区" in normalize_text(value)),
        None,
    )
    companies: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for row in rows[header_index + 1 :]:
        if len(row) <= max(name_column, sequence_column):
            continue
        raw_sequence = normalize_text(row[sequence_column])
        if not re.fullmatch(r"\d+(?:\.0)?", raw_sequence):
            continue
        sequence = int(float(raw_sequence))
        name = normalize_text(row[name_column])
        if not name or not name.endswith(LEGAL_SUFFIXES):
            raise RegistryImportError(f"Invalid company name at official row {sequence}")
        if name in seen_names:
            raise RegistryImportError(f"Duplicate company name: {name}")
        seen_names.add(name)
        companies.append(
            {
                "name": name,
                "official_sequence": sequence,
                "jurisdiction": normalize_text(row[jurisdiction_column])
                if jurisdiction_column is not None and jurisdiction_column < len(row)
                else "",
            }
        )
    if len(companies) < min_count:
        raise RegistryImportError(f"Only {len(companies)} companies found; expected at least {min_count}")
    sequences = [company["official_sequence"] for company in companies]
    if sequences != list(range(1, len(companies) + 1)):
        raise RegistryImportError("Official sequence numbers are duplicated, missing or out of order")
    return companies


def short_name(name: str) -> str:
    for suffix in LEGAL_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def merge_companies(
    companies: list[dict[str, Any]], previous: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    previous_brokers = previous.get("brokers", [])
    if not isinstance(previous_brokers, list):
        raise RegistryImportError("Existing registry brokers must be an array")
    for entry in previous_brokers:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise RegistryImportError("Existing registry entries must contain a name")
        if not isinstance(entry.get("aliases", []), list) or not all(
            isinstance(alias, str) for alias in entry.get("aliases", [])
        ):
            raise RegistryImportError("Existing broker aliases must be an array of strings")
    by_name = {entry["name"]: entry for entry in previous_brokers}
    brokers: list[dict[str, Any]] = []
    for company in companies:
        name = company["name"]
        existing = by_name.get(name, {})
        aliases = list(existing.get("aliases", []))
        shortened = short_name(name)
        if shortened != name and shortened not in aliases:
            aliases.append(shortened)
        brokers.append(
            {
                **existing,
                "id": existing.get("id") or "csrc-" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:12],
                **company,
                "aliases": aliases,
                "procurement_urls": existing.get("procurement_urls", []),
                "coverage_status": existing.get("coverage_status", "pending_verification"),
            }
        )
    current_names = {entry["name"] for entry in brokers}
    retired = [entry for entry in previous_brokers if entry["name"] not in current_names]
    return brokers, retired


def build_registry(
    source_file: Path,
    *,
    source_url: str,
    page_url: str,
    as_of: str,
    previous: dict[str, Any],
    min_count: int = 100,
) -> dict[str, Any]:
    if not re.fullmatch(r"20\d{2}-(?:0[1-9]|1[0-2])", as_of):
        raise RegistryImportError("--as-of must have YYYY-MM format")
    for label, url in (("source", source_url), ("page", page_url)):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or not (
            parsed.hostname == "csrc.gov.cn" or parsed.hostname.endswith(".csrc.gov.cn")
        ):
            raise RegistryImportError(f"The {label} URL must be an HTTPS csrc.gov.cn URL")
    sheet_name, rows = load_rows(source_file)
    companies = extract_companies(rows, min_count=min_count)
    brokers, retired = merge_companies(companies, previous)
    raw_bytes = source_file.read_bytes()
    registry = {
        "schema_version": 1,
        "as_of": as_of,
        "retrieved_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        "source_url": source_url,
        "provenance": {
            "publisher": "中国证券监督管理委员会",
            "title": f"证券公司名录（{as_of[:4]}年{int(as_of[5:])}月）",
            "page_url": page_url,
            "source_sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "source_bytes": len(raw_bytes),
            "source_sheet": sheet_name,
            "entry_count": len(brokers),
            "scope_note": "完整保留官方名录中证券公司及单列资产管理机构，不将记录数量视为独立券商集团数量。",
            "aliases_policy": "默认检索别名仅机械移除公司法律形式尾缀；历史更名和品牌别名须另行核实添加。",
            "coverage_note": "名录用于逐家检索计划，不代表对应券商已成功检索或官方采购入口已接入；采购入口为空的记录仍待核验。",
        },
        "brokers": brokers,
    }
    archived = list(previous.get("retired_brokers", []))
    archived_names = {entry["name"] for entry in archived}
    archived.extend(entry for entry in retired if entry["name"] not in archived_names)
    if archived:
        registry["retired_brokers"] = archived
    return registry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-file", type=Path, help="Already-downloaded official .xls / .xlsx file")
    parser.add_argument("--source-url", required=True, help="Official CSRC spreadsheet URL")
    parser.add_argument("--page-url", required=True, help="Official CSRC page linking the spreadsheet")
    parser.add_argument("--as-of", required=True, help="Directory month, YYYY-MM")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--download-to", type=Path, help="Download the source to this path when --source-file is absent")
    parser.add_argument("--min-count", type=int, default=100)
    args = parser.parse_args(argv)
    try:
        source_file = args.source_file
        if source_file is None:
            if args.download_to is None:
                raise RegistryImportError("Provide --source-file or --download-to")
            parsed = urlsplit(args.source_url)
            if parsed.scheme != "https" or not parsed.hostname or not (
                parsed.hostname == "csrc.gov.cn" or parsed.hostname.endswith(".csrc.gov.cn")
            ):
                raise RegistryImportError("Download is restricted to HTTPS csrc.gov.cn URLs")
            request = Request(args.source_url, headers={"User-Agent": "Mozilla/5.0 (broker-directory-import)"})
            with urlopen(request, timeout=60) as response:
                payload = response.read(5_000_001)
            if len(payload) > 5_000_000:
                raise RegistryImportError("Official source exceeds the 5 MB safety limit")
            source_file = args.download_to
            source_file.parent.mkdir(parents=True, exist_ok=True)
            source_file.write_bytes(payload)
        previous = json.loads(args.output.read_text(encoding="utf-8")) if args.output.exists() else {}
        registry = build_registry(
            source_file,
            source_url=args.source_url,
            page_url=args.page_url,
            as_of=args.as_of,
            previous=previous,
            min_count=args.min_count,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary_output = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary_output.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary_output.replace(args.output)
        print(f"Imported {len(registry['brokers'])} official directory entries into {args.output}")
        print(f"Source SHA-256: {registry['provenance']['source_sha256']}")
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print(f"Broker registry import failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
