import { firstValueFrom, Observable, of, throwError } from 'rxjs';
import { ApiError, Page } from './api-client';
import { LIST_ALL_PAGE_SIZE, listAll, PageRequest } from './list-all';

function page<T>(items: T[], pageNo: number, totalPages: number): Page<T> {
  return {
    items,
    meta: { page: pageNo, page_size: LIST_ALL_PAGE_SIZE, count: 0, total_pages: totalPages },
  };
}

describe('listAll', () => {
  it('asks for page 1 at page_size=100 and returns its items when it is the only page', async () => {
    const asked: PageRequest[] = [];
    const all = await firstValueFrom(
      listAll((req) => {
        asked.push(req);
        return of(page(['a', 'b'], 1, 1));
      }),
    );
    expect(all).toEqual(['a', 'b']);
    expect(asked).toEqual([{ page: 1, page_size: 100 }]);
  });

  it('follows meta.total_pages and concatenates every page in order', async () => {
    const pages: Record<number, Page<number>> = {
      1: page([1, 2], 1, 3),
      2: page([3, 4], 2, 3),
      3: page([5], 3, 3),
    };
    const asked: number[] = [];
    const all = await firstValueFrom(
      listAll((req) => {
        asked.push(req.page);
        return of(pages[req.page]);
      }),
    );
    expect(all).toEqual([1, 2, 3, 4, 5]);
    expect(asked).toEqual([1, 2, 3]);
  });

  it('returns an empty list for an empty first page', async () => {
    expect(await firstValueFrom(listAll(() => of(page([], 1, 1))))).toEqual([]);
  });

  it('errors with the failing page error and stops asking', async () => {
    const failure = new ApiError(500, [{ code: 'server_error', message: 'boom', field: null }]);
    const asked: number[] = [];
    const fetchPage = (req: PageRequest): Observable<Page<string>> => {
      asked.push(req.page);
      return req.page === 1 ? of(page(['a'], 1, 3)) : throwError(() => failure);
    };
    await expect(firstValueFrom(listAll(fetchPage))).rejects.toBe(failure);
    expect(asked).toEqual([1, 2]);
  });
});
