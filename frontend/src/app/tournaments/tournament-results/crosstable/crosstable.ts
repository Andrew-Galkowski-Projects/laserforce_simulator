import { NgTemplateOutlet } from '@angular/common';
import { Component, input } from '@angular/core';
import { RouterLink } from '@angular/router';
import { CrosstableRow } from '../../../api/tournaments.models';

/**
 * The Round robin N x N crosstable (`rr_crosstable`), in Standings order: each cell shows the row
 * Team's two legs against the column Team. A played leg is its total points, linked to the Match,
 * and the muted 6-point Match score; an unplayed leg is a dash; the diagonal is blank.
 */
@Component({
  selector: 'app-crosstable',
  imports: [NgTemplateOutlet, RouterLink],
  templateUrl: './crosstable.html',
  styleUrl: './crosstable.scss',
})
export class Crosstable {
  readonly rows = input.required<CrosstableRow[]>();
}
