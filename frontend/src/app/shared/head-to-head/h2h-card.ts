import { Component, computed, inject, input } from '@angular/core';
import { AuthService } from '../../auth/auth.service';

/** One record card: a muted label, the value (projected) and an optional muted note. */
@Component({
  selector: 'app-h2h-card',
  template: `
    <div class="h2h-card">
      <div class="h2h-card__label">{{ label() }}</div>
      <div class="h2h-card__value"><ng-content /></div>
      @if (note(); as n) {
        <div class="h2h-card__note">{{ n }}</div>
      }
    </div>
  `,
  styles: `
    :host {
      display: block;
    }

    .h2h-card {
      height: 100%;
      box-sizing: border-box;
      padding: 0.75rem 1rem;
      border: 1px solid var(--lf-border);
      border-radius: var(--lf-radius);
      background: var(--lf-surface);
    }

    .h2h-card__label,
    .h2h-card__note {
      color: var(--lf-text-muted);
      font-size: 0.85rem;
    }

    .h2h-card__value {
      margin: 0.25rem 0;
      font-family: var(--lf-font-numeric);
      font-size: 1.4rem;
      font-weight: 700;
      overflow-wrap: anywhere;
    }
  `,
})
export class H2HCard {
  readonly label = input.required<string>();
  readonly note = input<string | null>(null);
}

/** A Player's name with the shared You badge when it is the Account's Self-insert (INV-6). */
@Component({
  selector: 'app-h2h-player-name',
  template: `{{ name() }}
    @if (isSelf()) {
      &ngsp;<span class="badge badge--you">You</span>
    }`,
  styleUrl: './h2h-player-name.scss',
})
export class H2HPlayerName {
  private readonly auth = inject(AuthService);

  readonly playerId = input.required<number>();
  readonly name = input.required<string>();

  protected readonly isSelf = computed(
    () => this.auth.currentUser()?.self_insert?.id === this.playerId(),
  );
}
