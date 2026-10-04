export type Knot = [number, number];
export function validKnots(raw: unknown): Knot[] | null {
  if (!Array.isArray(raw) || raw.length < 2) return null;
  const k = raw as Knot[];
  if (k.some((p,i)=>!Array.isArray(p)||p.length!==2||!p.every(Number.isFinite)||p[1]<0||p[1]>1||(i>0&&(p[0]<=k[i-1][0]||p[1]<k[i-1][1])))) return null;
  return k;
}
// Interpolate only inside saved support; never fabricate open-tail values.
export function quantile(k: Knot[], p: number): number | null {
  if(p<k[0][1]||p>k[k.length-1][1]) return null;
  if(p===k[0][1])return k[0][0];
  for(let i=1;i<k.length;i++){const [x,y]=k[i], [a,b]=k[i-1];if(y>=p)return y===b?a:a+(x-a)*(p-b)/(y-b);}
  return null;
}
export function formatValue(value: number | null, type: string): string {
  if(value===null)return 'Outside saved range';
  if(type==='date')return new Date(value*1000).toLocaleDateString('en-GB',{month:'short',year:'numeric',timeZone:'UTC'});
  return value.toLocaleString('en-US',{maximumFractionDigits:Math.abs(value)>=100?0:3});
}
