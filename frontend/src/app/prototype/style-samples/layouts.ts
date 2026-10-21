import { Type } from '@angular/core';
import { LayoutA } from './layout-a/layout-a';
import { LayoutB } from './layout-b/layout-b';
import { LayoutC } from './layout-c/layout-c';
import { LayoutD } from './layout-d/layout-d';

/**
 * Layout registry. Each layout folder `layout-{a..d}/` is owned by one agent, who replaces its
 * contents wholesale but keeps the entry point: `layout-x/layout-x.ts` exporting `class LayoutX`
 * with a `screen = input<SampleScreen>('round')` signal input. Nobody edits this file.
 */
export const LAYOUT_KEYS = ['a', 'b', 'c', 'd'] as const;
export type LayoutKey = (typeof LAYOUT_KEYS)[number];

export const LAYOUTS: Record<LayoutKey, Type<unknown>> = {
  a: LayoutA,
  b: LayoutB,
  c: LayoutC,
  d: LayoutD,
};

export const LAYOUT_LABELS: Record<LayoutKey, string> = {
  a: 'A Dense dashboard',
  b: 'B LFstats-like',
  c: 'C Clean Material',
  d: 'D Esports broadcast',
};
