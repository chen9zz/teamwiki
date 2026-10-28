import { useInfiniteQuery } from '@tanstack/react-query';
import { api } from './api';
import type { PageBatch } from './types';

export const PAGE_BATCH_SIZE = 50;

export function usePageSummaries(wid: string, enabled = true) {
  return useInfiniteQuery({
    queryKey: ['pages', wid, 'summaries'],
    initialPageParam: 0,
    queryFn: ({ pageParam, signal }) =>
      api<PageBatch>(
        `/workspaces/${wid}/page-summaries?limit=${PAGE_BATCH_SIZE}&offset=${pageParam}`,
        { signal },
      ),
    getNextPageParam: (last) => (last.has_more ? last.offset + last.limit : undefined),
    enabled,
  });
}

export function usePageTree(wid: string, parentId?: string) {
  return useInfiniteQuery({
    queryKey: ['pages', wid, 'tree', parentId || null],
    initialPageParam: 0,
    queryFn: ({ pageParam, signal }) =>
      api<PageBatch>(
        `/workspaces/${wid}/page-tree?limit=${PAGE_BATCH_SIZE}&offset=${pageParam}${parentId ? `&parent_id=${encodeURIComponent(parentId)}` : ''}`,
        { signal },
      ),
    getNextPageParam: (last) => (last.has_more ? last.offset + last.limit : undefined),
  });
}
