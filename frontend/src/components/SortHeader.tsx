import type { ReactNode } from "react";
import type { Sort } from "../sorting";

type Props<K extends string> = {
  sortKey: K;
  sort: Sort<K> | null;
  onSort: () => void;
  children: ReactNode;
  className?: string;
};

/** A column heading that sorts its table: a button inside the cell, the state in
 * `aria-sort` on the cell itself (only the sorted column carries it). */
export function SortHeader<K extends string>({ sortKey, sort, onSort, children, className }: Props<K>) {
  const dir = sort?.key === sortKey ? sort.dir : null;
  return (
    <th
      className={className ? `sortable ${className}` : "sortable"}
      aria-sort={dir === "asc" ? "ascending" : dir === "desc" ? "descending" : undefined}
    >
      <button type="button" className="sort-btn" onClick={onSort}>
        <span>{children}</span>
        <span className={dir ? "sort-arrow on" : "sort-arrow"} aria-hidden="true">
          {dir === "asc" ? "↑" : dir === "desc" ? "↓" : "↕"}
        </span>
      </button>
    </th>
  );
}
