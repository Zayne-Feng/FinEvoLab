"""Render financial reports from validated metrics and source evidence."""

from html import escape
import contextlib
import io
import os
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image

from finevolab.tools import FinancialStore
from finevolab.tool_outputs import ReportArtifacts
from finrobot_equity.core.src.modules.chart_generator import generate_time_series_chart


def build_report(store: FinancialStore, company: str, year: int, output: Path) -> dict:
    result = store.growth(company, "revenue", year)
    output.mkdir(parents=True, exist_ok=True)
    values = [{"year": item.fiscal_year, "start": item.period_start, "end": item.period_end,
               "revenue": f"{item.value:,.0f}", "millions": item.value / 1_000_000}
              for item in result.inputs]
    chart_path = output / "revenue.png"
    frame = pd.DataFrame({"Revenue (USD millions)": [float(v["millions"]) for v in values]},
                         index=[str(v["year"]) for v in values])
    with contextlib.redirect_stdout(io.StringIO()):
        chart = generate_time_series_chart(frame, ["Revenue (USD millions)"], str(chart_path), company,
                                           title="Annual Revenue")
    if not chart:
        raise ValueError("Revenue chart generation failed")
    evidence = [store.evidence[key] for key in result.evidence_ids]
    environment = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"),
                              autoescape=select_autoescape(["html"]))
    html = environment.get_template("report.html").render(
        company=company, year=year, growth=f"{result.value:.6f}", values=values,
        formula=result.formula, chart=chart, evidence=evidence, data_version=store.version,
    )
    html_path, pdf_path = output / "report.html", output / "report.pdf"
    html_path.write_text(html, encoding="utf-8")
    styles = getSampleStyleSheet()
    body = styles["BodyText"]
    body.leading = 14
    story = [Paragraph(f"{escape(company)} | FY{year} Revenue Analysis", styles["Title"]), Spacer(1, 12),
             Paragraph(f"Annual revenue grew {result.value:.6f}% from FY{year - 1} to FY{year}.", body), Spacer(1, 14)]
    rows = [["Fiscal year", "Period", "Revenue (USD)"]] + [
        [str(v["year"]), f"{v['start']} to {v['end']}", v["revenue"]] for v in values]
    table = Table(rows, colWidths=[0.9 * inch, 3.1 * inch, 2.4 * inch])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17324d")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#f1f5f9"), colors.white]),
        ("PADDING", (0, 0), (-1, -1), 9),
        ("ALIGN", (2, 1), (2, -1), "RIGHT"),
    ]))
    story.extend([table, Spacer(1, 12), Paragraph(f"Formula: {escape(result.formula)}", body),
                  Image(str(chart_path), width=6.4 * inch, height=3.84 * inch),
                  Paragraph("Evidence", styles["Heading2"])])
    for item in evidence:
        story.extend([Paragraph(escape(item['source_url']), body),
                      Paragraph(f"Snapshot: {item['source_sha256']}", styles["Code"]),
                      Paragraph(f"Location: {escape(item['locator'])}", body)])
    story.extend([Spacer(1, 12), Paragraph(f"Data version: {store.version}", styles["Code"])])
    SimpleDocTemplate(str(pdf_path), rightMargin=42, leftMargin=42, topMargin=36, bottomMargin=36).build(story)
    return ReportArtifacts(html=str(html_path), pdf=str(pdf_path), chart=str(chart_path)).model_dump(
        mode="json", exclude_none=True)


def build_analysis_report(store: FinancialStore, question: str, result: dict, output: Path) -> dict:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont
    from finevolab.validation import validate_answer

    validate_answer(store, result)
    answer = result["answer"]
    observations = answer.get("metrics", []) + [item for calculation in answer.get("calculations", []) for item in calculation["inputs"]]
    evidence = (result.get("evidence", []) if result.get("answer_type") in {"explanation", "hybrid"} else
                [store.evidence[key] for key in answer["evidence_ids"]])
    narrative = result.get("narrative", answer if result.get("answer_type") == "explanation" else {})
    environment = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"),
                              autoescape=select_autoescape(["html"]))
    output.mkdir(parents=True, exist_ok=True)
    html_path, pdf_path = output / "report.html", output / "report.pdf"
    html_path.write_text(environment.get_template("analysis.html").render(
        question=question, answer=answer, observations=observations, evidence=evidence,
        data_version=store.version, narrative=narrative, overall_status=result.get("overall_status", answer["status"]),
        source_issues=result.get("source_issues", []), review=result.get("review"),
        source_reconciliation=result.get("source_reconciliation", {})), encoding="utf-8")
    font_path = Path(os.environ.get("FINEVOLAB_REPORT_FONT", "C:/Windows/Fonts/simhei.ttf"))
    font_name = "FinancialReport" if font_path.is_file() else "STSong-Light"
    if font_name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(font_name, str(font_path)) if font_path.is_file()
                                else UnicodeCIDFont(font_name))
    styles = getSampleStyleSheet()
    for name in ("BodyText", "Title", "Heading2"):
        styles[name].fontName = font_name
        styles[name].wordWrap = "CJK"
    styles["BodyText"].fontSize, styles["BodyText"].leading = 9, 13
    body = styles["BodyText"]
    paragraph = lambda text: Paragraph(escape(str(text)), body)
    story = [Paragraph("FinEvoLab Financial Analysis", styles["Title"]), Spacer(1, 10),
             paragraph(question), Spacer(1, 8), paragraph("Status: " + result.get("overall_status", answer["status"])),
             paragraph(answer["answer"]), Spacer(1, 12)]
    if observations:
        rows = [[paragraph(value) for value in ("Company / FY", "Metric", "Value / unit", "Actual period")]]
        for item in observations:
            rows.append([paragraph(f"{item['company']} / {item['fiscal_year']}"), paragraph(item["name"]),
                         paragraph(f"{item['value']} {item['unit']}"),
                         paragraph(f"{item['period_start']} to {item['period_end']}")])
        table = Table(rows, colWidths=[75, 100, 140, 195], repeatRows=1)
        table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
                                   ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                   ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5f9")]),
                                   ("PADDING", (0, 0), (-1, -1), 7)]))
        story.extend([table, Spacer(1, 12)])
    for calculation in answer.get("calculations", []):
        label = calculation["metric"] if calculation.get("kind") == "derived" else calculation["metric"] + " growth"
        story.extend([paragraph(f"{calculation['company']} FY{calculation['fiscal_year']} {label}: "
                                f"{calculation['value']} {calculation['unit']}"), paragraph("Formula: " + calculation["formula"])])
        for check in calculation.get("audit", []):
            story.append(paragraph(f"Audit {check['field']}: {check['status']} | {check['rule']} | "
                                   + ", ".join(check["evidence_ids"])))
    if answer.get("periods_aligned") is not None:
        story.append(paragraph("Fiscal periods aligned: " + str(answer["periods_aligned"])))
    story.append(Paragraph("Evidence", styles["Heading2"]))
    if result.get("answer_type") == "hybrid":
        story.extend([Paragraph("Report explanation", styles["Heading2"]), paragraph("Narrative status: " + narrative["status"])])
    for claim in narrative.get("claims", []):
        story.extend([paragraph(claim["statement"]), paragraph("Reference: " + claim["chunk_id"]),
                      paragraph("Source quote: " + claim["quote"]), Spacer(1, 8)])
    for item in evidence:
        story.extend([paragraph(item["id"] + " | " + item["company"]), paragraph(item["source_url"]),
                      paragraph("Location: " + item["locator"]), paragraph("SHA256: " + item["source_sha256"]),
                      paragraph(item.get("text", "")), Spacer(1, 8)])
    if result.get("source_reconciliation"):
        story.append(Paragraph("Source reconciliation", styles["Heading2"]))
        for scope, check in result["source_reconciliation"].items():
            story.append(paragraph(f"{scope}: {check['status']} | Source groups: {check['source_groups']}"))
    if result.get("source_issues"):
        story.append(paragraph("Selected document values differ from the pinned snapshot; the selected source was used."))
    if result.get("review"):
        review = result["review"]
        story.extend([Paragraph("Evidence review", styles["Heading2"]),
                      paragraph("Mode: " + review["mode"] + " | Status: " + review["status"])])
    story.extend([Spacer(1, 8), paragraph("Data version: " + store.version)])
    def footer(canvas, document):
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#64748b"))
        canvas.drawString(42, 22, "FinEvoLab | Page " + str(document.page))
    SimpleDocTemplate(str(pdf_path), rightMargin=42, leftMargin=42, topMargin=36, bottomMargin=36).build(
        story, onFirstPage=footer, onLaterPages=footer)
    return ReportArtifacts(html=str(html_path), pdf=str(pdf_path)).model_dump(mode="json", exclude_none=True)
