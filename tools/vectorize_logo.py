"""Convert the supplied monochrome logo to native SVG paths.

Development helper only; Pillow reads the source without modifying it.
The desktop application uses the resulting vectors and does not need Pillow.
"""

import argparse
from collections import defaultdict
from pathlib import Path
import shutil

from PIL import Image


def simplify(points, tolerance=0.65):
    if len(points) <= 2:
        return points
    ax, ay = points[0]
    bx, by = points[-1]
    dx, dy = bx - ax, by - ay
    divisor = (dx * dx + dy * dy) ** 0.5
    distances = [
        abs(dy * (x - ax) - dx * (y - ay)) / divisor
        if divisor else ((x - ax) ** 2 + (y - ay) ** 2) ** 0.5
        for x, y in points
    ]
    farthest = max(range(len(distances)), key=distances.__getitem__)
    if distances[farthest] <= tolerance:
        return [points[0], points[-1]]
    return simplify(points[:farthest + 1], tolerance)[:-1] + simplify(points[farthest:], tolerance)


def trace(image, box):
    left, top, right, bottom = box
    mask = image.load()

    def ink(x, y):
        return left <= x < right and top <= y < bottom and mask[x, y] < 100

    edges = defaultdict(list)
    for y in range(top, bottom):
        for x in range(left, right):
            if not ink(x, y):
                continue
            if not ink(x, y - 1):
                edges[x, y].append((x + 1, y))
            if not ink(x + 1, y):
                edges[x + 1, y].append((x + 1, y + 1))
            if not ink(x, y + 1):
                edges[x + 1, y + 1].append((x, y + 1))
            if not ink(x - 1, y):
                edges[x, y + 1].append((x, y))
    paths = []
    while edges:
        start = next(iter(edges))
        points = [start]
        current = start
        while True:
            choices = edges[current]
            following = choices.pop()
            if not choices:
                del edges[current]
            points.append(following)
            current = following
            if current == start:
                break
        area = abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:]))) / 2
        if area < 12:
            continue
        points = simplify(points)
        paths.append("M" + " L".join(f"{x},{y}" for x, y in points[:-1]) + " Z")
    return " ".join(paths)


def svg(width, height, content):
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}">{content}</svg>\n'


def main():
    parser = argparse.ArgumentParser(description="Trace the supplied Arandu mark and wordmark as SVG.")
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=Path("assets/branding"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    image = Image.open(args.source).convert("L")
    mark = trace(image, (321, 290, 934, 808))
    wordmark = trace(image, (210, 824, 1044, 991))
    # The horizontal lockup retains the supplied symbol and original lettering.
    symbol = f'<g id="symbol" fill="#000000" fill-rule="evenodd" transform="translate(0 3) scale(0.052) translate(-321 -290)"><path d="{mark}"/></g>'
    name = f'<g id="wordmark" fill="#000000" fill-rule="evenodd" transform="translate(43 9) scale(0.116) translate(-210 -824)"><path d="{wordmark}"/></g>'
    (args.output / "logo.svg").write_text(svg(141, 34, symbol + name))
    mark_group = f'<g id="symbol" fill="#000000" fill-rule="evenodd" transform="translate(55 102) translate(-321 -290)"><path d="{mark}"/></g>'
    (args.output / "mark.svg").write_text(svg(723, 723, mark_group))
    background = '<rect id="background" width="723" height="723" rx="72" fill="#ffffff"/>'
    (args.output / "icon.svg").write_text(svg(723, 723, background + mark_group))
    stacked = f'<g id="symbol" fill="#000000" fill-rule="evenodd"><path d="{mark}"/></g><g id="wordmark" fill="#000000" fill-rule="evenodd"><path d="{wordmark}"/></g>'
    (args.output / "stacked.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="190 270 874 741">' + stacked + '</svg>\n')
    reference = args.output / "reference.png"
    if args.source.resolve() != reference.resolve():
        shutil.copyfile(args.source, reference)
    print(f"Vectores guardados en {args.output}")


if __name__ == "__main__":
    main()
