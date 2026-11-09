import {
  Component,
  ElementRef,
  afterNextRender,
  computed,
  inject,
  input,
  signal,
} from '@angular/core';
import { Role } from '../../api/players.models';
import { ROLE_ICON_URLS, roleIconLoads, tintedRoleIcon } from './role-icon';

/** Each role's name: the icon's `alt` and `title`. */
const ROLE_NAMES: Readonly<Record<Role, string>> = {
  commander: 'Commander',
  heavy: 'Heavy',
  scout: 'Scout',
  ammo: 'Ammo',
  medic: 'Medic',
};

/** A tinted icon's PNG data URL, encoded once per canvas (a feed can show hundreds of icons). */
const dataUrls = new WeakMap<HTMLCanvasElement, string>();

function dataUrl(canvas: HTMLCanvasElement): string {
  let url = dataUrls.get(canvas);
  if (url === undefined) {
    url = canvas.toDataURL();
    dataUrls.set(canvas, url);
  }
  return url;
}

/**
 * A player's SM5 role icon at text size (RV-06): an `<img>` 1.25em square whose `alt` and
 * `title` are the role's name. It is tinted to the computed `--side` of its host (set by an
 * ancestor's `data-lf-side`). With no canvas (jsdom), or before the PNG loads, it shows the
 * untinted icon; an unknown role shows its name as text and no image.
 */
@Component({
  selector: 'app-role-icon',
  template: `@if (src(); as url) {
      <img class="role-icon" [src]="url" [alt]="name()" [title]="name()" />
    } @else {
      {{ name() }}
    }`,
  styles: `
    :host {
      display: inline-flex;
      align-items: center;
    }

    .role-icon {
      width: 1.25em;
      height: 1.25em;
    }
  `,
})
export class RoleIcon {
  /** A `Role`, matched case-insensitively. */
  readonly role = input.required<string>();

  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  /** The host's computed `--side`, read once it is in the DOM ('' outside a Side). */
  private readonly side = signal('');

  private readonly known = computed<Role | null>(() => {
    const role = this.role().toLowerCase();
    return role in ROLE_ICON_URLS ? (role as Role) : null;
  });

  protected readonly name = computed(() => {
    const role = this.known();
    return role ? ROLE_NAMES[role] : this.role();
  });

  protected readonly src = computed<string | null>(() => {
    const role = this.known();
    if (!role) return null;
    roleIconLoads();
    const side = this.side();
    const tinted = side ? tintedRoleIcon(role, side) : null;
    return tinted ? dataUrl(tinted) : ROLE_ICON_URLS[role];
  });

  constructor() {
    afterNextRender(() => {
      const css = getComputedStyle(this.host.nativeElement);
      this.side.set(css.getPropertyValue('--side').trim());
    });
  }
}
