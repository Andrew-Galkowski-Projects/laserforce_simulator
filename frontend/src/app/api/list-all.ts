import { EMPTY, Observable, expand, reduce } from 'rxjs';
import { Page } from './api-client';

/** The API's page-size cap; `listAll` follows further pages past it. */
export const LIST_ALL_PAGE_SIZE = 100;

/** The paging params `listAll` hands its `fetchPage`; spread them into the list call. */
export interface PageRequest {
  page: number;
  page_size: number;
}

/**
 * Every item of a paginated list: page 1 at `page_size=100`, then each further page
 * `meta.total_pages` names, concatenated in order. A failing page fails the whole list.
 */
export function listAll<T>(fetchPage: (req: PageRequest) => Observable<Page<T>>): Observable<T[]> {
  const fetch = (page: number) => fetchPage({ page, page_size: LIST_ALL_PAGE_SIZE });
  return fetch(1).pipe(
    expand(({ meta }) => (meta.page < meta.total_pages ? fetch(meta.page + 1) : EMPTY)),
    reduce((all, page) => [...all, ...page.items], [] as T[]),
  );
}
