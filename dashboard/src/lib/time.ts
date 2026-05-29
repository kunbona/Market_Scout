/**
 * 将 pub_time 格式化为 "YYYY-MM-DD HH:mm"
 * 若时分均为 00:00（RSS 来源无精确时间），只显示日期 "YYYY-MM-DD"
 */
export function relativeTime(pubTime: string): string {
  if (!pubTime) return '';
  const s = pubTime.replace('T', ' ').trim();
  const datePart = s.slice(0, 10);   // YYYY-MM-DD
  const timePart = s.slice(11, 16);  // HH:mm
  if (!timePart || timePart === '00:00') return datePart;
  return `${datePart} ${timePart}`;
}
