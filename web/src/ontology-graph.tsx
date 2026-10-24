import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Box, Button, SpaceBetween, Toggle } from "@cloudscape-design/components";
import * as tokens from "@cloudscape-design/design-tokens";
import { forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY } from "d3-force";
import type { SimulationNodeDatum } from "d3-force";
import { Background, BaseEdge, Controls, getBezierPath, Handle, MarkerType, Position, ReactFlow, useNodesState } from "@xyflow/react";
import type { Edge, EdgeProps, Node, NodeProps, ReactFlowInstance, XYPosition } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { Catalog } from "./api";
import { roundedPath, routeAroundCards } from "./ontology-routing";

const NODE_WIDTH = 220;
const NODE_HEIGHT = 104;
const sides = [Position.Top, Position.Right, Position.Bottom, Position.Left];
type ConceptNode = Node<{ title: string; table: string }, "concept">;
type LayoutPoint = SimulationNodeDatum & { id: string };
type RelationshipEdge = Edge<{ points: XYPosition[] | null; labelPoint: XYPosition }, "routed">;

function RelationshipLine(props: EdgeProps<RelationshipEdge>) {
  const { data, sourceX, sourceY, targetX, targetY } = props;
  const [fallback, fallbackX, fallbackY] = getBezierPath(props);
  const path = data?.points ? roundedPath([{ x: sourceX, y: sourceY }, ...data.points.slice(1, -1), { x: targetX, y: targetY }]) : fallback;
  return <BaseEdge id={props.id} path={path} markerEnd={props.markerEnd} style={props.style}
    label={props.label} labelX={data?.points ? data.labelPoint.x : fallbackX} labelY={data?.points ? data.labelPoint.y : fallbackY}
    labelStyle={props.labelStyle} labelShowBg labelBgStyle={props.labelBgStyle} labelBgPadding={[6, 4]} labelBgBorderRadius={4} />;
}
const edgeTypes = { routed: RelationshipLine };

function anchor(node: ConceptNode, side: Position, offset = 0): XYPosition {
  return {
    x: node.position.x + (side === Position.Left ? -offset : side === Position.Right ? NODE_WIDTH + offset : NODE_WIDTH / 2),
    y: node.position.y + (side === Position.Top ? -offset : side === Position.Bottom ? NODE_HEIGHT + offset : NODE_HEIGHT / 2),
  };
}

function Concept({ data }: NodeProps<ConceptNode>) {
  return <>
    {sides.map(position => <Handle key={`source-${position}`} id={`source-${position}`} type="source" position={position} isConnectable={false} style={{ opacity: 0 }} />)}
    {sides.map(position => <Handle key={`target-${position}`} id={`target-${position}`} type="target" position={position} isConnectable={false} style={{ opacity: 0 }} />)}
    <div className="ontology-concept-label"><strong>{data.title}</strong><span>{data.table}</span></div>
  </>;
}
const nodeTypes = { concept: Concept };

function arrange(catalog: Catalog): ConceptNode[] {
  const points: LayoutPoint[] = Object.keys(catalog.tables).sort().map(id => ({ id }));
  const links = catalog.relationships.map(relation => ({ source: relation.source, target: relation.target }));
  // Compute a deterministic, compact overview once. The simulation is stopped
  // before rendering; it never fights a user's drag or runs in the background.
  forceSimulation(points)
    .force("links", forceLink<LayoutPoint, { source: string; target: string }>(links).id(point => point.id).distance(350).strength(0.3))
    .force("charge", forceManyBody().strength(-900))
    .force("collisions", forceCollide(165).strength(1).iterations(4))
    .force("x", forceX().strength(0.025))
    .force("y", forceY().strength(0.025))
    .stop().tick(500);
  const nodes: ConceptNode[] = points.map(point => ({
    id: point.id, type: "concept", data: { title: catalog.tables[point.id].label, table: point.id },
    position: { x: point.x! - NODE_WIDTH / 2, y: point.y! * 0.52 - NODE_HEIGHT / 2 },
    draggable: true, connectable: false, deletable: false,
    ariaLabel: `${catalog.tables[point.id].label} (${point.id})`,
    style: {
      width: NODE_WIDTH, height: NODE_HEIGHT, boxSizing: "border-box", padding: 16,
      color: tokens.colorTextBodyDefault, background: tokens.colorBackgroundContainerContent,
      border: `1px solid ${tokens.colorBorderDividerDefault}`, borderRadius: tokens.borderRadiusContainer,
    },
  }));
  const linked = new Set(catalog.relationships.flatMap(relation => [relation.source, relation.target]));
  const connected = nodes.filter(node => linked.has(node.id));
  const isolated = nodes.filter(node => !linked.has(node.id));
  if (connected.length && isolated.length) {
    const left = Math.min(...connected.map(node => node.position.x));
    const right = Math.max(...connected.map(node => node.position.x + NODE_WIDTH));
    const bottom = Math.max(...connected.map(node => node.position.y + NODE_HEIGHT)) + 48;
    const columns = Math.min(isolated.length, Math.max(1, Math.floor((right - left) / (NODE_WIDTH + 48))));
    isolated.forEach((node, index) => {
      node.position = { x: (left + right - (columns * (NODE_WIDTH + 48) - 48)) / 2 + (index % columns) * (NODE_WIDTH + 48), y: bottom + Math.floor(index / columns) * (NODE_HEIGHT + 48) };
    });
  }
  return nodes;
}

export function OntologyGraph({ catalog, selected, onSelect }: { catalog: Catalog; selected: string; onSelect: (id: string) => void }) {
  const initial = useMemo(() => arrange(catalog), [catalog]);
  const [nodes, setNodes, onNodesChange] = useNodesState(initial);
  const [allLabels, setAllLabels] = useState(false);
  const flow = useRef<ReactFlowInstance<ConceptNode, RelationshipEdge> | null>(null);
  useEffect(() => setNodes(initial), [initial, setNodes]);
  const displayedNodes = useMemo(() => nodes.map(node => ({ ...node, style: {
    ...node.style,
    borderColor: node.id === selected ? tokens.colorBorderItemFocused : tokens.colorBorderDividerDefault,
    boxShadow: node.id === selected ? `0 0 0 1px ${tokens.colorBorderItemFocused}` : undefined,
  } })), [nodes, selected]);
  const edges = useMemo(() => {
    const byId = new Map(nodes.map(node => [node.id, node]));
    const cards = nodes.map(node => ({ ...node.position, width: NODE_WIDTH, height: NODE_HEIGHT }));
    return catalog.relationships.map((relation, index): RelationshipEdge => {
      const source = byId.get(relation.source)!;
      const target = byId.get(relation.target)!;
      const dx = target.position.x - source.position.x;
      const dy = target.position.y - source.position.y;
      const horizontal = Math.abs(dx) > Math.abs(dy) * 1.4;
      const sourceSide = horizontal ? (dx > 0 ? Position.Right : Position.Left) : (dy > 0 ? Position.Bottom : Position.Top);
      const targetSide = horizontal ? (dx > 0 ? Position.Left : Position.Right) : (dy > 0 ? Position.Top : Position.Bottom);
      const related = relation.source === selected || relation.target === selected;
      const color = related ? tokens.colorBorderItemFocused : relation.status === "validated" ? tokens.colorTextBodySecondary : tokens.colorTextStatusWarning;
      const route = routeAroundCards(anchor(source, sourceSide, 32), anchor(target, targetSide, 32), cards);
      const points = route ? [anchor(source, sourceSide), ...route, anchor(target, targetSide)] : null;
      let labelPoint = { x: (source.position.x + target.position.x + NODE_WIDTH) / 2, y: (source.position.y + target.position.y + NODE_HEIGHT) / 2 };
      let longest = 0;
      for (let i = 1; points && i < points.length; i++) {
        const a = points[i - 1], b = points[i];
        const length = Math.hypot(b.x - a.x, b.y - a.y) * (a.y === b.y ? 2 : 1);
        if (length > longest) { longest = length; labelPoint = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }; }
      }
      return {
        id: String(index), source: relation.source, target: relation.target, type: "routed", data: { points, labelPoint },
        sourceHandle: `source-${sourceSide}`, targetHandle: `target-${targetSide}`,
        label: allLabels || related ? relation.name : undefined,
        deletable: false, reconnectable: false, selectable: false,
        markerEnd: { type: MarkerType.ArrowClosed, color },
        style: { stroke: color, strokeWidth: related ? 2 : 1.25 },
        labelStyle: { fontSize: 14, fill: tokens.colorTextBodyDefault },
        labelBgStyle: { fill: tokens.colorBackgroundContainerContent },
        labelBgPadding: [6, 4], labelBgBorderRadius: 4,
      };
    });
  }, [catalog, nodes, selected, allLabels]);
  const fit = useCallback(() => void flow.current?.fitView({ padding: 0.12, duration: 200 }), []);
  function reset() {
    setNodes(initial.map(node => ({ ...node, position: { ...node.position } })));
    requestAnimationFrame(fit);
  }
  return <SpaceBetween size="m">
    <SpaceBetween direction="horizontal" size="m">
      <Button onClick={reset} iconName="refresh">Reset layout</Button>
      <Button onClick={() => void flow.current?.fitView({ nodes: [{ id: selected }], padding: 1, maxZoom: 1, duration: 200 })}>Focus selected</Button>
      <Toggle checked={allLabels} onChange={({ detail }) => setAllLabels(detail.checked)}>Show all relationship labels</Toggle>
    </SpaceBetween>
    <div className="ontology-graph">
      <ReactFlow<ConceptNode, RelationshipEdge> nodes={displayedNodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes}
        onInit={instance => { flow.current = instance; }}
        onNodesChange={changes => onNodesChange(changes.filter(change => change.type !== "remove"))}
        onNodeClick={(_, node) => onSelect(node.id)} onNodeDragStart={(_, node) => onSelect(node.id)}
        nodesDraggable nodesConnectable={false} edgesReconnectable={false}
        deleteKeyCode={null} onBeforeDelete={async () => false}
        fitView fitViewOptions={{ padding: 0.12 }} minZoom={0.1} maxZoom={2}
        aria-label="Ontology entity relationships">
        <Background /><Controls showInteractive={false} />
      </ReactFlow>
    </div>
    <Box color="text-body-secondary" fontSize="body-s">Drag concepts to arrange the view. Select a concept to highlight its relationships. Layout changes affect this view only.</Box>
  </SpaceBetween>;
}
