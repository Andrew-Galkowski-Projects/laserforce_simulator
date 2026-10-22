import { DatePipe } from '@angular/common';
import { Component, input } from '@angular/core';
import { MatAnchor } from '@angular/material/button';
import { RouterLink } from '@angular/router';
import { Result } from './result';
import { ResultChip } from './result-chip';

/**
 * The detail stubs' summary: Red vs Blue in their side colours, the score, the result chip and
 * the date, with "Back to Matches". ANG-06 item 4's detail pages replace it.
 */
@Component({
  selector: 'app-result-summary',
  imports: [DatePipe, MatAnchor, ResultChip, RouterLink],
  templateUrl: './result-summary.html',
  styleUrl: './result-summary.scss',
})
export class ResultSummary {
  readonly redName = input.required<string>();
  readonly blueName = input.required<string>();
  readonly redScore = input.required<number>();
  readonly blueScore = input.required<number>();
  readonly result = input.required<Result>();
  /** ISO 8601 datetime. */
  readonly datePlayed = input.required<string>();
}
