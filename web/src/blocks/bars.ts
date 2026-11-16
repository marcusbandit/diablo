export type Segment = { bottom: number; height: number };

/**
 * Where each bar sits, as shares of the chart height. Stacked bars pile up per label; grouped
 * ones stand side by side from the floor. `max` is the tallest column, 0 when all are empty.
 */
export function barLayout(count: number, series: number[][], stacked: boolean): { max: number; columns: Segment[][] } {
  const at = (s: number, i: number) => Math.max(0, series[s]?.[i] ?? 0);
  let max = 0;
  for (let i = 0; i < count; i++) {
    let total = 0;
    for (let s = 0; s < series.length; s++) total = stacked ? total + at(s, i) : Math.max(total, at(s, i));
    max = Math.max(max, total);
  }
  const columns: Segment[][] = [];
  for (let i = 0; i < count; i++) {
    let floor = 0;
    const col: Segment[] = [];
    for (let s = 0; s < series.length; s++) {
      const h = max ? at(s, i) / max : 0;
      col.push({ bottom: stacked ? floor : 0, height: h });
      if (stacked) floor += h;
    }
    columns.push(col);
  }
  return { max, columns };
}
