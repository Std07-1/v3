// Параметри адреси сторінки, які сайт прибирає після прочитання: мітка джерела зі знімка (ADR-0105 rev 11.10) і
// мова з посилання (ADR-0116 §5.2). Окремий модуль — одна функція для обох, а не дві копії з однією пасткою кодування.

/** Адреса без параметра `param`. `/` у значеннях лишається читабельним (символ «XAU/USD» у посиланні на графік):
 *  URLSearchParams перекодував би його в %2F. */
export function withoutQueryParam(href: string, param: string): string {
  const url = new URL(href);
  const params = new URLSearchParams(url.search);
  params.delete(param);
  const query = params.toString().replace(/%2F/gi, '/');
  url.search = query ? `?${query}` : '';
  return url.toString();
}
