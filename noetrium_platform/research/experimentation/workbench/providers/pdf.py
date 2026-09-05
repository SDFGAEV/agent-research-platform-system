"""Dependency-free deterministic vector PDF rendering for research figures."""
from __future__ import annotations

import base64
import math

from ..api import FigureKind, FigureOutputFormat, FigureRendererPort, FigureSpec


def _color(value: str) -> str:
    value = value.lstrip("#")
    if len(value) != 6:
        return "0 0 0"
    return " ".join(f"{int(value[index:index + 2], 16) / 255:.4f}" for index in (0, 2, 4))


def _escape(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _document(content: list[str], width: int, height: int) -> str:
    stream = "\n".join(content).encode("latin-1", "replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream",
    ]
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode())
        output.extend(obj)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    output.extend("".join(f"{offset:010d} 00000 n \n" for offset in offsets[1:]).encode())
    output.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
    return "data:application/pdf;base64," + base64.b64encode(bytes(output)).decode("ascii")
class PdfFigureRenderer(FigureRendererPort):
    """Render FigureSpec as portable vector PDF without optional dependencies."""

    def _text(self, out: list[str], figure: FigureSpec, x: float, y: float, text: object, size: float = 10) -> None:
        out.extend((
            "BT", f"/F1 {size:.1f} Tf", f"{_color(figure.style.foreground)} rg",
            f"1 0 0 1 {x:.2f} {figure.height - y:.2f} Tm",
            f"({_escape(text)}) Tj", "ET",
        ))

    def _line(self, out: list[str], color: str, width: float, *points: tuple[float, float]) -> None:
        if len(points) < 2:
            return
        out.extend((f"{_color(color)} RG", f"{width:.2f} w",
                    f"{points[0][0]:.2f} {points[0][1]:.2f} m"))
        out.extend(f"{x:.2f} {y:.2f} l" for x, y in points[1:])
        out.append("S")

    def _rect(self, out: list[str], color: str, x: float, y: float, width: float, height: float) -> None:
        out.extend((f"{_color(color)} rg", f"{x:.2f} {y:.2f} {width:.2f} {height:.2f} re", "f"))

    @staticmethod
    def _numeric(values: list[object]) -> bool:
        return bool(values) and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values)

    def _plot_points(self, figure: FigureSpec) -> tuple[float, float, float, float, dict[str, float]]:
        values = [point.y for series in figure.series for point in series.points]
        ys = [float(value) for value in values if isinstance(value, (int, float)) and math.isfinite(float(value))]
        xs = [point.x for series in figure.series for point in series.points]
        numeric_x = self._numeric(xs)
        x_values = [float(value) for value in xs] if numeric_x else list(range(len(xs)))
        x_low, x_high = (min(x_values), max(x_values)) if x_values else (0.0, 1.0)
        y_low, y_high = (min(ys), max(ys)) if ys else (0.0, 1.0)
        if x_low == x_high:
            x_low, x_high = x_low - 0.5, x_high + 0.5
        if y_low == y_high:
            y_low, y_high = y_low - 0.5, y_high + 0.5
        return x_low, x_high, y_low, y_high, {str(value): float(index) for index, value in enumerate(xs)}
    def _axes(self, out: list[str], figure: FigureSpec, plot: tuple[float, float, float, float],
               x_low: float, x_high: float, y_low: float, y_high: float) -> None:
        left, top, right, bottom = plot
        width, height = right - left, bottom - top
        grid = figure.style.grid
        for index in range(6):
            x = left + width * index / 5
            y = top + height * index / 5
            self._line(out, grid, 0.45, (x, figure.height - bottom), (x, figure.height - top))
            self._line(out, grid, 0.45, (left, figure.height - y), (right, figure.height - y))
        self._line(out, figure.style.foreground, figure.style.line_width,
                   (left, figure.height - bottom), (right, figure.height - bottom))
        self._line(out, figure.style.foreground, figure.style.line_width,
                   (left, figure.height - top), (left, figure.height - bottom))
        self._text(out, figure, left, top - 26, figure.title, figure.style.title_size)
        self._text(out, figure, (left + right) / 2 - 20, bottom + 30, figure.x_label, figure.style.label_size)
        self._text(out, figure, 12, (top + bottom) / 2, figure.y_label, figure.style.label_size)

    def _render_cells(self, out: list[str], figure: FigureSpec, plot: tuple[float, float, float, float]) -> None:
        left, top, right, bottom = plot
        rows = sorted({cell.row for cell in figure.cells})
        cols = sorted({cell.column for cell in figure.cells})
        if not rows or not cols:
            return
        cell_w, cell_h = (right - left) / len(cols), (bottom - top) / len(rows)
        values = [float(cell.value) for cell in figure.cells]
        low, high = min(values), max(values)
        for cell in figure.cells:
            value = float(cell.value)
            ratio = 0.5 if high == low else (value - low) / (high - low)
            color = figure.style.palette[min(len(figure.style.palette) - 1, int(ratio * len(figure.style.palette)))]
            x = left + cols.index(cell.column) * cell_w
            y = bottom - (rows.index(cell.row) + 1) * cell_h
            self._rect(out, color, x, figure.height - y - cell_h, cell_w, cell_h)
            self._text(out, figure, x + 3, y + cell_h / 2, f"{value:g}", 8)
    def _render_series(
        self,
        out: list[str],
        figure: FigureSpec,
        series: object,
        plot: tuple[float, float, float, float],
        x_low: float,
        x_high: float,
        y_low: float,
        y_high: float,
        categories: dict[str, float],
        index: int,
    ) -> None:
        left, top, right, bottom = plot
        scale_x = (right - left) / (x_high - x_low)
        scale_y = (bottom - top) / (y_high - y_low)
        color = figure.style.palette[index % len(figure.style.palette)]
        points: list[tuple[float, float]] = []
        numeric_x = self._numeric([item.x for item in series.points])
        for point in series.points:
            x = float(point.x) if numeric_x else categories[str(point.x)]
            y = float(point.y)
            px, py = left + (x - x_low) * scale_x, bottom - (y - y_low) * scale_y
            points.append((px, figure.height - py))
            if point.error_low is not None and point.error_high is not None:
                low_y = figure.height - (bottom - (float(point.error_low) - y_low) * scale_y)
                high_y = figure.height - (bottom - (float(point.error_high) - y_low) * scale_y)
                self._line(out, color, 0.8, (px, low_y), (px, high_y))
            self._rect(out, color, px - 2.2, figure.height - py - 2.2, 4.4, 4.4)
        self._line(out, color, figure.style.line_width, *points)
        self._text(out, figure, right - 110, top + 14 + index * 15, series.name, figure.style.legend_size)

    def render(self, figure: FigureSpec, *, output_format: FigureOutputFormat = FigureOutputFormat.PDF) -> str:
        if type(output_format) is not FigureOutputFormat:
            raise TypeError("output_format must be FigureOutputFormat")
        if output_format is not FigureOutputFormat.PDF:
            raise ValueError("PdfFigureRenderer only supports PDF output")
        if type(figure) is not FigureSpec:
            raise TypeError("figure must be FigureSpec")
        out: list[str] = []
        if not figure.style.transparent:
            self._rect(out, figure.style.background, 0, 0, figure.width, figure.height)
        plot = (72.0, 58.0, figure.width - 28.0, figure.height - 66.0)
        if figure.kind in {FigureKind.HEATMAP, FigureKind.CONFUSION_MATRIX}:
            self._render_cells(out, figure, plot)
            self._text(out, figure, 72, 32, figure.title, figure.style.title_size)
            return _document(out, figure.width, figure.height)
        x_low, x_high, y_low, y_high, categories = self._plot_points(figure)
        self._axes(out, figure, plot, x_low, x_high, y_low, y_high)
        left, top, right, bottom = plot
        for index, series in enumerate(figure.series):
            self._render_series(
                out,
                figure,
                series,
                plot,
                x_low,
                x_high,
                y_low,
                y_high,
                categories,
                index,
            )
        if figure.kind in {FigureKind.ROC, FigureKind.CALIBRATION}:
            self._line(out, figure.style.grid, 0.8, (left, figure.height - bottom), (right, figure.height - top))
        return _document(out, figure.width, figure.height)
