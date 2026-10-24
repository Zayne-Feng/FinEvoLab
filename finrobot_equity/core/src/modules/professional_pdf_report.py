#!/usr/bin/env python
# coding: utf-8
# Modified by FinEvoLab. Attribution is retained in NOTICE.
"""Render equity reports with financial tables, charts, and flowable page layouts."""

import os
import io
import base64
from datetime import datetime
import pytz
from typing import Dict, List, Optional, Any, Tuple

EASTERN_TZ = pytz.timezone('America/New_York')

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.units import mm, inch
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    Image, PageBreak, CondPageBreak, KeepTogether, HRFlowable, ListFlowable, ListItem
)
from reportlab.pdfgen import canvas
from reportlab.graphics.shapes import Drawing, Line, Rect

import pandas as pd
import numpy as np


class Colors:
    """Define report colors for typography, charts, and sentiment labels."""
    NAVY = colors.HexColor('#1a365d')
    GOLD = colors.HexColor('#c9a227')
    DARK_GRAY = colors.HexColor('#2d3748')
    MEDIUM_GRAY = colors.HexColor('#718096')
    LIGHT_GRAY = colors.HexColor('#e2e8f0')
    WHITE = colors.white

    CHART_PRIMARY = '#1a365d'
    CHART_SECONDARY = '#c9a227'
    CHART_TERTIARY = '#4a5568'
    CHART_GRID = '#e2e8f0'

    POSITIVE = colors.HexColor('#38a169')
    NEGATIVE = colors.HexColor('#e53e3e')
    NEUTRAL = colors.HexColor('#718096')


class Layout:
    """Define page dimensions, margins, and column widths."""
    PAGE_WIDTH, PAGE_HEIGHT = A4
    MARGIN_TOP = 20 * mm
    MARGIN_BOTTOM = 18 * mm
    MARGIN_LEFT = 18 * mm
    MARGIN_RIGHT = 18 * mm

    CONTENT_WIDTH = PAGE_WIDTH - MARGIN_LEFT - MARGIN_RIGHT
    CONTENT_HEIGHT = PAGE_HEIGHT - MARGIN_TOP - MARGIN_BOTTOM

    # Split the content width evenly after reserving the column gap.
    COL_GAP = 8 * mm
    COL_WIDTH = (CONTENT_WIDTH - COL_GAP) / 2


def create_styles() -> Dict[str, ParagraphStyle]:
    """Create paragraph styles for report headings, tables, and narrative text."""
    styles = {}

    styles['CoverTitle'] = ParagraphStyle(
        'CoverTitle',
        fontName='Helvetica-Bold',
        fontSize=32,
        textColor=Colors.WHITE,
        alignment=TA_CENTER,
        spaceAfter=8,
        leading=38,
    )

    styles['CoverSubtitle'] = ParagraphStyle(
        'CoverSubtitle',
        fontName='Helvetica',
        fontSize=16,
        textColor=Colors.GOLD,
        alignment=TA_CENTER,
        spaceAfter=4,
    )

    styles['SectionTitle'] = ParagraphStyle(
        'SectionTitle',
        fontName='Helvetica-Bold',
        fontSize=18,
        textColor=Colors.NAVY,
        alignment=TA_LEFT,
        spaceBefore=16,
        spaceAfter=10,
        leading=22,
    )

    styles['Heading2'] = ParagraphStyle(
        'Heading2',
        fontName='Helvetica-Bold',
        fontSize=13,
        textColor=Colors.NAVY,
        alignment=TA_LEFT,
        spaceBefore=12,
        spaceAfter=6,
        leading=16,
    )

    styles['Heading3'] = ParagraphStyle(
        'Heading3',
        fontName='Helvetica-Bold',
        fontSize=11,
        textColor=Colors.DARK_GRAY,
        alignment=TA_LEFT,
        spaceBefore=8,
        spaceAfter=4,
        leading=14,
    )

    styles['Body'] = ParagraphStyle(
        'Body',
        fontName='Helvetica',
        fontSize=9.5,
        textColor=Colors.DARK_GRAY,
        alignment=TA_JUSTIFY,
        spaceAfter=6,
        leading=13,
    )

    styles['BodyBold'] = ParagraphStyle(
        'BodyBold',
        fontName='Helvetica-Bold',
        fontSize=9.5,
        textColor=Colors.DARK_GRAY,
        alignment=TA_LEFT,
        spaceAfter=6,
        leading=13,
    )

    styles['BulletPoint'] = ParagraphStyle(
        'BulletPoint',
        fontName='Helvetica',
        fontSize=9.5,
        textColor=Colors.DARK_GRAY,
        alignment=TA_LEFT,
        leftIndent=15,
        spaceAfter=4,
        leading=13,
    )

    styles['Highlight'] = ParagraphStyle(
        'Highlight',
        fontName='Helvetica',
        fontSize=10,
        textColor=Colors.NAVY,
        alignment=TA_LEFT,
        spaceAfter=4,
        leading=14,
    )

    styles['Caption'] = ParagraphStyle(
        'Caption',
        fontName='Helvetica',
        fontSize=8,
        textColor=Colors.MEDIUM_GRAY,
        alignment=TA_LEFT,
        spaceAfter=3,
        leading=10,
    )

    styles['Header'] = ParagraphStyle(
        'Header',
        fontName='Helvetica',
        fontSize=8,
        textColor=Colors.MEDIUM_GRAY,
        alignment=TA_RIGHT,
    )

    styles['Footer'] = ParagraphStyle(
        'Footer',
        fontName='Helvetica',
        fontSize=7,
        textColor=Colors.MEDIUM_GRAY,
        alignment=TA_CENTER,
    )

    styles['Rating'] = ParagraphStyle(
        'Rating',
        fontName='Helvetica-Bold',
        fontSize=14,
        textColor=Colors.POSITIVE,
        alignment=TA_CENTER,
    )

    styles['BigNumber'] = ParagraphStyle(
        'BigNumber',
        fontName='Helvetica-Bold',
        fontSize=24,
        textColor=Colors.NAVY,
        alignment=TA_CENTER,
        leading=28,
    )

    styles['TableTitle'] = ParagraphStyle(
        'TableTitle',
        fontName='Helvetica-Bold',
        fontSize=10,
        textColor=Colors.NAVY,
        alignment=TA_LEFT,
        spaceBefore=8,
        spaceAfter=4,
    )

    return styles


def create_data_table_style():
    """Create financial table styles with header and row separators."""
    return TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), Colors.NAVY),
        ('TEXTCOLOR', (0, 0), (-1, 0), Colors.WHITE),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 8),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('TEXTCOLOR', (0, 1), (-1, -1), Colors.DARK_GRAY),
        ('ALIGN', (0, 1), (0, -1), 'LEFT'),
        ('ALIGN', (1, 1), (-1, -1), 'CENTER'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [Colors.WHITE, colors.HexColor('#f8f9fa')]),
        ('LINEBELOW', (0, 0), (-1, 0), 1.5, Colors.NAVY),
        ('LINEBELOW', (0, 1), (-1, -2), 0.5, colors.HexColor('#dee2e6')),
        ('LINEBELOW', (0, -1), (-1, -1), 1, Colors.NAVY),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#dee2e6')),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ])


def create_key_metrics_style():
    """Create styles for the key-metric summary tables."""
    return TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f8f9fa')),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica'),
        ('FONTNAME', (1, 0), (1, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('TEXTCOLOR', (0, 0), (0, -1), Colors.MEDIUM_GRAY),
        ('TEXTCOLOR', (1, 0), (1, -1), Colors.NAVY),
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ('LINEBELOW', (0, 0), (-1, -1), 0.5, colors.HexColor('#dee2e6')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ])


def clean_markdown_symbols(text):
    """Remove Markdown heading and emphasis markers while preserving their text."""
    if not text:
        return text

    import re
    text = re.sub(r'^#{2,3}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'_\*\*\s*', '', text)
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'\*\*', '', text)

    return text


def format_number(value, format_type='auto'):
    """Format numeric values by display type and preserve preformatted percentage strings."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 'N/A'

    if isinstance(value, str):
        # Preserve preformatted percentage strings.
        if '%' in value:
            return value
        try:
            clean_val = value.replace(',', '').replace('$', '').replace('%', '')
            num = float(clean_val)
        except:
            return value
    else:
        try:
            num = float(value)
        except:
            return str(value)

    if format_type == 'currency':
        return f"${num:,.2f}"
    elif format_type == 'percent':
        return f"{num:.1f}%"
    elif format_type == 'ratio':
        return f"{num:.2f}x"
    elif format_type == 'billions':
        return f"${num/1e9:,.1f}B"
    elif format_type == 'millions':
        return f"${num/1e6:,.1f}M"
    elif format_type == 'auto':
        abs_num = abs(num)

        # Scale large magnitudes using T, B, or M suffixes.
        if abs_num >= 1e12:
            return f"${num/1e12:,.1f}T"
        elif abs_num >= 1e9:
            return f"${num/1e9:,.1f}B"
        elif abs_num >= 1e6:
            return f"${num/1e6:,.1f}M"
        elif abs_num >= 1e3:
            return f"{num:,.0f}"  # Group thousands without fractional digits.
        elif abs_num < 100 and abs_num > 0:
            # Keep two fractional digits for nonzero magnitudes below 100.
            return f"{num:.2f}"
        elif abs_num == 0:
            return "0"
        else:
            return f"{num:,.2f}"

    return str(value)


def create_separator():
    """Create a horizontal separator within the available content width."""
    return HRFlowable(
        width="100%",
        thickness=0.5,
        color=Colors.LIGHT_GRAY,
        spaceBefore=8,
        spaceAfter=8
    )


def load_image_from_base64(base64_str, width, height):
    """Decode a Base64 image and size it for a report flowable."""
    try:
        if base64_str.startswith('data:image'):
            img_data = base64_str.split(',')[1]
        else:
            img_data = base64_str
        img_bytes = base64.b64decode(img_data)
        img_buffer = io.BytesIO(img_bytes)
        return Image(img_buffer, width=width, height=height)
    except Exception as e:
        return None


class ProfessionalEquityReport:
    """Compose equity-report sections from supplied financial and narrative data."""

    def __init__(self, output_path: str, data: Dict[str, Any]):
        self.output_path = output_path
        self.data = data
        self.styles = create_styles()
        self.elements = []
        self.page_number = 0

        self.doc = SimpleDocTemplate(
            output_path,
            pagesize=A4,
            topMargin=Layout.MARGIN_TOP,
            bottomMargin=Layout.MARGIN_BOTTOM,
            leftMargin=Layout.MARGIN_LEFT,
            rightMargin=Layout.MARGIN_RIGHT,
        )

        self.ticker = data.get('company_ticker', 'TICK')
        self.company_name = data.get('company_name_full', 'Company')

    def build_cover_page(self):
        """Build the cover with company identity, rating, valuation, and key metrics."""
        cover_elements = []
        cover_elements.append(Spacer(1, 12*mm))
        cover_elements.append(Paragraph("EQUITY RESEARCH", self.styles['CoverSubtitle']))
        cover_elements.append(Spacer(1, 4*mm))
        cover_elements.append(Paragraph(self.company_name.upper(), self.styles['CoverTitle']))
        cover_elements.append(Spacer(1, 3*mm))
        cover_elements.append(Paragraph(f"{self.ticker} | {self.data.get('sector', 'Technology')}", self.styles['CoverSubtitle']))
        cover_elements.append(Spacer(1, 12*mm))

        rating = self.data.get('rating', 'N/A')
        target = self.data.get('target_price', 'N/A')
        price = self.data.get('share_price', 'N/A')

        rating_color = Colors.POSITIVE if rating in ['Buy', 'Outperform', 'Overweight'] else (
            Colors.NEGATIVE if rating in ['Sell', 'Underperform', 'Underweight'] else Colors.GOLD
        )

        rating_style = ParagraphStyle('RatingBox', fontName='Helvetica-Bold', fontSize=20,
                                      textColor=rating_color, alignment=TA_CENTER)
        cover_elements.append(Paragraph(f"Rating: {rating}", rating_style))
        cover_elements.append(Spacer(1, 3*mm))

        price_style = ParagraphStyle('PriceBox', fontName='Helvetica', fontSize=14,
                                     textColor=Colors.WHITE, alignment=TA_CENTER)
        price_label = 'Snapshot' if self.data.get('numeric_only') else 'Current'
        cover_elements.append(Paragraph(f"{price_label}: {price} | Target: {target}", price_style))
        if self.data.get('numeric_only'):
            as_of_style = ParagraphStyle('SnapshotDate', fontName='Helvetica', fontSize=10,
                                        textColor=Colors.WHITE, alignment=TA_CENTER)
            cover_elements.append(Paragraph(f"Market data as of: {self.data.get('closing_price_date', 'Not provided')}", as_of_style))
        cover_elements.append(Spacer(1, 8*mm))

        date_style = ParagraphStyle('DateStyle', fontName='Helvetica', fontSize=11,
                                    textColor=Colors.LIGHT_GRAY, alignment=TA_CENTER)
        cover_elements.append(Paragraph(self.data.get('report_date', datetime.now(EASTERN_TZ).strftime('%B %Y')), date_style))
        cover_elements.append(Spacer(1, 8*mm))

        inner_table = Table([[cover_elements]], colWidths=[Layout.CONTENT_WIDTH])
        inner_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ]))

        cover_bg = Table([[inner_table]], colWidths=[Layout.CONTENT_WIDTH], rowHeights=[105*mm])
        cover_bg.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), Colors.NAVY),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ]))

        self.elements.append(cover_bg)
        self.elements.append(Spacer(1, 8*mm))

        self.elements.append(Paragraph("Key Metrics Snapshot", self.styles['Heading2']))
        self.elements.append(Spacer(1, 3*mm))

        def format_metric(value, is_percentage=False):
            if value == 'N/A' or value is None:
                return 'N/A'
            if isinstance(value, str):
                if '%' in value:
                    try:
                        num = float(value.replace('%', '').strip())
                        return f"{num:.2f}%"
                    except:
                        return value
                try:
                    num = float(value)
                except:
                    return value
            else:
                num = value

            if is_percentage:
                return f"{num:.2f}%"
            else:
                return f"{num:.2f}"

        metrics_data = [
            ['Market Cap', self.data.get('market_cap', 'N/A')],
            ['P/E (Fwd)', format_metric(self.data.get('fwd_pe', 'N/A'))],
            ['P/B Ratio', format_metric(self.data.get('pb_ratio', 'N/A'))],
            ['ROE', format_metric(self.data.get('roe', 'N/A'), is_percentage=True)],
            ['Dividend Yield', format_metric(self.data.get('dividend_yield', 'N/A'), is_percentage=True)],
            ['52W Range', self.data.get('52w_range', 'N/A')],
        ]

        left_metrics = metrics_data[:3]
        right_metrics = metrics_data[3:]

        col_widths = [50*mm, 35*mm]

        left_table = Table(left_metrics, colWidths=col_widths)
        left_table.setStyle(create_key_metrics_style())

        right_table = Table(right_metrics, colWidths=col_widths)
        right_table.setStyle(create_key_metrics_style())

        total_table_width = sum(col_widths) * 2 + 10*mm
        side_padding = (Layout.CONTENT_WIDTH - total_table_width) / 2

        metrics_layout = Table(
            [[left_table, Spacer(10*mm, 1), right_table]],
            colWidths=[sum(col_widths), 10*mm, sum(col_widths)]
        )
        metrics_layout.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ]))

        self.elements.append(metrics_layout)
        self.elements.append(Spacer(1, 6*mm))

        tagline = self.data.get('tagline', '')
        if tagline:
            self.elements.append(Paragraph("Investment Thesis", self.styles['Heading2']))
            tagline = clean_markdown_symbols(tagline)
            highlight_data = [[Paragraph(tagline, self.styles['Highlight'])]]
            highlight_table = Table(highlight_data, colWidths=[Layout.CONTENT_WIDTH - 10*mm])
            highlight_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f7fafc')),
                ('BOX', (0, 0), (-1, -1), 1, Colors.GOLD),
                ('LEFTPADDING', (0, 0), (-1, -1), 12),
                ('RIGHTPADDING', (0, 0), (-1, -1), 12),
                ('TOPPADDING', (0, 0), (-1, -1), 10),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
            ]))
            self.elements.append(highlight_table)

    def build_company_overview_section(self):
        """Build the company overview and investment thesis sections."""
        self.elements.append(PageBreak())

        self.elements.append(Paragraph("Company Overview", self.styles['SectionTitle']))
        self.elements.append(create_separator())

        overview = self.data.get('company_overview', '')
        if overview:
            overview = clean_markdown_symbols(overview)
            paragraphs = overview.split('\n')
            for para in paragraphs:
                para = para.strip()
                if para:
                    self.elements.append(Paragraph(para, self.styles['Body']))
                    self.elements.append(Spacer(1, 2*mm))

        self.elements.append(Spacer(1, 6*mm))

        investment = self.data.get('investment_overview', '')
        if investment:
            self.elements.append(Paragraph("Investment Overview", self.styles['Heading2']))
            self.elements.append(Spacer(1, 2*mm))

            investment = clean_markdown_symbols(investment)
            paragraphs = investment.split('\n')
            for para in paragraphs:
                para = para.strip()
                if para:
                    self.elements.append(Paragraph(para, self.styles['Body']))
                    self.elements.append(Spacer(1, 2*mm))

    def build_financial_analysis_section(self):
        """Build financial analysis sections with paired narrative and chart columns."""
        self.elements.append(Spacer(1, 8*mm))

        self.elements.append(Paragraph("Financial Analysis", self.styles['SectionTitle']))
        self.elements.append(create_separator())

        fixed_width = 82 * mm
        fixed_height = 52 * mm

        self.elements.append(Paragraph("Revenue & EBITDA Performance", self.styles['Heading2']))
        self.elements.append(Spacer(1, 2*mm))

        revenue_analysis = self._generate_revenue_analysis()
        revenue_chart = self.data.get('revenue_chart_path', '')

        left_content = [Paragraph(revenue_analysis, self.styles['Body'])]

        analysis_df = self.data.get('analysis_df')
        if analysis_df is not None and not analysis_df.empty:
            key_data = self._extract_key_metrics(analysis_df, ['Revenue', 'EBITDA', 'Revenue Growth'])
            if key_data:
                left_content.append(Spacer(1, 2*mm))
                left_content.append(Paragraph("Key Figures:", self.styles['BodyBold']))
                for metric, value in key_data.items():
                    left_content.append(Paragraph(f"• {metric}: {value}", self.styles['BulletPoint']))

        right_content = []
        if revenue_chart and revenue_chart.startswith('data:image'):
            img = load_image_from_base64(revenue_chart, fixed_width, fixed_height)
            if img:
                right_content.append(img)
                right_content.append(Paragraph("Source: Company Filings", self.styles['Caption']))

        if right_content:
            layout = Table([[left_content, right_content]],
                          colWidths=[fixed_width + 2*mm, fixed_width + 2*mm])
            layout.setStyle(TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ]))
            # Keep each chart and its accompanying text in the same PDF block.
            self.elements.append(KeepTogether([layout]))
        else:
            for item in left_content:
                self.elements.append(item)

        self.elements.append(Spacer(1, 6*mm))

        eps_section = []
        eps_section.append(Paragraph("Earnings & Valuation Metrics", self.styles['Heading2']))
        eps_section.append(Spacer(1, 2*mm))

        eps_analysis = self._generate_eps_analysis()
        eps_chart = self.data.get('eps_pe_chart_path', '')

        left_content = [Paragraph(eps_analysis, self.styles['Body'])]

        if analysis_df is not None and not analysis_df.empty:
            key_data = self._extract_key_metrics(analysis_df, ['EPS', 'PE Ratio'])
            if key_data:
                left_content.append(Spacer(1, 2*mm))
                left_content.append(Paragraph("Key Figures:", self.styles['BodyBold']))
                for metric, value in key_data.items():
                    left_content.append(Paragraph(f"• {metric}: {value}", self.styles['BulletPoint']))

        right_content = []
        if eps_chart and eps_chart.startswith('data:image'):
            img = load_image_from_base64(eps_chart, fixed_width, fixed_height)
            if img:
                right_content.append(img)
                right_content.append(Paragraph("Source: Company Filings", self.styles['Caption']))

        if right_content:
            layout = Table([[left_content, right_content]],
                          colWidths=[fixed_width + 2*mm, fixed_width + 2*mm])
            layout.setStyle(TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ]))
            eps_section.append(layout)
        else:
            eps_section.extend(left_content)

        # Keep the section's flowables in one PDF block.
        self.elements.append(KeepTogether(eps_section))

    def build_valuation_section(self):
        """Build valuation narrative, peer tables, and valuation charts."""
        self.elements.append(Spacer(1, 8*mm))

        self.elements.append(Paragraph("Valuation Analysis", self.styles['SectionTitle']))
        self.elements.append(create_separator())

        valuation = self.data.get('valuation_overview', '')
        if valuation:
            valuation = clean_markdown_symbols(valuation)
            paragraphs = valuation.split('\n')
            for para in paragraphs:
                para = para.strip()
                if para:
                    self.elements.append(Paragraph(para, self.styles['Body']))
                    self.elements.append(Spacer(1, 2*mm))

        self.elements.append(Spacer(1, 6*mm))

        self.elements.append(Paragraph("Peer Comparison", self.styles['Heading2']))
        self.elements.append(Spacer(1, 3*mm))

        peer_df = self.data.get('peer_comparison_df')
        ev_chart = self.data.get('ev_ebitda_chart_path', '')

        fixed_width = 82 * mm
        fixed_height = 50 * mm

        left_content = []
        if peer_df is not None and not peer_df.empty:
            table = self._create_peer_table_fixed(peer_df, fixed_width)
            if table:
                left_content.append(table)
        else:
            left_content.append(Paragraph("Peer comparison data not available.", self.styles['Body']))

        right_content = []
        if ev_chart and ev_chart.startswith('data:image'):
            img = load_image_from_base64(ev_chart, fixed_width, fixed_height)
            if img:
                right_content.append(img)
                right_content.append(Paragraph("EV/EBITDA Peer Comparison", self.styles['Caption']))

        if left_content and right_content:
            layout = Table([[left_content, right_content]],
                          colWidths=[fixed_width + 2*mm, fixed_width + 2*mm])
            layout.setStyle(TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ]))
            # Keep the peer table and its chart in the same PDF block.
            self.elements.append(KeepTogether([layout]))
        else:
            for item in left_content:
                self.elements.append(item)

    def build_competition_risk_section(self):
        """Build competitive analysis, risk factors, and key takeaways."""
        self.elements.append(Spacer(1, 8*mm))

        self.elements.append(Paragraph("Competitive Landscape", self.styles['SectionTitle']))
        self.elements.append(create_separator())

        competitor = self.data.get('competitor_analysis', '')
        if competitor:
            competitor = clean_markdown_symbols(competitor)
            paragraphs = competitor.split('\n')
            for para in paragraphs:
                para = para.strip()
                if para:
                    self.elements.append(Paragraph(para, self.styles['Body']))
                    self.elements.append(Spacer(1, 2*mm))

        self.elements.append(Spacer(1, 5*mm))

        self.elements.append(Paragraph("Risk Factors", self.styles['Heading2']))

        risks = self.data.get('risks', '')
        if risks:
            risks = clean_markdown_symbols(risks)
            risk_lines = risks.strip().split('\n')
            for line in risk_lines:
                line = line.strip()
                if line:
                    # Strip existing bullet prefixes before rendering bullet paragraphs.
                    line = line.lstrip('•').lstrip('-').strip()
                    if line:
                        self.elements.append(Paragraph(f"▪ {line}", self.styles['BulletPoint']))

        self.elements.append(Spacer(1, 5*mm))

        takeaways = self.data.get('major_takeaways', '')
        if takeaways:
            self.elements.append(Paragraph("Key Takeaways", self.styles['Heading2']))

            takeaways = clean_markdown_symbols(takeaways)
            takeaway_lines = takeaways.strip().split('\n')
            for line in takeaway_lines:
                line = line.strip()
                if line:
                    self.elements.append(Paragraph(f"✓ {line}", self.styles['BulletPoint']))

    def build_news_section(self):
        """Build news summaries and categorized news analysis."""
        news_summary = self.data.get('news_summary', '')
        enhanced_news = self.data.get('enhanced_news', {})
        retail_sentiment = self.data.get('retail_sentiment', {})
        company_news = self.data.get('company_news', [])

        if not news_summary and not enhanced_news and not retail_sentiment:
            return

        self.elements.append(Spacer(1, 8*mm))
        self.elements.append(Paragraph("Recent News & Events", self.styles['SectionTitle']))
        self.elements.append(create_separator())

        if news_summary:
            self.elements.append(Paragraph("News Summary", self.styles['Heading2']))
            self.elements.append(Spacer(1, 2*mm))
            news_summary = clean_markdown_symbols(news_summary)
            paragraphs = news_summary.split('\n')
            for para in paragraphs:
                para = para.strip()
                if para:
                    self.elements.append(Paragraph(para, self.styles['Body']))
                    self.elements.append(Spacer(1, 2*mm))

        if enhanced_news:
            if enhanced_news.get('sentiment_summary'):
                sentiment = enhanced_news['sentiment_summary']
                overall = sentiment.get('overall', 'neutral')
                sentiment_color = Colors.POSITIVE if overall == 'positive' else (
                    Colors.NEGATIVE if overall == 'negative' else Colors.NEUTRAL
                )
                sentiment_style = ParagraphStyle(
                    'SentimentStyle', fontName='Helvetica-Bold', fontSize=10,
                    textColor=sentiment_color, alignment=TA_LEFT
                )
                self.elements.append(Spacer(1, 3*mm))
                self.elements.append(Paragraph(f"Overall Sentiment: {overall.upper()}", sentiment_style))

            if enhanced_news.get('categories'):
                self.elements.append(Spacer(1, 3*mm))
                self.elements.append(Paragraph("News by Category:", self.styles['BodyBold']))
                for cat, count in enhanced_news['categories'].items():
                    self.elements.append(Paragraph(f"• {cat}: {count} articles", self.styles['BulletPoint']))

        if retail_sentiment:
            self.elements.append(Spacer(1, 4 * mm))
            self.elements.append(Paragraph("Retail Sentiment Insights", self.styles['Heading2']))

            summary_items = []
            if retail_sentiment.get('average_buzz') is not None:
                summary_items.append(f"Average Buzz: {retail_sentiment['average_buzz']}/100")
            if retail_sentiment.get('bullish_avg') is not None:
                summary_items.append(f"Bullish Avg: {retail_sentiment['bullish_avg']}%")
            summary_items.append(f"Source Alignment: {retail_sentiment.get('source_alignment', 'No coverage')}")
            summary_items.append(f"Coverage: {retail_sentiment.get('coverage', '0/3')}")

            for item in summary_items:
                self.elements.append(Paragraph(f"• {item}", self.styles['BulletPoint']))

            for source in retail_sentiment.get('sources', []):
                if not source.get('has_data'):
                    continue
                bullish_text = (
                    f"{source['bullish_pct']}%"
                    if source.get('bullish_pct') is not None
                    else 'N/A'
                )
                self.elements.append(
                    Paragraph(
                        f"{source['label']}: Buzz {source['buzz_score']}/100, "
                        f"Bullish {bullish_text}, "
                        f"{source['activity_label']} {source['activity_value']}, "
                        f"Trend {source['trend']}",
                        self.styles['Body'],
                    )
                )
                self.elements.append(Spacer(1, 1.5 * mm))


    def build_sensitivity_section(self):
        """Build sensitivity summaries and confidence-interval tables."""
        sensitivity = self.data.get('sensitivity_analysis', {})

        if not sensitivity:
            return

        self.elements.append(Spacer(1, 8*mm))
        self.elements.append(Paragraph("Sensitivity Analysis", self.styles['SectionTitle']))
        self.elements.append(create_separator())

        if sensitivity.get('summary'):
            summary = clean_markdown_symbols(sensitivity['summary'])
            paragraphs = summary.split('\n')
            for para in paragraphs:
                para = para.strip()
                if para:
                    self.elements.append(Paragraph(para, self.styles['Body']))
                    self.elements.append(Spacer(1, 2*mm))

        if sensitivity.get('confidence_intervals'):
            self.elements.append(Spacer(1, 3*mm))
            self.elements.append(Paragraph("Forecast Confidence Intervals (95%):", self.styles['Heading3']))
            for metric, ci in sensitivity['confidence_intervals'].items():
                if ci:
                    low = ci.get('low', 'N/A')
                    high = ci.get('high', 'N/A')
                    self.elements.append(Paragraph(f"• {metric}: {low} - {high}", self.styles['BulletPoint']))

    def build_catalyst_section(self):
        """Build catalyst summaries and sentiment-grouped catalyst lists."""
        catalyst = self.data.get('catalyst_analysis', {})

        if not catalyst:
            return

        self.elements.append(Spacer(1, 8*mm))
        self.elements.append(Paragraph("Key Catalysts", self.styles['SectionTitle']))
        self.elements.append(create_separator())

        if catalyst.get('summary'):
            summary = clean_markdown_symbols(catalyst['summary'])
            paragraphs = summary.split('\n')
            for para in paragraphs:
                para = para.strip()
                if para:
                    self.elements.append(Paragraph(para, self.styles['Body']))
                    self.elements.append(Spacer(1, 2*mm))

        top_catalysts = catalyst.get('top_catalysts', [])
        if top_catalysts:
            self.elements.append(Spacer(1, 3*mm))
            self.elements.append(Paragraph("Upcoming Catalysts:", self.styles['Heading3']))

            for cat in top_catalysts:
                event_type = cat.get('event_type', 'Event')
                description = cat.get('description', '')
                sentiment = cat.get('sentiment', 'neutral')
                impact = cat.get('impact_level', cat.get('impact', 'medium'))

                if sentiment == 'positive':
                    prefix = "▲"
                    color = Colors.POSITIVE
                elif sentiment == 'negative':
                    prefix = "▼"
                    color = Colors.NEGATIVE
                else:
                    prefix = "●"
                    color = Colors.NEUTRAL

                catalyst_style = ParagraphStyle(
                    'CatalystItem', fontName='Helvetica', fontSize=9,
                    textColor=color, alignment=TA_LEFT, leftIndent=15
                )

                text = f"{prefix} [{event_type.upper()}] {description} (Impact: {impact})"
                self.elements.append(Paragraph(text, catalyst_style))
                self.elements.append(Spacer(1, 1*mm))

        categorized = catalyst.get('categorized', {})
        if categorized:
            self.elements.append(Spacer(1, 4*mm))

            if categorized.get('positive'):
                self.elements.append(Paragraph("Positive Catalysts:", self.styles['BodyBold']))
                for item in categorized['positive']:
                    desc = item.get('description', '')
                    if desc:
                        self.elements.append(Paragraph(f"✓ {desc}", self.styles['BulletPoint']))

            if categorized.get('negative'):
                self.elements.append(Spacer(1, 2*mm))
                self.elements.append(Paragraph("Risk Factors:", self.styles['BodyBold']))
                for item in categorized['negative']:
                    desc = item.get('description', '')
                    if desc:
                        self.elements.append(Paragraph(f"⚠ {desc}", self.styles['BulletPoint']))

    def build_advanced_charts_section(self):
        """Build price, technical-indicator, financial-ratio, and cash-flow chart sections."""
        stock_price_chart = self.data.get('stock_price_chart_path', '')
        technical_chart = self.data.get('technical_indicators_path', '')
        radar_chart = self.data.get('financial_radar_path', '')
        cashflow_chart = self.data.get('cash_flow_chart_path', '')

        has_advanced_charts = any([stock_price_chart, technical_chart, radar_chart, cashflow_chart])

        if not has_advanced_charts:
            return
        self.elements.append(CondPageBreak(100 * mm))
        self.elements.append(Spacer(1, 8*mm))
        title = "Cash Flow Review" if self.data.get('numeric_only') else "Technical & Advanced Analysis"
        self.elements.append(Paragraph(title, self.styles['SectionTitle']))
        self.elements.append(create_separator())

        fixed_width = 82 * mm
        fixed_height = 52 * mm

        if stock_price_chart and stock_price_chart.startswith('data:image'):
            stock_section = []
            stock_section.append(Paragraph("Stock Price Performance", self.styles['Heading2']))
            stock_section.append(Spacer(1, 2*mm))

            stock_analysis = self._generate_stock_price_analysis()
            left_content = [Paragraph(stock_analysis, self.styles['Body'])]

            left_content.append(Spacer(1, 2*mm))
            left_content.append(Paragraph("Key Technical Levels:", self.styles['BodyBold']))
            left_content.append(Paragraph("• MA20: Short-term trend indicator", self.styles['BulletPoint']))
            left_content.append(Paragraph("• MA50: Medium-term support/resistance", self.styles['BulletPoint']))
            left_content.append(Paragraph("• MA200: Long-term trend direction", self.styles['BulletPoint']))

            right_content = []
            img = load_image_from_base64(stock_price_chart, fixed_width, fixed_height)
            if img:
                right_content.append(img)
                right_content.append(Paragraph("Stock Price with Moving Averages", self.styles['Caption']))

            if right_content:
                layout = Table([[left_content, right_content]],
                              colWidths=[fixed_width + 2*mm, fixed_width + 2*mm])
                layout.setStyle(TableStyle([
                    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ]))
                stock_section.append(layout)
            else:
                stock_section.extend(left_content)

            self.elements.append(KeepTogether(stock_section))
            self.elements.append(Spacer(1, 6*mm))

        if technical_chart and technical_chart.startswith('data:image'):
            tech_section = []
            tech_section.append(Paragraph("Technical Indicators", self.styles['Heading2']))
            tech_section.append(Spacer(1, 2*mm))

            tech_analysis = self._generate_technical_analysis()
            left_content = [Paragraph(tech_analysis, self.styles['Body'])]

            left_content.append(Spacer(1, 2*mm))
            left_content.append(Paragraph("Indicator Interpretation:", self.styles['BodyBold']))
            left_content.append(Paragraph("• RSI > 70: Overbought condition", self.styles['BulletPoint']))
            left_content.append(Paragraph("• RSI < 30: Oversold condition", self.styles['BulletPoint']))
            left_content.append(Paragraph("• MACD crossover: Momentum signal", self.styles['BulletPoint']))

            right_content = []
            img = load_image_from_base64(technical_chart, fixed_width, fixed_height)
            if img:
                right_content.append(img)
                right_content.append(Paragraph("RSI & MACD Technical Indicators", self.styles['Caption']))

            if right_content:
                layout = Table([[left_content, right_content]],
                              colWidths=[fixed_width + 2*mm, fixed_width + 2*mm])
                layout.setStyle(TableStyle([
                    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ]))
                tech_section.append(layout)
            else:
                tech_section.extend(left_content)

            self.elements.append(KeepTogether(tech_section))
            self.elements.append(Spacer(1, 6*mm))

        if radar_chart and radar_chart.startswith('data:image'):
            radar_section = []
            radar_section.append(Paragraph("Financial Ratio Analysis", self.styles['Heading2']))
            radar_section.append(Spacer(1, 2*mm))

            radar_analysis = self._generate_radar_analysis()
            left_content = [Paragraph(radar_analysis, self.styles['Body'])]

            left_content.append(Spacer(1, 2*mm))
            left_content.append(Paragraph("Key Ratios Explained:", self.styles['BodyBold']))
            left_content.append(Paragraph("• ROE: Return on shareholder equity", self.styles['BulletPoint']))
            left_content.append(Paragraph("• ROA: Asset utilization efficiency", self.styles['BulletPoint']))
            left_content.append(Paragraph("• Margins: Profitability indicators", self.styles['BulletPoint']))

            right_content = []
            img = load_image_from_base64(radar_chart, fixed_width, fixed_height)
            if img:
                right_content.append(img)
                right_content.append(Paragraph("Financial Ratio Radar Chart", self.styles['Caption']))

            if right_content:
                layout = Table([[left_content, right_content]],
                              colWidths=[fixed_width + 2*mm, fixed_width + 2*mm])
                layout.setStyle(TableStyle([
                    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ]))
                radar_section.append(layout)
            else:
                radar_section.extend(left_content)

            self.elements.append(KeepTogether(radar_section))
            self.elements.append(Spacer(1, 6*mm))

        if cashflow_chart and cashflow_chart.startswith('data:image'):
            cf_section = []
            cf_section.append(Paragraph("Cash Flow Analysis", self.styles['Heading2']))
            cf_section.append(Spacer(1, 2*mm))

            cf_analysis = self._generate_cashflow_analysis()
            left_content = [Paragraph(cf_analysis, self.styles['Body'])]

            left_content.append(Spacer(1, 2*mm))
            left_content.append(Paragraph("Cash Flow Components:", self.styles['BodyBold']))
            left_content.append(Paragraph("• Operating CF: Core business cash generation", self.styles['BulletPoint']))
            left_content.append(Paragraph("• Investing CF: Capital expenditure & investments", self.styles['BulletPoint']))
            left_content.append(Paragraph("• Financing CF: Debt & equity transactions", self.styles['BulletPoint']))

            right_content = []
            img = load_image_from_base64(cashflow_chart, fixed_width, fixed_height)
            if img:
                right_content.append(img)
                right_content.append(Paragraph("Cash Flow Statement Analysis", self.styles['Caption']))

            if right_content:
                layout = Table([[left_content, right_content]],
                              colWidths=[fixed_width + 2*mm, fixed_width + 2*mm])
                layout.setStyle(TableStyle([
                    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ]))
                cf_section.append(layout)
            else:
                cf_section.extend(left_content)

            self.elements.append(KeepTogether(cf_section))

    def _generate_stock_price_analysis(self) -> str:
        """Return explanatory text for moving-average and trading-volume charts."""
        return (
            f"{self.company_name}'s stock price performance is analyzed using multiple moving averages "
            f"to identify trend direction and potential support/resistance levels. The chart displays "
            f"the 20-day, 50-day, and 200-day moving averages alongside daily closing prices. "
            f"Volume analysis provides insight into trading activity and potential price momentum. "
            f"Investors should monitor crossover signals between moving averages as potential entry/exit indicators."
        )

    def _generate_technical_analysis(self) -> str:
        """Return explanatory text for RSI and MACD charts."""
        return (
            f"Technical analysis for {self.company_name} incorporates momentum indicators including "
            f"the Relative Strength Index (RSI) and Moving Average Convergence Divergence (MACD). "
            f"RSI measures the speed and magnitude of price movements, with readings above 70 suggesting "
            f"overbought conditions and below 30 indicating oversold conditions. MACD provides trend-following "
            f"momentum signals through the relationship between two exponential moving averages."
        )

    def _generate_radar_analysis(self) -> str:
        """Return explanatory text for the financial-ratio radar chart."""
        return (
            f"The financial ratio radar chart provides a multi-dimensional view of {self.company_name}'s "
            f"financial health across key metrics including profitability, efficiency, and leverage ratios. "
            f"This visualization enables quick comparison against industry benchmarks and historical performance. "
            f"Stronger performance is indicated by larger coverage area on the radar chart, while gaps "
            f"highlight areas requiring management attention or further analysis."
        )

    def _generate_cashflow_analysis(self) -> str:
        """Explain operating, investing, and financing cash-flow chart categories."""
        if self.data.get('numeric_only'):
            return ("Operating, investing, and financing cash flows are displayed in USD billions. "
                    "The line sums these three categories and excludes foreign-exchange effects. "
                    "Values refer to the reported fiscal years shown on the axis.")
        return (
            f"{self.company_name}'s cash flow analysis examines the three primary components of cash movement: "
            f"operating, investing, and financing activities. Strong operating cash flow indicates healthy "
            f"core business performance, while investing activities reflect capital allocation decisions. "
            f"Financing cash flows show the company's approach to capital structure management. "
            f"The net cash flow trend provides insight into overall liquidity and financial flexibility."
        )

    def build_financial_appendix(self):
        """Build the financial-summary and credit-metric appendix tables."""
        self.elements.append(Spacer(1, 8*mm))

        self.elements.append(Paragraph("Financial Data", self.styles['SectionTitle']))
        self.elements.append(create_separator())

        financial_df = self.data.get('financial_summary_df')
        if financial_df is not None and not financial_df.empty:
            self.elements.append(Paragraph("Income Statement Summary", self.styles['TableTitle']))
            table = self._create_financial_table(financial_df)
            if table:
                self.elements.append(table)
            self.elements.append(Spacer(1, 5*mm))

        credit_df = self.data.get('credit_cashflow_df')
        if credit_df is not None and not credit_df.empty:
            self.elements.append(Paragraph("Credit & Cash Flow Metrics", self.styles['TableTitle']))
            table = self._create_financial_table(credit_df)
            if table:
                self.elements.append(table)

    def build_disclaimer_page(self):
        """Build the report disclaimer and data-source information."""
        self.elements.append(Spacer(1, 10*mm))
        self.elements.append(create_separator())

        self.elements.append(Paragraph("DISCLAIMER", self.styles['Heading3']))
        self.elements.append(Spacer(1, 3*mm))

        disclaimer = self.data.get('disclaimer_text',
            "This report is for informational purposes only and does not constitute investment advice. "
            "The information contained herein has been obtained from sources believed to be reliable, "
            "but its accuracy cannot be guaranteed. Past performance is not indicative of future results. "
            "Investors should conduct their own due diligence before making any investment decisions."
        )

        disclaimer_style = ParagraphStyle(
            'Disclaimer',
            fontName='Helvetica',
            fontSize=7.5,
            textColor=Colors.MEDIUM_GRAY,
            alignment=TA_JUSTIFY,
            leading=10,
        )

        self.elements.append(Paragraph(disclaimer, disclaimer_style))

        self.elements.append(Spacer(1, 5*mm))

        source_text = f"Data Source: {self.data.get('data_source_text', 'Company Filings, FMP API')}"
        self.elements.append(Paragraph(source_text, self.styles['Caption']))

        report_info = f"Report Generated: {datetime.now(EASTERN_TZ).strftime('%Y-%m-%d %H:%M')} ET | {self.data.get('research_source', 'AI4Finance FinRobot')}"
        self.elements.append(Paragraph(report_info, self.styles['Caption']))

    def _generate_revenue_analysis(self) -> str:
        """Summarize revenue and EBITDA series or explain their numeric-reporting convention."""
        if self.data.get('numeric_only'):
            return ("Revenue and EBITDA are shown for reported fiscal years and explicit forecast scenarios. "
                    "A identifies reported amounts; E identifies assumption-based estimates. "
                    "Scenario estimates are separate from the provider's reported financial data.")
        analysis_df = self.data.get('analysis_df')
        if analysis_df is None or analysis_df.empty:
            return "Revenue and EBITDA analysis data is not available for detailed commentary."

        try:
            years = [col for col in analysis_df.columns if col.endswith('A')][-3:]

            revenue_row = analysis_df[analysis_df['metrics'] == 'Revenue']
            ebitda_row = analysis_df[analysis_df['metrics'] == 'EBITDA']
            growth_row = analysis_df[analysis_df['metrics'] == 'Revenue Growth']

            latest_year = years[-1] if years else ''

            text = f"{self.company_name} has demonstrated "

            if not growth_row.empty and latest_year:
                growth = growth_row[latest_year].values[0]
                if isinstance(growth, str) and '%' in growth:
                    growth_val = float(growth.replace('%', ''))
                else:
                    growth_val = float(growth) if not pd.isna(growth) else 0

                if growth_val > 10:
                    text += "strong revenue growth, "
                elif growth_val > 0:
                    text += "moderate revenue growth, "
                else:
                    text += "challenging revenue performance, "

            text += f"with the company continuing to expand its market presence. "

            if not ebitda_row.empty:
                text += "EBITDA margins reflect the company's operational efficiency and cost management capabilities. "

            text += "Management's focus on profitability optimization has been evident in recent quarters."

            return text
        except Exception as e:
            return f"Revenue analysis indicates continued business operations with performance metrics as shown in the accompanying chart."

    def _generate_eps_analysis(self) -> str:
        """Summarize EPS and valuation multiples or explain their numeric-reporting convention."""
        if self.data.get('numeric_only'):
            return ("The chart shows earnings per share and trailing P/E for the available fiscal years. "
                    "Columns marked E are scenario estimates. Trailing P/E is distinct from forward P/E; "
                    "unavailable forward valuation fields remain N/A.")
        analysis_df = self.data.get('analysis_df')
        if analysis_df is None or analysis_df.empty:
            return "Earnings per share data provides insight into shareholder value creation."

        try:
            eps_row = analysis_df[analysis_df['metrics'] == 'EPS']
            pe_row = analysis_df[analysis_df['metrics'] == 'PE Ratio']

            text = f"{self.company_name}'s earnings trajectory reflects "

            if not eps_row.empty:
                years = [col for col in eps_row.columns if col.endswith('A')]
                if len(years) >= 2:
                    latest = pd.to_numeric(eps_row[years[-1]].values[0], errors='coerce')
                    prev = pd.to_numeric(eps_row[years[-2]].values[0], errors='coerce')
                    if not pd.isna(latest) and not pd.isna(prev):
                        if latest > prev:
                            text += "positive earnings momentum, "
                        else:
                            text += "earnings pressure, "

            text += "while valuation multiples indicate market expectations for future growth. "

            if not pe_row.empty:
                text += "The P/E ratio comparison against historical averages and peers provides context for current market pricing."

            return text
        except:
            return "Earnings analysis shows the company's profitability trends over the analysis period."

    def _extract_key_metrics(self, df: pd.DataFrame, metrics: List[str]) -> Dict[str, str]:
        """Return formatted values for the requested metrics from the last actual-year column."""
        result = {}
        try:
            years = [col for col in df.columns if col.endswith('A')]
            latest_year = years[-1] if years else None

            if latest_year:
                for metric in metrics:
                    row = df[df['metrics'] == metric]
                    if not row.empty:
                        value = row[latest_year].values[0]
                        result[f"{metric} ({latest_year})"] = format_number(value)
        except:
            pass
        return result

    def _create_financial_table(self, df: pd.DataFrame) -> Optional[Table]:
        """Create a financial table with metric labels and fiscal-year columns."""
        try:
            if df is None or df.empty:
                return None

            headers = ['Metric'] + list(df.columns)
            data = [headers]

            for idx, row in df.iterrows():
                row_data = [str(idx)]
                for val in row:
                    row_data.append(format_number(val))
                data.append(row_data)

            # Reserve the metric column and divide the remaining width among fiscal columns.
            n_cols = len(headers)
            first_col = 40 * mm
            other_cols = (Layout.CONTENT_WIDTH - first_col) / (n_cols - 1) if n_cols > 1 else Layout.CONTENT_WIDTH
            col_widths = [first_col] + [other_cols] * (n_cols - 1)

            table = Table(data, colWidths=col_widths, repeatRows=1)
            table.setStyle(create_data_table_style())
            return table
        except Exception as e:
            return None

    def _create_peer_table(self, df: pd.DataFrame) -> Optional[Table]:
        """Create a peer table using at most four data columns and six rows."""
        try:
            if df is None or df.empty:
                return None

            headers = ['Company'] + list(df.columns)[:4]  # Retain at most four data columns.
            data = [headers]

            for idx, row in df.head(6).iterrows():
                row_data = [str(idx)]
                for val in list(row)[:4]:
                    row_data.append(format_number(val))
                data.append(row_data)

            n_cols = len(data[0])
            col_widths = [80 * mm / n_cols] * n_cols

            table = Table(data, colWidths=col_widths)
            table.setStyle(create_data_table_style())
            return table
        except:
            return None

    def _create_peer_table_fixed(self, df: pd.DataFrame, total_width: float) -> Optional[Table]:
        """Create a peer table within a fixed width using at most three data columns."""
        try:
            if df is None or df.empty:
                return None

            headers = ['Year'] + list(df.columns)[:3]  # Retain at most three data columns.
            data = [headers]

            for idx, row in df.head(6).iterrows():
                row_data = [str(idx)]
                for val in list(row)[:3]:
                    row_data.append(format_number(val))
                data.append(row_data)

            n_cols = len(data[0])
            col_widths = [total_width / n_cols] * n_cols

            table = Table(data, colWidths=col_widths)
            table.setStyle(create_data_table_style())
            return table
        except:
            return None

    def _add_pdf_metadata(self, canvas_obj, doc):
        """Set document metadata for the first page."""
        canvas_obj.setTitle(f"{self.company_name} ({self.ticker}) - Equity Research Report")
        canvas_obj.setAuthor(self.data.get('research_source', 'AI4Finance FinRobot'))
        canvas_obj.setSubject(f"Equity Research Report for {self.company_name}")
        canvas_obj.setKeywords(f"{self.ticker}, {self.company_name}, Equity Research, Investment Analysis")
        canvas_obj.setCreator('FinRobot Equity Research Platform')

    def add_page_header_footer(self, canvas_obj, doc):
        """Draw the report header and footer."""
        canvas_obj.saveState()

        canvas_obj.setFont('Helvetica', 8)
        canvas_obj.setFillColor(Colors.MEDIUM_GRAY)
        canvas_obj.drawString(Layout.MARGIN_LEFT, Layout.PAGE_HEIGHT - 12*mm,
                             f"{self.company_name} ({self.ticker})")
        canvas_obj.drawRightString(Layout.PAGE_WIDTH - Layout.MARGIN_RIGHT,
                                   Layout.PAGE_HEIGHT - 12*mm, "Equity Research")

        canvas_obj.setStrokeColor(Colors.LIGHT_GRAY)
        canvas_obj.setLineWidth(0.5)
        canvas_obj.line(Layout.MARGIN_LEFT, Layout.PAGE_HEIGHT - 14*mm,
                       Layout.PAGE_WIDTH - Layout.MARGIN_RIGHT, Layout.PAGE_HEIGHT - 14*mm)

        canvas_obj.drawCentredString(Layout.PAGE_WIDTH / 2, 10*mm,
                                     f"Page {doc.page}")

        canvas_obj.restoreState()

    def build(self) -> str:
        """Assemble report sections in order and write the PDF with flowable pagination."""
        print("📄 Building professional equity report...")

        # Let section flowables paginate without a page break between every section.
        self.build_cover_page()
        if self.data.get('numeric_only'):
            self.elements.append(PageBreak())
        else:
            self.build_company_overview_section()
        self.build_financial_analysis_section()
        if not self.data.get('numeric_only'):
            self.build_valuation_section()
        self.build_news_section()
        self.build_sensitivity_section()
        self.build_catalyst_section()
        self.build_advanced_charts_section()
        if not self.data.get('numeric_only'):
            self.build_competition_risk_section()
        self.build_financial_appendix()
        self.build_disclaimer_page()

        self.doc.build(
            self.elements,
            onFirstPage=self._add_pdf_metadata,
            onLaterPages=self.add_page_header_footer
        )

        print(f"✅ Professional report generated: {self.output_path}")
        return self.output_path


def generate_professional_report(output_path: str, data: Dict[str, Any]) -> str:
    """Generate an equity-report PDF from structured report data."""
    report = ProfessionalEquityReport(output_path, data)
    return report.build()
