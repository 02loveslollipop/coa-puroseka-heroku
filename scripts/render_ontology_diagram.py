"""Render only schema classes/relationships from a reviewed Turtle ontology.

Usage: python scripts/render_ontology_diagram.py /path/to/ontology.ttl
The source Turtle and screenshot are never copied into the public repository.
"""

import argparse
from html import escape
import json
from pathlib import Path

from rdflib import Graph, OWL, RDF, RDFS

ROOT = Path(__file__).resolve().parents[1]
# Reference arrangement: events on the left, cards on the right, gachas below.
POSITIONS = {
    "RankingObservation": (110, 360),
    "EventReward": (370, 60),
    "EventRewardRange": (370, 230),
    "Event": (370, 430),
    "EventCardAssociation": (650, 460),
    "Card": (930, 440),
    "Character": (720, 140),
    "CardTrainingCost": (990, 80),
    "CardMasterResource": (1250, 250),
    "CardParameter": (1250, 560),
    "EntityLabel": (650, 290),
    "GachaPickup": (530, 650),
    "GachaCardPoolEntry": (940, 770),
    "Gacha": (660, 870),
    "GachaBehavior": (260, 810),
    "GachaRarityRate": (430, 1040),
}
# Source and target anchors, with intermediate bends clear of concept cards.
ROUTES = {
    "observedInEvent": ([(210, 360), (240, 360), (240, 430), (270, 430)], (210, 420)),
    "rewardOf": ([(370, 99), (370, 191)], (415, 145)),
    "rewardRangeOf": ([(370, 269), (370, 391)], (433, 329)),
    "associatedEvent": ([(550, 460), (510, 460), (510, 350), (440, 350), (440, 391)], (480, 350)),
    "associatedCard": ([(750, 460), (790, 460), (790, 510), (850, 510), (850, 479)], (790, 530)),
    "depicts": ([(900, 401), (900, 230), (720, 230), (720, 179)], (800, 230)),
    "trainingCostOf": ([(990, 119), (990, 330), (960, 330), (960, 401)], (1050, 310)),
    "masterResourceOf": (
        [(1150, 250), (1090, 250), (1090, 440), (1030, 440)],
        (1091, 392),
    ),
    "parameterOf": ([(1150, 560), (1090, 560), (1090, 470), (1030, 470)], (1090, 527)),
    "pickupCard": ([(630, 650), (890, 650), (890, 479)], (765, 650)),
    "poolCard": ([(940, 731), (940, 705), (980, 705), (980, 479)], (1020, 645)),
    "poolOf": ([(840, 770), (800, 770), (800, 870), (760, 870)], (800, 819)),
    "pickupOf": ([(530, 689), (530, 750), (660, 750), (660, 831)], (588, 750)),
    "behaviorOf": ([(360, 810), (460, 810), (460, 870), (560, 870)], (460, 840)),
    "rateOf": ([(430, 1001), (430, 950), (660, 950), (660, 909)], (540, 950)),
}


def local(value):
    return str(value).rsplit("/", 1)[-1].rsplit("#", 1)[-1]


def rounded(points):
    path = f"M {points[0][0]} {points[0][1]}"
    for a, b, c in zip(points, points[1:], points[2:]):
        before = abs(a[0] - b[0]) + abs(a[1] - b[1])
        after = abs(c[0] - b[0]) + abs(c[1] - b[1])
        radius = min(8, before / 2, after / 2)
        p = (
            b[0] + (a[0] - b[0]) * radius / before,
            b[1] + (a[1] - b[1]) * radius / before,
        )
        q = (
            b[0] + (c[0] - b[0]) * radius / after,
            b[1] + (c[1] - b[1]) * radius / after,
        )
        path += f" L {p[0]} {p[1]} Q {b[0]} {b[1]} {q[0]} {q[1]}"
    return path + f" L {points[-1][0]} {points[-1][1]}"


def render(source, output):
    graph = Graph().parse(source, format="turtle")
    supported = {OWL.Class, OWL.ObjectProperty, OWL.DatatypeProperty, OWL.Ontology}
    subjects = set(graph.subjects())
    if any(set(graph.objects(subject, RDF.type)) - supported for subject in subjects):
        raise ValueError("Only ontology schema declarations may be rendered")
    if any(
        not set(graph.objects(subject, RDF.type)) & supported for subject in subjects
    ):
        raise ValueError("Instances or untyped data are not permitted")
    classes = sorted(graph.subjects(RDF.type, OWL.Class))
    properties = sorted(graph.subjects(RDF.type, OWL.ObjectProperty))
    if {local(c) for c in classes} != set(POSITIONS) or {
        local(p) for p in properties
    } != set(ROUTES):
        raise ValueError("Review the layout when the ontology schema changes")
    svg = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1400 1120" role="img" aria-labelledby="title desc">',
        '<title id="title">Puroseka ontology relationships</title>',
        '<desc id="desc">Sixteen ontology classes and fifteen directed object properties, extracted from the supplied puroseka-1.1.0 Turtle schema. Cards and their relationships are highlighted. Entity labels are a separate lookup.</desc>',
        '<defs><pattern id="dots" width="20" height="20" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r="0.6" fill="#cbd5e1"/></pattern>',
        '<marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto-start-reverse"><path d="M0 1 L7 4 L0 7" fill="none" stroke="context-stroke"/></marker></defs>',
        '<rect width="1400" height="1120" fill="white"/><rect width="1400" height="1120" fill="url(#dots)"/>',
        '<g font-family="Arial, sans-serif">',
    ]
    dot = [
        "digraph ontology {",
        'graph [layout=neato, splines=ortho, bgcolor="white", overlap=false];',
        'node [shape=box, style="rounded,filled", fillcolor="white", color="#cbd5e1", fontname="Arial"];',
    ]
    labels = []
    for prop in properties:
        name = local(prop)
        domain, range_ = (
            local(graph.value(prop, RDFS.domain)),
            local(graph.value(prop, RDFS.range)),
        )
        points, label = ROUTES[name]
        for point, concept in ((points[0], domain), (points[-1], range_)):
            x, y = POSITIONS[concept]
            dx, dy = abs(point[0] - x), abs(point[1] - y)
            if not (dx == 100 and dy <= 39 or dy == 39 and dx <= 100):
                raise ValueError("Review relationship anchors for " + name)
        color = "#0972d3" if "Card" in (domain, range_) else "#667085"
        svg.append(
            f'<path d="{rounded(points)}" fill="none" stroke="{color}" stroke-width="1.7" marker-end="url(#arrow)"><title>{escape(str(graph.value(prop, RDFS.comment) or name))}</title></path>'
        )
        labels.append((name, label))
        dot.append(f'{domain} -> {range_} [label={json.dumps(name)},color="{color}"];')
    for name, (x, y) in labels:
        width = len(name) * 6.5 + 16
        svg.append(
            f'<rect x="{x - width / 2}" y="{y - 11}" width="{width}" height="22" rx="3" fill="white"/><text x="{x}" y="{y + 4}" text-anchor="middle" font-size="12" fill="#344054">{escape(name)}</text>'
        )
    for cls in classes:
        name = local(cls)
        x, y = POSITIONS[name]
        title = str(graph.value(cls, RDFS.label) or name)
        prefixes = set()
        for prop in graph.subjects(RDFS.domain, cls):
            if (prop, RDF.type, OWL.DatatypeProperty) in graph:
                field = str(graph.value(prop, RDFS.label) or "")
                if field and local(prop).endswith("_" + field):
                    prefixes.add(local(prop)[: -len(field) - 1])
        if len(prefixes) != 1:
            raise ValueError("Expected one physical table for class " + name)
        table = prefixes.pop()
        selected = name == "Card"
        border = "#0972d3" if selected else "#cbd5e1"
        svg.append(
            f'<rect x="{x - 100}" y="{y - 39}" width="200" height="78" rx="12" fill="white" stroke="{border}" stroke-width="{2 if selected else 1}"/><text x="{x - 84}" y="{y - 5}" font-size="14" font-weight="600" fill="#101828">{escape(title)}</text><text x="{x - 84}" y="{y + 17}" font-size="12" fill="#475467">{escape(table)}</text>'
        )
        dot.append(
            f'{name} [label={json.dumps(title + chr(10) + table)},pos="{x},{1120 - y}!",color="{border}"];'
        )
    svg.append("</g></svg>")
    dot.append("}")
    output.mkdir(exist_ok=True, parents=True)
    (output / "ontology-relations.svg").write_text("\n".join(svg) + "\n")
    (output / "ontology-relations.dot").write_text("\n".join(dot) + "\n")
    print(
        json.dumps(
            {
                "classes": len(classes),
                "relationships": len(properties),
                "datatype_properties": len(
                    list(graph.subjects(RDF.type, OWL.DatatypeProperty))
                ),
                "triples": len(graph),
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "docs")
    args = parser.parse_args()
    render(args.source, args.output)
