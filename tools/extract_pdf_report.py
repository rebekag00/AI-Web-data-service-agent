from __future__ import annotations

import json
import io
import re
import sys
from pathlib import Path

from pypdf import PdfReader


CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
NUMBER = r"-?\d{1,3}(?:[ \u00a0]\d{3})+|-?\d+"
NUMBER_SUFFIX = re.compile(rf"((?:{NUMBER})(?:\s+(?:{NUMBER}))*)\s*$")
SKIP_TITLES = (
    "čez distribuce, a. s. /",
    "označení ",
    "běžné účetní",
    "minulé účetní",
    "skutečnost",
    "v účetním období",
    "(v tis. kč)",
)


def clean_text(value: str) -> str:
    value = CONTROL.sub("", value).replace("\u00a0", " ")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def page_title(text: str, page_number: int) -> str:
    known = (
        "Údaje o osobách odpovědných za výroční zprávu",
        "Úvodní slovo předsedy představenstva",
        "Přehled vybraných ukazatelů společnosti",
        "Hlavní události roku 2024",
        "Orgány společnosti",
        "Hospodaření společnosti",
        "Očekávaná hospodářská a finanční situace",
        "Investice",
        "Informace o hlavním předmětu činnosti společnosti",
        "Řízení bezpečnosti a jakosti",
        "Akcie a akcionáři",
        "Personalistika",
        "Výzkum a vývoj",
        "Vývoj legislativního rámce energetiky",
        "Zpráva o vztazích za rok 2024",
        "Zpráva nezávislého auditora",
        "Rozvaha",
        "Výkaz zisku a ztráty",
        "Přehled o peněžních tocích",
        "Přehled o změnách vlastního kapitálu",
        "Příloha účetní závěrky",
    )
    folded = text.casefold()
    for heading in known:
        if heading.casefold() in folded:
            return heading
    for line in text.splitlines():
        candidate = line.strip()
        lowered = candidate.casefold()
        if not candidate or candidate.isdigit() or lowered == "sníži":
            continue
        if any(lowered.startswith(prefix) for prefix in SKIP_TITLES):
            continue
        return candidate[:140]
    return f"Page {page_number}"


def statement_name(text: str) -> str | None:
    if "Účetní závěrka ČEZ Distribuce" not in text or "(v tis. Kč)" not in text:
        return None
    if "Přehled o peněžních tocích" in text or "Peněžní toky z provozní činnosti" in text:
        return "Cash flow statement"
    if "Výkaz zisku a ztráty" in text or "Označení Text Skutečnost" in text and "Výsledek hospodaření" in text:
        return "Income statement"
    if "Rozvaha" in text or "Označení AKTIVA" in text or "Označení PASIVA" in text:
        return "Balance sheet"
    return None


def parse_number(value: str) -> int:
    return int(value.replace(" ", "").replace("\u00a0", ""))


def parse_columns(value: str, count: int, reconcile_assets: bool = False) -> list[int]:
    tokens = value.replace("\u00a0", " ").split()
    candidates: list[tuple[float, list[int]]] = []

    def valid(group: list[str]) -> bool:
        return bool(group) and bool(re.fullmatch(r"-?\d{1,3}", group[0])) and all(
            re.fullmatch(r"\d{3}", token) for token in group[1:]
        )

    def visit(start: int, groups: list[list[str]]) -> None:
        remaining = count - len(groups)
        if remaining == 0:
            if start != len(tokens):
                return
            values = [parse_number(" ".join(group)) for group in groups]
            lengths = [len(group) for group in groups]
            score = -sum(abs(length - (sum(lengths) / len(lengths))) for length in lengths)
            if reconcile_assets and len(values) == 4 and values[0] + values[1] == values[2]:
                score += 100
            candidates.append((score, values))
            return
        max_end = len(tokens) - (remaining - 1)
        for end in range(start + 1, max_end + 1):
            group = tokens[start:end]
            if valid(group):
                visit(end, groups + [group])

    visit(0, [])
    return max(candidates, key=lambda item: item[0])[1] if candidates else []


def parse_asset_columns(value: str) -> dict[str, int | None]:
    candidates: list[tuple[float, list[int]]] = []
    for count in (2, 3, 4):
        values = parse_columns(value, count, reconcile_assets=count == 4)
        if not values:
            continue
        score = float(count)
        if count == 4 and values[0] + values[1] == values[2]:
            score += 100
        elif count == 3 and values[0] == values[1]:
            score += 90
        elif count == 3 and values[0] + values[1] == values[2]:
            score += 80
        elif count == 2 and values[0] + values[1] == 0:
            score += 70
        elif count == 2:
            score += 20
        candidates.append((score, values))
    values = max(candidates, key=lambda item: item[0])[1] if candidates else []
    result = {"gross": None, "adjustments": None, "current": None, "prior": None}
    if len(values) == 4:
        result.update(gross=values[0], adjustments=values[1], current=values[2], prior=values[3])
    elif len(values) == 3 and values[0] == values[1]:
        result.update(gross=values[0], current=values[1], prior=values[2])
    elif len(values) == 3 and values[0] + values[1] == values[2]:
        result.update(gross=values[0], adjustments=values[1], current=values[2])
    elif len(values) == 2 and values[0] + values[1] == 0:
        result.update(gross=values[0], adjustments=values[1])
    elif len(values) == 2:
        result.update(current=values[0], prior=values[1])
    return result


def split_code(label: str) -> tuple[str, str]:
    match = re.match(r"^([A-ZIVXLCDM*]+(?:[. +]+(?:[A-ZIVXLCDM*]+|\d+))*\.?)\s+(.+)$", label)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return "", label.strip()


def financial_rows(text: str, page_number: int) -> list[dict]:
    statement = statement_name(text)
    if not statement:
        return []
    assets_page = "Označení AKTIVA" in text
    rows: list[dict] = []
    continuation = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.isdigit() or any(line.casefold().startswith(prefix) for prefix in SKIP_TITLES):
            continue
        match = NUMBER_SUFFIX.search(line)
        if not match:
            if line and not line.endswith(("období", "Kč)")) and len(line) < 180:
                continuation = line if continuation == "" else f"{continuation} {line}"
            continue
        expected = 4 if statement == "Balance sheet" and assets_page else 2
        values = parse_columns(match.group(1), expected, reconcile_assets=expected == 4)
        if not values:
            values = [parse_number(value) for value in re.findall(NUMBER, match.group(1))]
        label = line[: match.start()].strip()
        if continuation and label and label[0].islower():
            label = f"{continuation} {label}"
        continuation = ""
        if not label or len(values) == 0:
            continue
        code, item = split_code(label)
        if len(values) == 1 and 1900 <= values[0] <= 2100 and "ČEZ Distribuce" in item:
            continue
        row = {
            "statement": statement,
            "code": code,
            "item": item,
            "gross": None,
            "adjustments": None,
            "current": None,
            "prior": None,
            "page": page_number,
        }
        if statement == "Balance sheet" and assets_page:
            row.update(parse_asset_columns(match.group(1)))
        elif len(values) >= 2:
            row.update(current=values[-2], prior=values[-1])
        else:
            row.update(current=values[-1])
        rows.append(row)
    return rows


def select_metrics(rows: list[dict]) -> list[dict]:
    wanted = [
        ("Total assets", r"AKTIVA CELKEM"),
        ("Equity", r"Vlastní kapitál$"),
        ("Revenue from products and services", r"Tržby z prodeje výrobků a služeb"),
        ("Operating profit", r"Provozní výsledek hospodaření"),
        ("Profit before tax", r"Výsledek hospodaření před zdaněním"),
        ("Net profit", r"Výsledek hospodaření za účetní období"),
        ("Operating cash flow", r"Čistý peněžní tok z provozní činnosti"),
        ("Capital expenditure", r"Výdaje spojené s nabytím stálých aktiv"),
    ]
    metrics: list[dict] = []
    for label, pattern in wanted:
        match = next((row for row in rows if re.search(pattern, f'{row["code"]} {row["item"]}', re.I)), None)
        if match:
            metrics.append({
                "metric": label,
                "current": match["current"],
                "prior": match["prior"],
                "page": match["page"],
            })
    return metrics


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: extract_pdf_report.py INPUT_PDF")
    source = io.BytesIO(sys.stdin.buffer.read()) if sys.argv[1] == "-" else Path(sys.argv[1])
    reader = PdfReader(source)
    pages: list[dict] = []
    rows: list[dict] = []
    for index, page in enumerate(reader.pages):
        text = clean_text(page.extract_text() or "")
        page_number = index + 1
        statement = statement_name(text)
        title = statement if statement else page_title(text, page_number)
        pages.append({"page": page_number, "title": title, "text": text[:30000]})
        rows.extend(financial_rows(text, page_number))
    result = {
        "page_count": len(reader.pages),
        "pages": pages,
        "financial_rows": rows,
        "metrics": select_metrics(rows),
    }
    json.dump(result, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
