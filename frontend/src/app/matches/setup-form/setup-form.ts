import { Component, inject } from '@angular/core';
import { ActivatedRoute } from '@angular/router';
import { SetupKind } from '../shared/setup-kind';

/**
 * `/matches/new` and `/rounds/new`: one setup form, configured by the route's `data.kind`.
 * Placeholder until ANG-06c T3 (#292) builds the form.
 */
@Component({
  selector: 'app-setup-form',
  template: `<h1>{{ kind === 'match' ? 'New Match' : 'New Single Round' }}</h1>`,
})
export class SetupForm {
  protected readonly kind = inject(ActivatedRoute).snapshot.data['kind'] as SetupKind;
}
