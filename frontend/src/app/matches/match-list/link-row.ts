import { CDK_ROW_TEMPLATE, CdkCellOutlet, CdkRow } from '@angular/cdk/table';
import { Component, ViewEncapsulation } from '@angular/core';

/**
 * A `mat-row` that is a real `<a>`: put `[routerLink]` on it and the whole row gets an href,
 * so click, Enter and middle-click all open the row's page. Material's own `mat-row` selector
 * only matches `mat-row` / `tr`, so this re-declares it for `a[app-link-row]` (flex `mat-table`).
 */
@Component({
  // The row must be the <a> itself (like Material's `tr[mat-row]`), so no element selector.
  // eslint-disable-next-line @angular-eslint/component-selector
  selector: 'a[app-link-row]',
  imports: [CdkCellOutlet],
  template: CDK_ROW_TEMPLATE,
  host: { class: 'mat-mdc-row mdc-data-table__row', role: 'row' },
  encapsulation: ViewEncapsulation.None,
  providers: [{ provide: CdkRow, useExisting: LinkRow }],
})
export class LinkRow extends CdkRow {}
