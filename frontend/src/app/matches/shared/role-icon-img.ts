import { Component, computed, input } from '@angular/core';
import { Role } from '../../api/players.models';
import { Side } from '../../api/rounds.models';
import { ROLE_ICON_URLS, iconRole, roleIconLoads, tintedRoleIcon } from './role-icon';

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

/** A Side's colour token (`--lf-side-red` / `--lf-side-blue`), as the document root computes it. */
function sideColour(side: Side): string {
  return getComputedStyle(document.documentElement).getPropertyValue(`--lf-side-${side}`).trim();
}

/**
 * A player's SM5 role icon at text size (RV-06): an `<img>` 1.25em square whose `alt` and
 * `title` are the role's name. It is tinted to its `side` input's token, resolved again whenever
 * `side` or `role` changes, so a reused component (a feed row) never keeps an old Side's tint;
 * `data-tint` carries the resolved colour. With no canvas (jsdom), or before the PNG loads, it
 * shows the untinted icon; an unknown role shows its name as text and no image.
 */
@Component({
  selector: 'app-role-icon',
  template: `@if (src(); as url) {
      <img
        class="role-icon"
        [src]="url"
        [alt]="name()"
        [title]="name()"
        [attr.data-tint]="tint()"
      />
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
  /** The player's Side: the icon takes that Side's colour. */
  readonly side = input.required<Side>();

  private readonly known = computed(() => iconRole(this.role()));

  protected readonly name = computed(() => {
    const role = this.known();
    return role ? ROLE_NAMES[role] : this.role();
  });

  /** The Side's resolved colour ('' when the token is unset); recomputed with `side` and `role`. */
  protected readonly tint = computed(() => {
    this.role();
    return sideColour(this.side());
  });

  protected readonly src = computed<string | null>(() => {
    const role = this.known();
    if (!role) return null;
    roleIconLoads();
    const colour = this.tint();
    const tinted = colour ? tintedRoleIcon(role, colour) : null;
    return tinted ? dataUrl(tinted) : ROLE_ICON_URLS[role];
  });
}
