import { DecimalPipe } from '@angular/common';
import { AfterViewInit, Component, effect, input, viewChild } from '@angular/core';
import { MatSort, MatSortModule } from '@angular/material/sort';
import { MatTableDataSource, MatTableModule } from '@angular/material/table';
import { SamplePlayerState } from '../fixture/sample-fixture';

/** Layout C per-Side stat table: `mat-table` + `matSort`, scrolling in its own container. */
@Component({
  selector: 'app-lc-side-table',
  imports: [DecimalPipe, MatTableModule, MatSortModule],
  template: `
    <div class="lc-table-wrap">
      <table mat-table [dataSource]="data" matSort class="lc-table">
        <ng-container matColumnDef="playerName">
          <th mat-header-cell *matHeaderCellDef mat-sort-header>Player</th>
          <td mat-cell *matCellDef="let p" class="lc-player">{{ p.playerName }}</td>
        </ng-container>
        <ng-container matColumnDef="role">
          <th mat-header-cell *matHeaderCellDef mat-sort-header>Role</th>
          <td mat-cell *matCellDef="let p">{{ p.role }}</td>
        </ng-container>
        <ng-container matColumnDef="points">
          <th mat-header-cell *matHeaderCellDef mat-sort-header arrowPosition="before">Score</th>
          <td mat-cell *matCellDef="let p" class="lc-num">{{ p.points | number }}</td>
        </ng-container>
        <ng-container matColumnDef="mvp">
          <th mat-header-cell *matHeaderCellDef mat-sort-header arrowPosition="before">MVP</th>
          <td mat-cell *matCellDef="let p" class="lc-num">
            <a href="" class="lc-mvp" (click)="$event.preventDefault()">{{
              p.mvp | number: '1.1-1'
            }}</a>
          </td>
        </ng-container>
        <ng-container matColumnDef="hitDiff">
          <th mat-header-cell *matHeaderCellDef mat-sort-header arrowPosition="before">Hit Diff</th>
          <td mat-cell *matCellDef="let p" class="lc-num">{{ p.hitDiff.toFixed(2) }}</td>
        </ng-container>
        <ng-container matColumnDef="accuracy">
          <th mat-header-cell *matHeaderCellDef mat-sort-header arrowPosition="before">Acc</th>
          <td mat-cell *matCellDef="let p" class="lc-num">{{ p.accuracy.toFixed(0) }}%</td>
        </ng-container>
        <ng-container matColumnDef="medicHits">
          <th mat-header-cell *matHeaderCellDef mat-sort-header arrowPosition="before">
            Medic Hits
          </th>
          <td mat-cell *matCellDef="let p" class="lc-num">{{ p.medicHits }}</td>
        </ng-container>
        <ng-container matColumnDef="lives">
          <th mat-header-cell *matHeaderCellDef mat-sort-header arrowPosition="before">Lives</th>
          <td mat-cell *matCellDef="let p" class="lc-num">{{ p.lives }}/{{ p.startingLives }}</td>
        </ng-container>
        <ng-container matColumnDef="shotsFired">
          <th mat-header-cell *matHeaderCellDef mat-sort-header arrowPosition="before">Shots</th>
          <td mat-cell *matCellDef="let p" class="lc-num">{{ p.shotsFired }}</td>
        </ng-container>
        <ng-container matColumnDef="missilesLanded">
          <th mat-header-cell *matHeaderCellDef mat-sort-header arrowPosition="before">Missiles</th>
          <td mat-cell *matCellDef="let p" class="lc-num">{{ p.missilesLanded }}</td>
        </ng-container>
        <ng-container matColumnDef="timesMissiled">
          <th mat-header-cell *matHeaderCellDef mat-sort-header arrowPosition="before">Missiled</th>
          <td mat-cell *matCellDef="let p" class="lc-num">{{ p.timesMissiled }}</td>
        </ng-container>

        <tr mat-header-row *matHeaderRowDef="columns; sticky: true"></tr>
        <tr
          mat-row
          *matRowDef="let p; columns: columns"
          [class.lc-row--top]="p.mvp === topMvp()"
          [class.lc-row--out]="p.eliminatedAtTick !== null"
        ></tr>
      </table>
    </div>
  `,
})
export class SideTableC implements AfterViewInit {
  readonly rows = input.required<SamplePlayerState[]>();
  readonly topMvp = input<number>(0);

  private readonly sort = viewChild.required(MatSort);
  protected readonly data = new MatTableDataSource<SamplePlayerState>([]);
  protected readonly columns = [
    'playerName',
    'role',
    'points',
    'mvp',
    'hitDiff',
    'accuracy',
    'medicHits',
    'lives',
    'shotsFired',
    'missilesLanded',
    'timesMissiled',
  ];

  constructor() {
    effect(() => {
      this.data.data = this.rows();
    });
  }

  ngAfterViewInit(): void {
    this.data.sort = this.sort();
  }
}
