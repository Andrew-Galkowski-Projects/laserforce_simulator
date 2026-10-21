import { NgComponentOutlet } from '@angular/common';
import { Component, ViewEncapsulation, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { SAMPLE_SCREENS, SampleScreen } from './fixture/sample-fixture';
import { LAYOUTS, LAYOUT_KEYS, LAYOUT_LABELS, LayoutKey } from './layouts';
import { PALETTE_KEYS, PALETTE_LABELS, PaletteKey } from './palette-contract';

function pick<T extends string>(value: string | null, allowed: readonly T[], fallback: T): T {
  return allowed.includes(value as T) ? (value as T) : fallback;
}

/**
 * ANG-04b prototype route `/prototype/style-samples?layout=a..d&palette=a..d&screen=...`.
 * Puts `.lf-palette-x` on the root, renders `LayoutX` with the current screen, and floats the
 * layout / palette switcher. Encapsulation is off so the palette classes and the `--side`
 * convention reach every layout component (and Material) underneath.
 */
@Component({
  selector: 'app-style-samples',
  imports: [NgComponentOutlet, RouterLink],
  templateUrl: './style-samples.html',
  styleUrl: './style-samples.scss',
  encapsulation: ViewEncapsulation.None,
})
export class StyleSamples {
  private readonly params = toSignal(inject(ActivatedRoute).queryParamMap, {
    requireSync: true,
  });

  protected readonly layoutKeys = LAYOUT_KEYS;
  protected readonly paletteKeys = PALETTE_KEYS;
  protected readonly layoutLabels = LAYOUT_LABELS;
  protected readonly paletteLabels = PALETTE_LABELS;

  protected readonly layoutKey = computed<LayoutKey>(() =>
    pick(this.params().get('layout'), LAYOUT_KEYS, 'a'),
  );
  protected readonly paletteKey = computed<PaletteKey>(() =>
    pick(this.params().get('palette'), PALETTE_KEYS, 'a'),
  );
  protected readonly screen = computed<SampleScreen>(() =>
    pick(this.params().get('screen'), SAMPLE_SCREENS, 'round'),
  );
  protected readonly layout = computed(() => LAYOUTS[this.layoutKey()]);
  protected readonly layoutInputs = computed(() => ({ screen: this.screen() }));
}
