import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";

// Sortable tables: the sorted column of each table lives in the URL (?sort=name,
// ?sort=-queued for descending), so «Назад» from a client's card and a shared link keep
// it. Dates, money (Decimal strings) and counts compare as values, names in the order of
// the interface language's alphabet. A row without a value stays at the end either way.

export type SortDir = "asc" | "desc";
export type Sort<K extends string> = { key: K; dir: SortDir };
/** How a column compares: text by the alphabet, the rest as values. */
export type SortKind = "text" | "number" | "money" | "date";
export type SortValue = string | number | null | undefined;

export type SortColumn<T> = {
  kind: SortKind;
  value: (row: T) => SortValue;
  /** The direction of the first click: A → Я and the earliest date, the largest count. */
  first?: SortDir;
};
export type SortColumns<T, K extends string> = Record<K, SortColumn<T>>;

export function parseSort<K extends string>(value: string | null, keys: readonly K[]): Sort<K> | null {
  if (!value) return null;
  const desc = value.startsWith("-");
  const key = keys.find((k) => k === (desc ? value.slice(1) : value));
  return key ? { key, dir: desc ? "desc" : "asc" } : null;
}

export function formatSort<K extends string>(sort: Sort<K> | null): string {
  return sort ? `${sort.dir === "desc" ? "-" : ""}${sort.key}` : "";
}

/** The next state of a click on a column: its first direction, the other one, then back
 * to the table's own order. */
export function nextSort<K extends string>(
  current: Sort<K> | null,
  fallback: Sort<K> | null,
  key: K,
  first: SortDir,
): Sort<K> | null {
  const shown = current ?? fallback;
  if (!shown || shown.key !== key) return { key, dir: first };
  if (shown.dir === first) return { key, dir: first === "asc" ? "desc" : "asc" };
  // Reversed: back to the table's own order, or to the first direction when the table's
  // own order is this very column.
  return fallback?.key === key ? { key, dir: first } : null;
}

/** The sort of one table, read from and written to the URL parameter `param`. */
export function useSortParam<K extends string>(
  param: string,
  keys: readonly K[],
  fallback: Sort<NoInfer<K>> | null = null,
) {
  const [params, setParams] = useSearchParams();
  const chosen = parseSort(params.get(param), keys);
  const sort = chosen ?? fallback;
  const set = (next: Sort<K> | null) =>
    setParams(
      (current) => {
        const updated = new URLSearchParams(current);
        const value = formatSort(next);
        // The table's own order needs no parameter.
        if (value && value !== formatSort(fallback)) updated.set(param, value);
        else updated.delete(param);
        return updated;
      },
      { replace: true },
    );
  const toggle = (key: K, first: SortDir = "asc") => set(nextSort(chosen, fallback, key, first));
  return { sort, set, toggle };
}

function blank(value: SortValue): boolean {
  return value === null || value === undefined || value === "";
}

function numeric(kind: SortKind, value: string | number): number {
  if (typeof value === "number") return value;
  // ISO dates and moments; money and counts come as decimal strings.
  return kind === "date" ? Date.parse(value) : Number(value);
}

export function compareValues(
  kind: SortKind,
  a: string | number,
  b: string | number,
  collator: Intl.Collator,
): number {
  if (kind === "text") return collator.compare(String(a), String(b));
  return numeric(kind, a) - numeric(kind, b);
}

/** The rows in the sorted order; ties keep the order they came in. */
export function sortRows<T, K extends string>(
  rows: readonly T[],
  columns: SortColumns<T, K>,
  sort: Sort<K> | null,
  collator: Intl.Collator,
): T[] {
  if (!sort) return [...rows];
  const { kind, value } = columns[sort.key];
  const sign = sort.dir === "asc" ? 1 : -1;
  return rows
    .map((row, index) => ({ row, index, key: value(row) }))
    .sort((a, b) => {
      const aBlank = blank(a.key);
      const bBlank = blank(b.key);
      if (aBlank || bBlank) return aBlank === bBlank ? a.index - b.index : aBlank ? 1 : -1;
      const order = compareValues(kind, a.key as string | number, b.key as string | number, collator);
      return order === 0 ? a.index - b.index : sign * order;
    })
    .map((item) => item.row);
}

/** A collator for the interface language: «Ё» with «Е», «Иван 2» before «Иван 10». */
export function useCollator(): Intl.Collator {
  const { i18n } = useTranslation();
  return useMemo(() => new Intl.Collator(i18n.language, { numeric: true }), [i18n.language]);
}

export type SortedTable<T, K extends string> = {
  rows: T[];
  sort: Sort<K> | null;
  /** Props of the column's SortHeader. */
  header: (key: K) => { sortKey: K; sort: Sort<K> | null; onSort: () => void };
};

/** A table sorted by the URL parameter `param`: the shown rows and the header's props. */
export function useSortedTable<T, K extends string>(
  param: string,
  rows: readonly T[],
  columns: SortColumns<T, K>,
  fallback: Sort<NoInfer<K>> | null = null,
): SortedTable<T, K> {
  const keys = Object.keys(columns) as K[];
  const { sort, toggle } = useSortParam(param, keys, fallback);
  const collator = useCollator();
  // Sorted on every render: a few hundred rows at most, and the columns' values depend on
  // lists (countries, registrars) that may arrive after the rows.
  const sorted = sortRows(rows, columns, sort, collator);
  const header = (key: K) => ({
    sortKey: key,
    sort,
    onSort: () => toggle(key, columns[key].first ?? "asc"),
  });
  return { rows: sorted, sort, header };
}
