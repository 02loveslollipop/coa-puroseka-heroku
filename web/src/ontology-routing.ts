import type { XYPosition } from "@xyflow/react";

export type Obstacle = { x: number; y: number; width: number; height: number };

// A small orthogonal visibility grid keeps connections out of concept cards.
// Coordinates follow card boundaries, so routing scales with concepts rather
// than canvas pixels and still works after a user drags an unrelated card.
export function routeAroundCards(start: XYPosition, end: XYPosition, cards: Obstacle[]): XYPosition[] | null {
  const boxes = cards.map(card => ({ left: card.x - 16, right: card.x + card.width + 16, top: card.y - 16, bottom: card.y + card.height + 16 }));
  const xs = [...new Set([start.x, end.x, ...boxes.flatMap(box => [box.left, box.right])])].sort((a, b) => a - b);
  const ys = [...new Set([start.y, end.y, ...boxes.flatMap(box => [box.top, box.bottom])])].sort((a, b) => a - b);
  const key = (x: number, y: number) => y * xs.length + x;
  const point = (id: number) => ({ x: xs[id % xs.length], y: ys[Math.floor(id / xs.length)] });
  const blocked = (a: XYPosition, b: XYPosition) => boxes.some(box =>
    a.y === b.y
      ? a.y > box.top + 0.01 && a.y < box.bottom - 0.01 && Math.max(a.x, b.x) > box.left + 0.01 && Math.min(a.x, b.x) < box.right - 0.01
      : a.x > box.left + 0.01 && a.x < box.right - 0.01 && Math.max(a.y, b.y) > box.top + 0.01 && Math.min(a.y, b.y) < box.bottom - 0.01);
  const first = key(xs.indexOf(start.x), ys.indexOf(start.y));
  const last = key(xs.indexOf(end.x), ys.indexOf(end.y));
  const costs = new Map<number, number>([[first, 0]]);
  const previous = new Map<number, number>();
  const queue: [number, number][] = [];
  const visited = new Set<number>();
  function push(item: [number, number]) {
    queue.push(item);
    let index = queue.length - 1;
    while (index > 0) {
      const parent = Math.floor((index - 1) / 2);
      if (queue[parent][0] <= item[0]) break;
      queue[index] = queue[parent]; index = parent;
    }
    queue[index] = item;
  }
  function pop() {
    const result = queue[0];
    const tail = queue.pop()!;
    if (queue.length) {
      let index = 0;
      while (index * 2 + 1 < queue.length) {
        let child = index * 2 + 1;
        if (child + 1 < queue.length && queue[child + 1][0] < queue[child][0]) child++;
        if (queue[child][0] >= tail[0]) break;
        queue[index] = queue[child]; index = child;
      }
      queue[index] = tail;
    }
    return result[1];
  }
  push([0, first]);
  while (queue.length) {
    const current = pop();
    if (visited.has(current)) continue;
    if (current === last) {
      const path: XYPosition[] = [];
      let id: number | undefined = last;
      while (id !== undefined) { path.push(point(id)); id = previous.get(id); }
      path.reverse();
      return path.filter((p, index) => !index || index === path.length - 1 ||
        !((path[index - 1].x === p.x && path[index + 1].x === p.x) || (path[index - 1].y === p.y && path[index + 1].y === p.y)));
    }
    visited.add(current);
    const x = current % xs.length;
    const y = Math.floor(current / xs.length);
    const from = point(current);
    const prior = previous.has(current) ? point(previous.get(current)!) : null;
    const neighbors = [[x - 1, y], [x + 1, y], [x, y - 1], [x, y + 1]];
    for (const [nx, ny] of neighbors) {
      if (nx < 0 || nx >= xs.length || ny < 0 || ny >= ys.length) continue;
      const next = key(nx, ny);
      const to = point(next);
      if (visited.has(next) || blocked(from, to)) continue;
      const turn = prior && (prior.x === from.x) !== (from.x === to.x) ? 24 : 0;
      const cost = costs.get(current)! + Math.abs(from.x - to.x) + Math.abs(from.y - to.y) + turn;
      if (cost >= (costs.get(next) ?? Infinity)) continue;
      costs.set(next, cost); previous.set(next, current);
      push([cost + Math.abs(to.x - end.x) + Math.abs(to.y - end.y), next]);
    }
  }
  return null;
}

export function roundedPath(points: XYPosition[]) {
  let path = `M ${points[0].x} ${points[0].y}`;
  for (let index = 1; index < points.length - 1; index++) {
    const a = points[index - 1], b = points[index], c = points[index + 1];
    const before = Math.hypot(a.x - b.x, a.y - b.y), after = Math.hypot(c.x - b.x, c.y - b.y);
    if (!before || !after) continue;
    const radius = Math.min(8, before / 2, after / 2);
    path += ` L ${b.x + (a.x - b.x) * radius / before} ${b.y + (a.y - b.y) * radius / before}`;
    path += ` Q ${b.x} ${b.y} ${b.x + (c.x - b.x) * radius / after} ${b.y + (c.y - b.y) * radius / after}`;
  }
  return path + ` L ${points[points.length - 1].x} ${points[points.length - 1].y}`;
}
