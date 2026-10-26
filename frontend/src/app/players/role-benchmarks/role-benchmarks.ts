import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute } from '@angular/router';
import { EMPTY, catchError, switchMap } from 'rxjs';
import { RoleBenchmarks as RoleBenchmarksBody } from '../../api/players.models';
import { PlayersService } from '../../api/players.service';
import { BenchmarkFilter, benchmarkParams } from './benchmark-filter';
import { capitalise, fixedOrDash } from './benchmark-format';

/**
 * `/players/benchmarks`: the per-role benchmark distributions (ANG-06-8, `role_benchmarks.html`),
 * one table per role, over the caller's own and Unmanaged Rounds. The filters live in the URL
 * (`?threshold=&display=`) and reload the page's data when they change.
 */
@Component({
  selector: 'app-role-benchmarks',
  imports: [BenchmarkFilter],
  templateUrl: './role-benchmarks.html',
  styleUrl: './benchmark-page.scss',
})
export class RoleBenchmarks {
  protected readonly data = signal<RoleBenchmarksBody | null>(null);
  protected readonly loadError = signal<string | null>(null);

  protected readonly tables = computed(() => {
    const d = this.data();
    if (!d) return [];
    return d.roles.map((role) => ({
      role,
      heading: capitalise(role),
      rows: (d.benchmarks[role] ?? []).map((r) => ({
        stat: r.stat,
        values: [r.mean, r.median, r.p25, r.p75, r.p90].map((v) => fixedOrDash(v, 2)),
        n: r.n,
      })),
    }));
  });

  constructor() {
    const players = inject(PlayersService);
    inject(ActivatedRoute)
      .queryParamMap.pipe(
        switchMap((query) => {
          this.data.set(null);
          this.loadError.set(null);
          return players.benchmarks(benchmarkParams(query)).pipe(
            catchError((error: unknown) => {
              this.loadError.set(error instanceof Error ? error.message : String(error));
              return EMPTY;
            }),
          );
        }),
        takeUntilDestroyed(),
      )
      .subscribe((loaded) => this.data.set(loaded));
  }
}
